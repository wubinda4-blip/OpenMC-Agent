"""OpenMC source / settings validation and active-fuel source-box binding.

Binding the initial source to the *full* model axial domain (nozzles, plena,
reflectors, moderator buffers) with ``only_fissionable=True`` rejects most
source sites because the fissionable fuel only occupies the active-fuel
z-range (a 'too few source sites' crash).

This module computes the active-fuel z-range from the plan's axial layers, binds
the source box to it, and validates the source settings before OpenMC runs --
turning a raw crash into a structured ``runtime.*`` issue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from openmc_agent.schemas import ComplexMaterialSpec, ComplexModelSpec, SimulationPlan, ValidationIssue

__all__ = [
    "SourceBounds",
    "active_fuel_z_bounds",
    "assembly_xy_bounds",
    "fissionable_material_ids",
    "fuel_material_ids",
    "source_bounds_for_plan",
    "validate_source_settings",
]


# Tolerance for z-range comparisons (cm).
_Z_TOL = 1.0e-6

# Nuclides that make a material fissionable (fissile or fertile).
_FISSIONABLE_NUCLIDES = {
    "U232", "U233", "U234", "U235", "U236", "U238",
    "Pu238", "Pu239", "Pu240", "Pu241", "Pu242",
    "Th232",
}

# Alloy name tokens that must NOT be reduced to a single pure element.
_ALLOY_TOKENS: tuple[tuple[str, str, str], ...] = (
    # (name_token, expected_base_element, pure_element_short)
    ("zircaloy", "Zr", "Zr"),
    ("zr4", "Zr", "Zr"),
    ("ss304", "Fe", "Fe"),
    ("ss-304", "Fe", "Fe"),
    ("stainless", "Fe", "Fe"),
    ("inconel", "Ni", "Ni"),
    ("a286", "Fe", "Fe"),
)


@dataclass(frozen=True)
class SourceBounds:
    """Resolved initial-source box bounds (cm)."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    z_bound_to_active_fuel: bool


def _layer_is_active_fuel(layer: Any) -> bool:
    """A layer that contributes the pin lattice with fissionable fuel.

    Component-profile layers (end-plug, plenum, gas gap) are lattice-filled
    but their fuel-pin internals are replaced by non-fissionable material
    (Zircaloy, helium). They are excluded so the neutron source overlaps
    only the actual active fuel region.
    """
    fill = getattr(layer, "fill", None)
    if fill is None or getattr(fill, "type", None) != "lattice":
        return False
    name = (getattr(layer, "name", "") or "").lower()
    _component_profile_roles = {
        "lower_end_plug", "upper_end_plug", "lower_plenum", "upper_plenum",
        "gas_gap", "shoulder_gap", "lower_shoulder_gap", "upper_shoulder_gap",
    }
    return name not in _component_profile_roles


def active_fuel_z_bounds(model: ComplexModelSpec) -> tuple[float, float] | None:
    """z-range spanning the lattice-filled axial layers (active fuel region).

    Returns None when the plan has no identifiable lattice-filled axial layer.
    """
    if model.core is None or not model.core.axial_layers:
        return None
    z_mins = [L.z_min_cm for L in model.core.axial_layers if _layer_is_active_fuel(L)]
    z_maxs = [L.z_max_cm for L in model.core.axial_layers if _layer_is_active_fuel(L)]
    if not z_mins:
        return None
    return (min(z_mins), max(z_maxs))


def _lattice_footprint(model: ComplexModelSpec) -> tuple[float, float, float, float] | None:
    """(x_min, y_min, x_max, y_max) of the first rect lattice, if any."""
    for lat in model.lattices:
        if lat.kind != "rect":
            continue
        pitch_x, pitch_y = float(lat.pitch_cm[0]), float(lat.pitch_cm[1])
        if lat.universe_pattern:
            rows = len(lat.universe_pattern)
            cols = len(lat.universe_pattern[0])
        else:
            continue
        lower_left = lat.lower_left_cm
        if lower_left is None or len(lower_left) < 2:
            continue
        lx, ly = float(lower_left[0]), float(lower_left[1])
        return (lx, ly, lx + cols * pitch_x, ly + rows * pitch_y)
    return None


