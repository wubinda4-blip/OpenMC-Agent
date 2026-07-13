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
    """Resolved initial-source box bounds (cm).

    ``strategy`` records which source_strategy produced these bounds.
    ``x_source`` / ``y_source`` / ``z_source`` record the origin of each axis
    (``lattice_footprint``, ``active_fuel``, ``axial_domain``, ``manual``).
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    z_bound_to_active_fuel: bool
    strategy: str = "active_fuel_box"
    x_source: str = "lattice_footprint"
    y_source: str = "lattice_footprint"
    z_source: str = "active_fuel"
    only_fissionable: bool = True
    derived_from_plan: bool = True


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
    """Ids of materials that are fissionable fuel with executable density.

    Recognizes:
    - **mass-density basis** (g/cm3, kg/m3): density_value > 0 + U/Pu nuclide.
    - **atom-density basis** (sum): composition percents are absolute number
      densities; a U/Pu nuclide with percent > 0 is executable fuel.
    - **atom-density basis** (atom/b-cm): density_value > 0 + U/Pu nuclide.
    - Fraction-only basis (ao/wo) without a bulk density is NOT executable fuel.
    """
    ids: set[str] = set()
    _fuel_nuclides = ("U235", "U238", "Pu239", "Pu241")
    for m in model.materials:
        names = _nuclide_names(m)
        if not any(n in names for n in _fuel_nuclides):
            continue
        unit = m.density_unit
        if unit == "sum":
            # Absolute atom densities: percents ARE number densities.
            if any(n.name in _fuel_nuclides and n.percent > 0 for n in m.composition):
                ids.add(m.id)
        elif unit == "atom/b-cm":
            if m.density_value and m.density_value > 0:
                ids.add(m.id)
        elif unit in ("g/cm3", "kg/m3"):
            if m.density_value and m.density_value > 0:
                ids.add(m.id)
    return ids


def fissionable_material_ids(model: ComplexModelSpec) -> set[str]:
    """Ids of materials containing a fissile/fertile nuclide with positive amount."""
    ids: set[str] = set()
    for m in model.materials:
        for n in m.composition:
            if n.name in _FISSIONABLE_NUCLIDES and n.percent > 0:
                ids.add(m.id)
                break
    return ids


def source_bounds_for_plan(
    model: ComplexModelSpec,
    *,
    source_strategy: str | None = None,
    manual_bounds: list[float] | None = None,
) -> SourceBounds | None:
    """Resolve the initial-source box for a plan, respecting source_strategy.

    ``active_fuel_box`` (default): xy = lattice footprint, z = active fuel.
    ``assembly_box``:              xy = lattice footprint, z = full axial domain.
    ``manual``:                    use manual_bounds (caller must supply).
    ``unknown``:                   returns None (caller should produce a blocker).

    When ``source_strategy`` is None, reads it from ``model.settings.source_strategy``
    (defaulting to ``active_fuel_box``).
    """
    if source_strategy is None:
        source_strategy = getattr(
            getattr(model, "settings", None), "source_strategy", "active_fuel_box",
        )
    if manual_bounds is None:
        manual_bounds = getattr(
            getattr(model, "settings", None), "manual_source_bounds_cm", None,
        )

    xy = assembly_xy_bounds(model)
    af = active_fuel_z_bounds(model)

    # -- manual --------------------------------------------------------
    if source_strategy == "manual":
        if manual_bounds is None or len(manual_bounds) != 6:
            return None
        return SourceBounds(
            manual_bounds[0], manual_bounds[1],
            manual_bounds[2], manual_bounds[3],
            manual_bounds[4], manual_bounds[5],
            z_bound_to_active_fuel=False,
            strategy="manual",
            x_source="manual", y_source="manual", z_source="manual",
            derived_from_plan=False,
        )

    # -- unknown -------------------------------------------------------
    if source_strategy == "unknown":
        return None

    # Resolve xy.
    if xy is not None:
        x_min, y_min, x_max, y_max = xy
        xy_src = "lattice_footprint"
    else:
        x_min, y_min, x_max, y_max = -1.0, -1.0, 1.0, 1.0
        xy_src = "fallback_unit"

    # -- assembly_box --------------------------------------------------
    if source_strategy == "assembly_box":
        if model.core is not None and model.core.axial_layers:
            z_mins = [L.z_min_cm for L in model.core.axial_layers]
            z_maxs = [L.z_max_cm for L in model.core.axial_layers]
            z_min, z_max = min(z_mins), max(z_maxs)
        else:
            z_min, z_max = -1.0, 1.0
        return SourceBounds(
            x_min, x_max, y_min, y_max, z_min, z_max,
            z_bound_to_active_fuel=False,
            strategy="assembly_box",
            x_source=xy_src, y_source=xy_src, z_source="axial_domain",
        )

    # -- active_fuel_box (default) ------------------------------------
    if af is not None:
        z_min, z_max = af
        z_src = "active_fuel"
        bound = True
    elif model.core is not None and model.core.axial_layers:
        z_mins = [L.z_min_cm for L in model.core.axial_layers]
        z_maxs = [L.z_max_cm for L in model.core.axial_layers]
        z_min, z_max = min(z_mins), max(z_maxs)
        z_src = "axial_domain"
        bound = False
    else:
        z_min, z_max = -1.0, 1.0
        z_src = "fallback_unit"
        bound = False
    return SourceBounds(
        x_min, x_max, y_min, y_max, z_min, z_max, bound,
        strategy="active_fuel_box",
        x_source=xy_src, y_source=xy_src, z_source=z_src,
    )


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

    strategy = getattr(
        getattr(model, "settings", None), "source_strategy", "active_fuel_box",
    )
    manual_bounds = getattr(
        getattr(model, "settings", None), "manual_source_bounds_cm", None,
    )

    # Strategy-level blockers.
    if strategy == "unknown":
        issues.append(_issue(
            "runtime.unknown_source_strategy",
            "source_strategy is 'unknown'; cannot derive source bounds",
            schema_path="complex_model.settings.source_strategy",
        ))
        return issues

    if strategy == "manual" and not manual_bounds:
        issues.append(_issue(
            "runtime.manual_source_bounds_missing",
            "source_strategy is 'manual' but manual_source_bounds_cm is not set",
            schema_path="complex_model.settings.manual_source_bounds_cm",
        ))
        return issues

    bounds = source_bounds if source_bounds is not None else source_bounds_for_plan(model)
    af = active_fuel_z_bounds(model)

    has_axial_layers = model.core is not None and bool(model.core.axial_layers)

    # Active-fuel region missing entirely.
    if has_axial_layers and af is None and strategy == "active_fuel_box":
        issues.append(_issue(
            "runtime.active_fuel_region_missing",
            "assembly has axial_layers but no lattice-filled active-fuel layer; the "
            "source box cannot be bound to the fissionable region",
            schema_path="complex_model.core.axial_layers",
        ))

    if bounds is None:
        return issues

    # Strategy mismatch: rendered bounds don't match declared strategy.
    if bounds.strategy != strategy:
        issues.append(_issue(
            "runtime.source_strategy_not_rendered",
            f"source_strategy={strategy!r} but bounds were derived with strategy={bounds.strategy!r}",
            schema_path="complex_model.settings.source_strategy",
        ))

    # For active_fuel_box: z must overlap the active fuel region.
    if strategy == "active_fuel_box":
        if has_axial_layers and af is not None and _is_default_unit_z(bounds.z_min, bounds.z_max):
            issues.append(_issue(
                "runtime.source_default_z_extent",
                f"source z-range is the default -1..1 unit slab but the active fuel "
                f"region is {af[0]}~{af[1]} cm; bind the source to the active fuel z-range",
                schema_path="settings.source",
            ))

        if af is not None and not (bounds.z_min < af[1] - _Z_TOL and bounds.z_max > af[0] + _Z_TOL):
            issues.append(_issue(
                "runtime.source_not_in_active_fuel_region",
                f"source z-range {bounds.z_min}~{bounds.z_max} does not overlap the "
                f"active fuel region {af[0]}~{af[1]} cm",
                schema_path="settings.source",
            ))

        if af is not None and not _is_default_unit_z(bounds.z_min, bounds.z_max):
            nonfuel_span = max(0.0, af[0] - bounds.z_min) + max(0.0, bounds.z_max - af[1])
            fuel_height = af[1] - af[0]
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
            "no fuel material containing U235/U238/Pu with a density or atom-density "
            "was found; the source has no fissionable material to sample",
            schema_path="complex_model.materials",
        ))
    else:
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
