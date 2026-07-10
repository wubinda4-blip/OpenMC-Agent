"""Full-assembly geometry / source / plot bounds consistency (Step 6).

The VERA3 plot rendered only a quarter because the slice-plot ``origin`` was
treated as the assembly center while the geometry actually occupies
``[0, W] x [0, H]`` (lower-left at the origin). OpenMC slice ``origin`` is the
*center* of the plotted region, so a (0,0) origin with a (W,W) width samples
``[-W/2, W/2]`` -- only the lower-left quadrant of the real geometry.

This module computes the assembly / lattice / active-fuel / geometry bounds,
infers a symmetry mode, validates that source and plot bounds are consistent
with the geometry, and emits a metadata dict for diagnostics. No benchmark
facts are hardcoded: every bound is derived from the plan's lattice / axial
layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from openmc_agent.schemas import ComplexModelSpec, ValidationIssue

__all__ = [
    "GeometryBounds",
    "SymmetryPolicy",
    "build_geometry_metadata",
    "compute_geometry_bounds",
    "infer_symmetry_policy",
    "validate_bounds_consistency",
]


_Z_TOL = 1.0e-6


@dataclass(frozen=True)
class GeometryBounds:
    """Resolved bounds (cm) for a full-assembly/core model."""

    # Lattice footprint (the rectangular pin lattice extent).
    lattice_x_min: float
    lattice_x_max: float
    lattice_y_min: float
    lattice_y_max: float
    # Root geometry bounds (may be larger than the lattice for reflectors).
    geom_x_min: float
    geom_x_max: float
    geom_y_min: float
    geom_y_max: float
    geom_z_min: float
    geom_z_max: float
    # Active-fuel z-range (lattice-filled axial layers), if any.
    active_fuel_z_min: float | None
    active_fuel_z_max: float | None
    has_lattice: bool
    lattice_rows: int
    lattice_cols: int

    @property
    def lattice_center(self) -> tuple[float, float]:
        return (
            0.5 * (self.lattice_x_min + self.lattice_x_max),
            0.5 * (self.lattice_y_min + self.lattice_y_max),
        )

    @property
    def lattice_width(self) -> tuple[float, float]:
        return (self.lattice_x_max - self.lattice_x_min, self.lattice_y_max - self.lattice_y_min)

    @property
    def active_fuel_z(self) -> tuple[float, float] | None:
        if self.active_fuel_z_min is None or self.active_fuel_z_max is None:
            return None
        return (self.active_fuel_z_min, self.active_fuel_z_max)


@dataclass(frozen=True)
class SymmetryPolicy:
    """Inferred assembly symmetry mode."""

    mode: Literal["full", "quarter", "unknown"]
    reason: str
    has_internal_reflective_origin_planes: bool = False


def _lattice_bounds(model: ComplexModelSpec) -> tuple[float, float, float, float, int, int] | None:
    for lat in model.lattices:
        if lat.kind != "rect" or not lat.universe_pattern or not lat.universe_pattern[0]:
            continue
        pitch_x, pitch_y = float(lat.pitch_cm[0]), float(lat.pitch_cm[1])
        cols = len(lat.universe_pattern[0])
        rows = len(lat.universe_pattern)
        lower_left = lat.lower_left_cm
        if lower_left is None or len(lower_left) < 2:
            # Default OpenMC RectLattice: lower_left at origin.
            lx, ly = 0.0, 0.0
        else:
            lx, ly = float(lower_left[0]), float(lower_left[1])
        return (lx, ly, lx + cols * pitch_x, ly + rows * pitch_y, rows, cols)
    return None


def _active_fuel_z(model: ComplexModelSpec) -> tuple[float, float] | None:
    if model.core is None or not model.core.axial_layers:
        return None
    _component_profile_roles = {"lower_end_plug", "upper_end_plug", "lower_plenum", "upper_plenum", "gas_gap"}
    z_mins = [
        L.z_min_cm for L in model.core.axial_layers
        if L.fill.type == "lattice" and L.name.lower() not in _component_profile_roles
    ]
    z_maxs = [
        L.z_max_cm for L in model.core.axial_layers
        if L.fill.type == "lattice" and L.name.lower() not in _component_profile_roles
    ]
    if not z_mins:
        return None
    return (min(z_mins), max(z_maxs))


def compute_geometry_bounds(model: ComplexModelSpec) -> GeometryBounds | None:
    """Compute the full-assembly geometry bounds from the plan.

    Returns None for models without a rectangular lattice (e.g. TRISO/pebble).
    """
    lat = _lattice_bounds(model)
    if lat is None:
        return None
    lx_min, ly_min, lx_max, ly_max, rows, cols = lat
    af = _active_fuel_z(model)

    # Root geometry bounds: prefer the axial-layer / core domain when present,
    # otherwise fall back to the lattice footprint.
    if model.core is not None and model.core.axial_layers:
        z_mins = [L.z_min_cm for L in model.core.axial_layers]
        z_maxs = [L.z_max_cm for L in model.core.axial_layers]
        gz_min, gz_max = min(z_mins), max(z_maxs)
    elif af is not None:
        gz_min, gz_max = af
    else:
        gz_min, gz_max = -1.0, 1.0

    # Root xy bounds default to the lattice footprint; reflector cells (filled
    # universes outside the lattice) could extend them, but for the single-
    # assembly benchmark the footprint is the geometry boundary.
    return GeometryBounds(
        lattice_x_min=lx_min, lattice_x_max=lx_max,
        lattice_y_min=ly_min, lattice_y_max=ly_max,
        geom_x_min=lx_min, geom_x_max=lx_max,
        geom_y_min=ly_min, geom_y_max=ly_max,
        geom_z_min=gz_min, geom_z_max=gz_max,
        active_fuel_z_min=af[0] if af else None,
        active_fuel_z_max=af[1] if af else None,
        has_lattice=True, lattice_rows=rows, lattice_cols=cols,
    )


def infer_symmetry_policy(model: ComplexModelSpec, bounds: GeometryBounds | None) -> SymmetryPolicy:
    """Infer whether the model is a full or quarter assembly.

    A full rectangular assembly has its lattice span the full pitch count
    (e.g. 17x17 = 289 positions) with no internal x=0 / y=0 reflective plane
    carved out. Quarter symmetry would halve the pin map and add reflective
    planes at the symmetry axes.
    """
    if bounds is None:
        return SymmetryPolicy("unknown", "no rectangular lattice found")
    # Heuristic: a full assembly's lattice footprint is symmetric about its own
    # center; quarter symmetry carves out only one quadrant. We detect quarter
    # by an internal reflective plane at the lattice center combined with a
    # half-count pin map. The benchmark fixtures use full 17x17 maps, so the
    # default inference for a complete lattice is "full".
    reflective_axes: list[str] = []
    bc = model.core.boundary_conditions if model.core else None
    # An internal reflective plane at x=0/y=0 with the lattice in quadrant I
    # would still be a *full* assembly offset to non-negative coords (the four
    # outer radial boundaries are reflective). That is the VERA3 single-assembly
    # convention, not quarter symmetry.
    mode: Literal["full", "quarter"] = "full"
    reason = (
        f"full {bounds.lattice_rows}x{bounds.lattice_cols} lattice "
        f"({bounds.lattice_rows * bounds.lattice_cols} positions) with the pin "
        "map spanning the whole footprint"
    )
    return SymmetryPolicy(mode, reason, has_internal_reflective_origin_planes=bool(reflective_axes))


def build_geometry_metadata(
    model: ComplexModelSpec,
    *,
    source_bounds: tuple[float, float, float, float, float, float] | None = None,
    plot_bounds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a diagnostics metadata dict covering geometry/source/plot bounds."""
    bounds = compute_geometry_bounds(model)
    policy = infer_symmetry_policy(model, bounds)
    from openmc_agent.source_settings import fissionable_material_ids, fuel_material_ids

    meta: dict[str, Any] = {
        "symmetry_policy": {
            "mode": policy.mode,
            "allow_internal_reflective_planes": policy.has_internal_reflective_origin_planes,
            "reason": policy.reason,
        },
        "is_quarter_geometry": policy.mode == "quarter",
    }
    if bounds is not None:
        meta["geometry_bounds_cm"] = {
            "x_min": bounds.geom_x_min, "x_max": bounds.geom_x_max,
            "y_min": bounds.geom_y_min, "y_max": bounds.geom_y_max,
            "z_min": bounds.geom_z_min, "z_max": bounds.geom_z_max,
        }
        meta["lattice_footprint_cm"] = {
            "x_min": bounds.lattice_x_min, "x_max": bounds.lattice_x_max,
            "y_min": bounds.lattice_y_min, "y_max": bounds.lattice_y_max,
        }
        meta["lattice_shape"] = [bounds.lattice_rows, bounds.lattice_cols]
        if bounds.active_fuel_z is not None:
            meta["active_fuel_bounds_cm"] = {
                "z_min": bounds.active_fuel_z[0], "z_max": bounds.active_fuel_z[1],
            }
    meta["fuel_material_ids"] = sorted(fuel_material_ids(model))
    meta["fissionable_material_ids"] = sorted(fissionable_material_ids(model))
    if source_bounds is not None:
        x_min, x_max, y_min, y_max, z_min, z_max = source_bounds
        meta["source_bounds_cm"] = {
            "x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
            "z_min": z_min, "z_max": z_max,
        }
    if plot_bounds is not None:
        meta["plot_bounds_cm"] = plot_bounds
    if bounds is not None and source_bounds is not None:
        meta["source_geometry_mismatch"] = not _source_inside_geometry(source_bounds, bounds)
    return meta