def assembly_xy_bounds(model: ComplexModelSpec) -> tuple[float, float, float, float] | None:
    """x/y bounds for the source box: the full lattice/assembly footprint.

    Uses the shared geometry-bounds computation so a lattice whose lower_left
    sits at the origin (the VERA3 non-negative convention) still resolves to its
    full footprint rather than being treated as missing.
    """
    from openmc_agent.geometry_bounds import compute_geometry_bounds

    bounds = compute_geometry_bounds(model)
    if bounds is None:
        return None
    return (bounds.lattice_x_min, bounds.lattice_y_min,
            bounds.lattice_x_max, bounds.lattice_y_max)


def _cell_fuel_material_ids(model: ComplexModelSpec) -> set[str]:
    """Material ids referenced by material-filled cells (candidate fuel/clad)."""
    return {c.fill_id for c in model.cells if c.fill_type == "material" and c.fill_id}


def _nuclide_names(material: ComplexMaterialSpec) -> set[str]:
    return {n.name for n in material.composition}


def fuel_material_ids(model: ComplexModelSpec) -> set[str]:
    """Ids of materials that look like fuel (U/Pu + density present)."""
    ids: set[str] = set()
    for m in model.materials:
        names = _nuclide_names(m)
        if any(nuclide in names for nuclide in ("U235", "U238", "Pu239", "Pu241")) and m.density_value:
            ids.add(m.id)
    return ids


def fissionable_material_ids(model: ComplexModelSpec) -> set[str]:
    """Ids of materials containing a fissile/fertile nuclide."""
    ids: set[str] = set()
    for m in model.materials:
        if _nuclide_names(m) & _FISSIONABLE_NUCLIDES:
            ids.add(m.id)
    return ids


def source_bounds_for_plan(model: ComplexModelSpec) -> SourceBounds | None:
    """Resolve the recommended initial-source box for a plan.

    z is bound to the active-fuel region when one exists (the physically correct
    choice and the fix for the source-rejection crash). x/y use the lattice
    footprint. Returns None when no geometry is available to anchor the source.
    """
    xy = assembly_xy_bounds(model)
    z = active_fuel_z_bounds(model)
    if xy is None and z is None:
        return None
    # Fall back to permissive bounds when one axis family is missing.
    x_min, y_min, x_max, y_max = xy if xy is not None else (-1.0, -1.0, 1.0, 1.0)
    if z is not None:
        z_min, z_max = z
        bound = True
    else:
        # No lattice layer -> default unit slab (2D assembly); correct there.
        if model.core is not None and model.core.axial_layers:
            z_mins = [L.z_min_cm for L in model.core.axial_layers]
            z_maxs = [L.z_max_cm for L in model.core.axial_layers]
            z_min, z_max = min(z_mins), max(z_maxs)
        else:
            z_min, z_max = -1.0, 1.0
        bound = False
    return SourceBounds(x_min, x_max, y_min, y_max, z_min, z_max, bound)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _is_default_unit_z(z_min: float, z_max: float) -> bool:
    return abs(z_min - (-1.0)) < _Z_TOL and abs(z_max - 1.0) < _Z_TOL


def _issue(code: str, message: str, *, severity: str = "error", schema_path: str | None = None) -> ValidationIssue:
    from openmc_agent.error_catalog import issue_from_catalog

    return issue_from_catalog(code, message=message, severity=severity, schema_path=schema_path)


