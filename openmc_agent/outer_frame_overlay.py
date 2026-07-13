"""Reactor-neutral mass-conserving outer-frame overlay planner.

For each pitch cell in a spacer-grid overlay, computes a thin square frame
of grid alloy whose cross-sectional area satisfies total-mass conservation.
The frame occupies only the outer ring of each pitch cell; fuel, cladding,
tubes, and inserts continue through the inner square untouched.

This module is intentionally free of any reactor-specific constants or
hardcoded VERA3 numbers.  All physical values are passed in by the caller
from the authoritative input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Clearance tolerance
# ---------------------------------------------------------------------------

#: Minimum clearance (cm) between the inner frame boundary and the outermost
#: solid surface.  Set to 1 nm to catch only true geometric overlaps while
#: allowing mathematically positive clearances as small as a few microns
#: (which arise naturally from mass conservation).
CLEARANCE_TOLERANCE_CM: float = 1e-7


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClearanceResult:
    """Clearance outcome for a single universe."""

    universe_id: str
    max_solid_radius_cm: float
    inner_half_width_cm: float
    clearance_cm: float
    blocked: bool
    detail: str


@dataclass(frozen=True)
class OuterFrameGeometryPlan:
    """Complete geometry plan for a mass-conserving outer-frame overlay.

    All derived values (area, thickness, reconstructed mass) are computed
    deterministically from the source facts (total_mass, density, height,
    cell_count, pitch).
    """

    overlay_id: str
    target_lattice_id: str
    material_id: str
    z_min_cm: float
    z_max_cm: float
    grid_height_cm: float

    # Source facts
    total_mass_g: float
    material_density_g_cm3: float
    lattice_cell_count: int
    pitch_x_cm: float
    pitch_y_cm: float

    # Derived values
    mass_per_cell_g: float
    frame_area_cm2: float
    inner_side_x_cm: float
    inner_side_y_cm: float
    frame_thickness_x_cm: float
    frame_thickness_y_cm: float
    inner_half_width_x_cm: float
    inner_half_width_y_cm: float

    # Verification
    reconstructed_total_mass_g: float
    relative_mass_error: float

    # Clearance
    clearance_results: list[ClearanceResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OuterFrameError(ValueError):
    """Raised when the outer-frame plan cannot be computed (invalid inputs)."""


class OuterFrameClearanceError(OuterFrameError):
    """Raised when the frame would overlap a protected solid."""

    def __init__(self, blocked: list[ClearanceResult]) -> None:
        self.blocked_results = blocked
        msgs = "; ".join(r.detail for r in blocked)
        super().__init__(f"Clearance check failed for {len(blocked)} universe(s): {msgs}")


# ---------------------------------------------------------------------------
# Universe max-solid-extent computation (reactor-neutral)
# ---------------------------------------------------------------------------


def compute_universe_max_solid_extent(
    universe_id: str,
    cell_ids: list[str],
    cells_by_id: dict[str, Any],
    regions_by_id: dict[str, Any],
    surfaces_by_id: dict[str, Any],
    materials_by_id: dict[str, Any] | None = None,
    is_open_cell_fn: Any = None,
) -> float:
    """Return the maximum solid radius (cm) across all non-open cells in a
    universe.

    The function inspects every cell in the universe, resolves its region's
    surfaces, and returns the largest ``r`` parameter of any ``zcylinder``
    surface belonging to a **non-open** (protected) cell.  Open/moderator
    cells are skipped so that the moderator background radius does not
    inflate the extent.

    Parameters
    ----------
    universe_id
        Universe identifier (for diagnostics).
    cell_ids
        Cell ids belonging to the universe.
    cells_by_id
        Mapping ``cell_id -> cell spec`` with at least ``region_id``,
        ``fill_type``, ``fill_id``.
    regions_by_id
        Mapping ``region_id -> region spec`` with ``surface_ids``.
    surfaces_by_id
        Mapping ``surface_id -> surface spec`` with ``kind`` and
        ``parameters``.
    materials_by_id
        Optional material lookup; when provided together with
        *is_open_cell_fn*, cells classified as open are excluded.
    is_open_cell_fn
        Optional callable ``(cell, materials_by_id) -> bool`` that returns
        True for open/moderator cells.

    Returns
    -------
    float
        Maximum solid radius in cm, or 0.0 when no solid cylinders exist.
    """
    max_r = 0.0
    for cid in cell_ids:
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        # Skip open/moderator cells when classification is available.
        if materials_by_id is not None and is_open_cell_fn is not None:
            try:
                if is_open_cell_fn(cell, materials_by_id):
                    continue
            except Exception:
                pass
        region_id = getattr(cell, "region_id", None)
        if region_id is None:
            continue
        region = regions_by_id.get(region_id)
        if region is None:
            continue
        for surf_id in getattr(region, "surface_ids", []):
            surf = surfaces_by_id.get(surf_id)
            if surf is None:
                continue
            if getattr(surf, "kind", None) == "zcylinder":
                r = surf.parameters.get("r")
                if r is not None and r > max_r:
                    max_r = float(r)
    return max_r


def collect_lattice_universe_extents(
    lattice: Any,
    cells_by_id: dict[str, Any],
    regions_by_id: dict[str, Any],
    surfaces_by_id: dict[str, Any],
    universes_by_id: dict[str, Any],
    materials_by_id: dict[str, Any] | None = None,
    is_open_cell_fn: Any = None,
) -> dict[str, float]:
    """Return ``{universe_id: max_solid_radius_cm}`` for every unique
    universe that appears in *lattice*'s ``universe_pattern``.

    Recursively resolves nested universes via ``fill_type='universe'`` cells.
    """
    seen: set[str] = set()
    result: dict[str, float] = {}

    for row in lattice.universe_pattern:
        for uid in row:
            if uid in seen:
                continue
            seen.add(uid)
            univ = universes_by_id.get(uid)
            if univ is None:
                result[uid] = 0.0
                continue
            max_r = compute_universe_max_solid_extent(
                uid,
                univ.cell_ids,
                cells_by_id,
                regions_by_id,
                surfaces_by_id,
                materials_by_id=materials_by_id,
                is_open_cell_fn=is_open_cell_fn,
            )
            result[uid] = max_r
    return result


# ---------------------------------------------------------------------------
# Core planner
# ---------------------------------------------------------------------------


def derive_mass_conserving_outer_frame(
    *,
    overlay_id: str,
    target_lattice_id: str,
    material_id: str,
    z_min_cm: float,
    z_max_cm: float,
    total_mass_g: float,
    material_density_g_cm3: float,
    lattice_cell_count: int,
    pitch_x_cm: float,
    pitch_y_cm: float,
    universe_max_extents: dict[str, float] | None = None,
    mass_tolerance_rel: float = 1e-6,
    clearance_tolerance_cm: float = CLEARANCE_TOLERANCE_CM,
) -> OuterFrameGeometryPlan:
    """Compute a mass-conserving outer-frame geometry plan.

    The frame occupies the outer ring of each pitch cell such that the
    total grid mass is exactly conserved:

        mass_per_cell = total_mass / cell_count
        A_frame       = mass_per_cell / (density * height)
        inner_side    = sqrt(pitch^2 - A_frame)
        thickness     = (pitch - inner_side) / 2

    Parameters
    ----------
    universe_max_extents
        Mapping ``{universe_id: max_solid_radius_cm}``.  When provided, a
        clearance check is performed: the inner half-width must exceed
        every universe's max solid radius.  Insufficient clearance raises
        :class:`OuterFrameClearanceError`.

    Raises
    ------
    OuterFrameError
        If any input is invalid (non-positive mass/density/count, z-range
        inversion, impossible area, mass-conservation failure, non-square
        pitch).
    OuterFrameClearanceError
        If the frame would overlap a protected solid in any universe.

    Returns
    -------
    OuterFrameGeometryPlan
    """
    # --- validate source facts ---
    if total_mass_g <= 0:
        raise OuterFrameError(f"total_mass_g must be positive, got {total_mass_g}")
    if material_density_g_cm3 <= 0:
        raise OuterFrameError(
            f"material_density_g_cm3 must be positive, got {material_density_g_cm3}"
        )
    if lattice_cell_count <= 0:
        raise OuterFrameError(
            f"lattice_cell_count must be positive, got {lattice_cell_count}"
        )
    if z_min_cm >= z_max_cm:
        raise OuterFrameError(
            f"z_min_cm ({z_min_cm}) must be < z_max_cm ({z_max_cm})"
        )
    if pitch_x_cm <= 0 or pitch_y_cm <= 0:
        raise OuterFrameError(
            f"pitch must be positive, got ({pitch_x_cm}, {pitch_y_cm})"
        )

    grid_height_cm = z_max_cm - z_min_cm

    # Only square pitch is supported (the outer frame is a square ring).
    if abs(pitch_x_cm - pitch_y_cm) > 1e-12:
        raise OuterFrameError(
            f"Non-square pitch ({pitch_x_cm} x {pitch_y_cm}) is not supported "
            f"for mass_conserving_outer_frame; square pitch required."
        )

    pitch = pitch_x_cm
    pitch_area = pitch * pitch

    # --- derive frame geometry ---
    mass_per_cell_g = total_mass_g / lattice_cell_count
    frame_area_cm2 = mass_per_cell_g / (material_density_g_cm3 * grid_height_cm)

    if frame_area_cm2 >= pitch_area:
        raise OuterFrameError(
            f"frame_area ({frame_area_cm2:.6f} cm^2) >= pitch_area ({pitch_area:.6f} cm^2); "
            f"grid mass {total_mass_g}g is too large for {lattice_cell_count} cells at "
            f"pitch {pitch}cm, density {material_density_g_cm3}g/cm3, height {grid_height_cm}cm."
        )

    inner_side = math.sqrt(pitch_area - frame_area_cm2)
    if inner_side <= 0:
        raise OuterFrameError(f"inner_side must be positive, got {inner_side}")

    frame_thickness = (pitch - inner_side) / 2.0
    inner_half_width = inner_side / 2.0

    # --- verify mass conservation by independent reconstruction ---
    reconstructed_area = pitch_area - inner_side * inner_side
    reconstructed_mass_per_cell = (
        reconstructed_area * material_density_g_cm3 * grid_height_cm
    )
    reconstructed_total_mass = reconstructed_mass_per_cell * lattice_cell_count
    if total_mass_g > 0:
        relative_mass_error = abs(reconstructed_total_mass - total_mass_g) / total_mass_g
    else:
        relative_mass_error = 0.0

    if relative_mass_error > mass_tolerance_rel:
        raise OuterFrameError(
            f"Mass conservation check failed: relative error {relative_mass_error:.2e} "
            f"exceeds tolerance {mass_tolerance_rel:.2e} "
            f"(expected {total_mass_g}g, reconstructed {reconstructed_total_mass:.6f}g)"
        )

    # --- clearance check ---
    clearance_results: list[ClearanceResult] = []
    if universe_max_extents:
        blocked: list[ClearanceResult] = []
        for uid, max_radius in sorted(universe_max_extents.items()):
            clearance = inner_half_width - max_radius
            is_blocked = clearance < clearance_tolerance_cm
            cr = ClearanceResult(
                universe_id=uid,
                max_solid_radius_cm=max_radius,
                inner_half_width_cm=inner_half_width,
                clearance_cm=clearance,
                blocked=is_blocked,
                detail=(
                    f"universe {uid}: max_solid_radius={max_radius:.6f} cm, "
                    f"inner_half_width={inner_half_width:.6f} cm, "
                    f"clearance={clearance:.6f} cm"
                    + (" — BLOCKED" if is_blocked else " — OK")
                ),
            )
            clearance_results.append(cr)
            if is_blocked:
                blocked.append(cr)
        if blocked:
            raise OuterFrameClearanceError(blocked)

    return OuterFrameGeometryPlan(
        overlay_id=overlay_id,
        target_lattice_id=target_lattice_id,
        material_id=material_id,
        z_min_cm=z_min_cm,
        z_max_cm=z_max_cm,
        grid_height_cm=grid_height_cm,
        total_mass_g=total_mass_g,
        material_density_g_cm3=material_density_g_cm3,
        lattice_cell_count=lattice_cell_count,
        pitch_x_cm=pitch_x_cm,
        pitch_y_cm=pitch_y_cm,
        mass_per_cell_g=mass_per_cell_g,
        frame_area_cm2=frame_area_cm2,
        inner_side_x_cm=inner_side,
        inner_side_y_cm=inner_side,
        frame_thickness_x_cm=frame_thickness,
        frame_thickness_y_cm=frame_thickness,
        inner_half_width_x_cm=inner_half_width,
        inner_half_width_y_cm=inner_half_width,
        reconstructed_total_mass_g=reconstructed_total_mass,
        relative_mass_error=relative_mass_error,
        clearance_results=clearance_results,
    )


def plan_to_dict(plan: OuterFrameGeometryPlan) -> dict[str, Any]:
    """Serialize a plan to a plain dict for JSON artifacts."""
    return {
        "overlay_id": plan.overlay_id,
        "target_lattice_id": plan.target_lattice_id,
        "material_id": plan.material_id,
        "z_min_cm": plan.z_min_cm,
        "z_max_cm": plan.z_max_cm,
        "grid_height_cm": plan.grid_height_cm,
        "total_mass_g": plan.total_mass_g,
        "material_density_g_cm3": plan.material_density_g_cm3,
        "lattice_cell_count": plan.lattice_cell_count,
        "pitch_x_cm": plan.pitch_x_cm,
        "pitch_y_cm": plan.pitch_y_cm,
        "mass_per_cell_g": plan.mass_per_cell_g,
        "frame_area_cm2": plan.frame_area_cm2,
        "inner_side_x_cm": plan.inner_side_x_cm,
        "inner_side_y_cm": plan.inner_side_y_cm,
        "frame_thickness_x_cm": plan.frame_thickness_x_cm,
        "frame_thickness_y_cm": plan.frame_thickness_y_cm,
        "inner_half_width_x_cm": plan.inner_half_width_x_cm,
        "inner_half_width_y_cm": plan.inner_half_width_y_cm,
        "reconstructed_total_mass_g": plan.reconstructed_total_mass_g,
        "relative_mass_error": plan.relative_mass_error,
        "clearance_results": [
            {
                "universe_id": cr.universe_id,
                "max_solid_radius_cm": cr.max_solid_radius_cm,
                "inner_half_width_cm": cr.inner_half_width_cm,
                "clearance_cm": cr.clearance_cm,
                "blocked": cr.blocked,
            }
            for cr in plan.clearance_results
        ],
    }