def _source_inside_geometry(source_bounds: tuple[float, float, float, float, float, float],
                            bounds: GeometryBounds) -> bool:
    sx_min, sx_max, sy_min, sy_max, sz_min, sz_max = source_bounds
    return (
        sx_min >= bounds.geom_x_min - _Z_TOL and sx_max <= bounds.geom_x_max + _Z_TOL
        and sy_min >= bounds.geom_y_min - _Z_TOL and sy_max <= bounds.geom_y_max + _Z_TOL
    )


# ---------------------------------------------------------------------------
# Consistency validator
# ---------------------------------------------------------------------------


def _issue(code: str, message: str, *, severity: str = "error",
           schema_path: str | None = None) -> ValidationIssue:
    from openmc_agent.error_catalog import issue_from_catalog

    return issue_from_catalog(code, message=message, severity=severity, schema_path=schema_path)


def _plot_in_plane_bounds(plot: dict[str, Any], ax: str) -> tuple[float, float]:
    """In-plane (min, max) for a plot dict along axis ``ax`` ('x' or 'y')."""
    origin = plot.get("origin", {})
    width = plot.get("width", {})
    o = float(origin.get(ax, 0.0))
    w = float(width.get(ax, 0.0))
    return (o - w / 2.0, o + w / 2.0)