def validate_source_settings(
    plan: SimulationPlan,
    *,
    source_bounds: SourceBounds | None = None,
) -> list[ValidationIssue]:
    """Pre-flight source/settings checks before OpenMC runs.

    Returns structured ``runtime.*`` issues (empty = source is sound). When the
    plan has a real active-fuel region, the source z-range must overlap it and
    must not be the default z=-1..1 unit slab.
    """
    issues: list[ValidationIssue] = []
    model = plan.complex_model
    if model is None:
        return issues

    bounds = source_bounds if source_bounds is not None else source_bounds_for_plan(model)
    af = active_fuel_z_bounds(model)

    has_axial_layers = model.core is not None and bool(model.core.axial_layers)

    # Active-fuel region missing entirely.
    if has_axial_layers and af is None:
        issues.append(_issue(
            "runtime.active_fuel_region_missing",
            "assembly has axial_layers but no lattice-filled active-fuel layer; the "
            "source box cannot be bound to the fissionable region",
            schema_path="complex_model.core.axial_layers",
        ))

    if bounds is None:
        return issues

    # Default z=-1..1 on a 3D axial plan.
    if has_axial_layers and af is not None and _is_default_unit_z(bounds.z_min, bounds.z_max):
        issues.append(_issue(
            "runtime.source_default_z_extent",
            f"source z-range is the default -1..1 unit slab but the active fuel "
            f"region is {af[0]}~{af[1]} cm; bind the source to the active fuel z-range",
            schema_path="settings.source",
        ))

    # Source does not overlap the active fuel region.
    if af is not None and not (bounds.z_min < af[1] - _Z_TOL and bounds.z_max > af[0] + _Z_TOL):
        issues.append(_issue(
            "runtime.source_not_in_active_fuel_region",
            f"source z-range {bounds.z_min}~{bounds.z_max} does not overlap the "
            f"active fuel region {af[0]}~{af[1]} cm",
            schema_path="settings.source",
        ))

    # Source covers large nonfuel axial regions when active fuel is known.
    if af is not None and not _is_default_unit_z(bounds.z_min, bounds.z_max):
        nonfuel_span = max(0.0, af[0] - bounds.z_min) + max(0.0, bounds.z_max - af[1])
        fuel_height = af[1] - af[0]
        # Warn when the source extends beyond the fuel by more than 10% of the
        # fuel height (covers nozzles/plena/buffers with only_fissionable=True).
        if fuel_height > _Z_TOL and nonfuel_span > 0.1 * fuel_height:
            issues.append(_issue(
                "runtime.source_covers_nonfuel_axial_regions",
                f"source z-range {bounds.z_min}~{bounds.z_max} extends "
                f"{nonfuel_span:.1f} cm beyond the active fuel region "
                f"{af[0]}~{af[1]} cm; with only_fissionable=True this triggers "
                f"source rejection. Bind the source to the active fuel z-range.",
                severity="warning",
                schema_path="settings.source",
            ))

    # Fuel material fissionability.
    fmat = fuel_material_ids(model)
    if not fmat:
        issues.append(_issue(
            "runtime.fuel_material_not_fissionable",
            "no fuel material containing U235/U238/Pu with a density was found; the "
            "source has no fissionable material to sample",
            schema_path="complex_model.materials",
        ))
    else:
        # Fuel material must be reachable from a material-filled cell.
        referenced = _cell_fuel_material_ids(model)
        if not (fmat & referenced):
            issues.append(_issue(
                "runtime.active_fuel_geometry_missing",
                f"fuel material(s) {sorted(fmat)!r} are defined but not referenced by "
                f"any material-filled cell; the active fuel is not in the geometry",
                schema_path="complex_model.cells",
            ))

    return issues


# ---------------------------------------------------------------------------
# Alloy / guide-tube material guards (benchmark-fidelity, non-fabricating)
# ---------------------------------------------------------------------------


def alloy_pure_element_issues(model: ComplexModelSpec) -> list[ValidationIssue]:
    """Flag structural alloys reduced to a single pure element.

    Zircaloy-4 is not pure Zr, SS304 is not pure Fe, Inconel-718 is not pure Ni.
    Reducing them loses real absorption (Sn, Cr, Ni, Nb, Mo, ...). This only
    warns and asks for confirmation -- it never invents a composition.
    """
    issues: list[ValidationIssue] = []
    for m in model.materials:
        text = f"{m.id} {m.name}".lower()
        for token, _base, pure in _ALLOY_TOKENS:
            if token not in text:
                continue
            names = _nuclide_names(m)
            # Single nuclide that is just the base element -> pure-element reduction.
            non_trivial = {n for n in names if n != pure and n != "He4"}
            if len(names) <= 1 or not non_trivial:
                issues.append(_issue(
                    "materials.alloy_reduced_to_pure_element",
                    f"material {m.id!r} ({m.name}) is modelled as a single pure "
                    f"element ({sorted(names) or 'none'}); {m.name} is an alloy and "
                    f"should keep its minor constituents (Sn/Cr/Ni/Nb/Mo/...). Provide "
                    f"the alloy composition or mark requires_human_confirmation.",
                    severity="warning",
                    schema_path=f"complex_model.materials.{m.id}",
                ))
            break
    return issues