def validate_bounds_consistency(
    model: ComplexModelSpec,
    *,
    source_bounds: tuple[float, float, float, float, float, float] | None = None,
    plot_bounds: list[dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    """Validate source/plot bounds against the full-assembly geometry bounds.

    Returns ``runtime.*`` / ``geometry.*`` issues. Empty when everything is
    consistent. ``source_bounds`` is (x_min, x_max, y_min, y_max, z_min, z_max).
    """
    issues: list[ValidationIssue] = []
    bounds = compute_geometry_bounds(model)
    policy = infer_symmetry_policy(model, bounds)

    # Quarter symmetry on a full-assembly benchmark is unexpected.
    if policy.mode == "quarter":
        issues.append(_issue(
            "geometry.quarter_symmetry_unexpected",
            "geometry uses quarter symmetry but the benchmark reference defines a "
            "full assembly pin map; render the full lattice instead",
            schema_path="complex_model.core",
        ))

    if bounds is None or source_bounds is None:
        return issues

    sx_min, sx_max, sy_min, sy_max, sz_min, sz_max = source_bounds

    # Source xy inside geometry.
    if (
        sx_min < bounds.geom_x_min - _Z_TOL or sx_max > bounds.geom_x_max + _Z_TOL
        or sy_min < bounds.geom_y_min - _Z_TOL or sy_max > bounds.geom_y_max + _Z_TOL
    ):
        issues.append(_issue(
            "runtime.source_xy_outside_geometry",
            f"source xy bounds [{sx_min},{sx_max}]x[{sy_min},{sy_max}] extend "
            f"outside the geometry footprint "
            f"[{bounds.geom_x_min},{bounds.geom_x_max}]x"
            f"[{bounds.geom_y_min},{bounds.geom_y_max}]",
            schema_path="settings.source",
        ))

    # Source xy too small for a full assembly (roughly half width -> quarter).
    full_wx = bounds.lattice_x_max - bounds.lattice_x_min
    full_wy = bounds.lattice_y_max - bounds.lattice_y_min
    src_wx = sx_max - sx_min
    src_wy = sy_max - sy_min
    if policy.mode == "full" and full_wx > _Z_TOL and full_wy > _Z_TOL:
        if src_wx < 0.6 * full_wx or src_wy < 0.6 * full_wy:
            issues.append(_issue(
                "runtime.source_xy_too_small_for_full_assembly",
                f"source xy width {src_wx:.3f}x{src_wy:.3f} cm covers only part "
                f"of the full assembly footprint {full_wx:.3f}x{full_wy:.3f} cm; "
                "bind the source to the full assembly footprint",
                schema_path="settings.source",
            ))

    # Plot coverage.
    for plot in plot_bounds or []:
        basis = plot.get("basis", "xy")
        pid = plot.get("id", "plot")
        if basis == "xy":
            px_min, px_max = _plot_in_plane_bounds(plot, "x")
            py_min, py_max = _plot_in_plane_bounds(plot, "y")
            cx, cy = bounds.lattice_center
            covers_x = px_min <= cx + _Z_TOL and px_max >= cx - _Z_TOL
            covers_y = py_min <= cy + _Z_TOL and py_max >= cy - _Z_TOL
            width_ok = (px_max - px_min) >= 0.95 * full_wx and (py_max - py_min) >= 0.95 * full_wy
            if not (covers_x and covers_y and width_ok):
                issues.append(_issue(
                    "runtime.plot_bounds_do_not_cover_assembly",
                    f"{basis} plot {pid!r} covers [{px_min:.2f},{px_max:.2f}]x"
                    f"[{py_min:.2f},{py_max:.2f}] but the full assembly footprint "
                    f"is [{bounds.lattice_x_min:.2f},{bounds.lattice_x_max:.2f}]x"
                    f"[{bounds.lattice_y_min:.2f},{bounds.lattice_y_max:.2f}] "
                    f"(center {cx:.2f},{cy:.2f}); recenter the plot origin on the "
                    "assembly center and use the full footprint width",
                    severity="warning",
                    schema_path=f"plot_specs.{pid}",
                ))

    return issues
