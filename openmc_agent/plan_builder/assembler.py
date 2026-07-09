"""Deterministic assembler for incremental plan building (Phase 3).

Reads validated patches (facts, materials, universes, pin_map, axial_layers,
axial_overlays, settings) and assembles them into a complete
:class:`~openmc_agent.schemas.SimulationPlan` — **without** calling an LLM,
OpenMC, or any renderer.

The assembler is the bridge between the future LLM patch generator (Phase 4+)
and the existing SimulationPlan validators / renderers.  It takes small,
independently-validatable patches and deterministically produces the same IR
that the monolithic path produces — but without the 25 KB JSON bottleneck.

Key responsibilities
--------------------
* **Pin map expansion**: expand special-pin coordinates into a full
  ``nx × ny`` ``universe_pattern``, so the LLM never emits 289 entries.
* **Material adaptation**: convert ``MaterialSpecPatch`` →
  ``ComplexMaterialSpec`` with ``NuclideSpec`` composition.
* **Universe / cell adaptation**: convert patch universes → plan universes.
* **Axial layer / overlay assembly**: map patch items → ``AxialLayerSpec`` /
  ``AxialOverlaySpec``.
* **Reference resolution**: check that all material / universe / lattice ids
  referenced by the patches actually exist.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import (
    AgentBaseModel,
    AssemblySpec,
    AxialLayerSpec,
    AxialOverlaySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    FillRefSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RegionSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
)

from .material_resolution import resolve_material_id
from ..material_policy import (
    DEFAULT_MATERIAL_POLICY,
    MaterialCompositionPolicy,
    MaterialCompositionReport,
    apply_policy_to_material_patch,
    build_composition_report,
    policy_from_value,
)
from .patches import (
    AxialLayerPatchItem,
    AxialLayersPatch,
    AxialOverlayPatchItem,
    AxialOverlaysPatch,
    CellLayerPatch,
    CoordinateConvention,
    FactsPatch,
    MaterialSpecPatch,
    MaterialsPatch,
    PinMapPatch,
    SettingsPatch,
    UniverseSpecPatch,
    UniversesPatch,
    normalized_coords,
)
from .pin_counts import compute_pin_role_counts


# ---------------------------------------------------------------------------
# Assembly issue / result models
# ---------------------------------------------------------------------------


class PlanAssemblyIssue(AgentBaseModel):
    """A single issue encountered during plan assembly."""

    code: str
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    path: str | None = None
    expected: Any | None = None
    actual: Any | None = None


class PlanAssemblyResult(AgentBaseModel):
    """Result of assembling patches into a SimulationPlan."""

    ok: bool = True
    plan: SimulationPlan | None = None
    plan_dict: dict[str, Any] | None = None
    issues: list[PlanAssemblyIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    material_composition_report: MaterialCompositionReport | None = None


# ---------------------------------------------------------------------------
# Pin map expansion
# ---------------------------------------------------------------------------

# PinMapPatch coordinate groups → universe kind.
_COORD_GROUP_TO_KIND: dict[str, str] = {
    "guide_tube_coords": "guide_tube",
    "instrument_tube_coords": "instrument_tube",
    "pyrex_rod_coords": "pyrex_rod",
    "thimble_plug_coords": "thimble_plug",
    "water_cell_coords": "water_cell",
}


def _build_kind_to_universe_map(
    universes_patch: UniversesPatch | None,
    pin_map: PinMapPatch,
) -> dict[str, str]:
    """Map universe kind → universe_id from the UniversesPatch."""
    kind_map: dict[str, str] = {}
    if universes_patch is not None:
        for univ in universes_patch.universes:
            kind_map[univ.kind] = univ.universe_id
    kind_map.setdefault("fuel_pin", pin_map.default_universe_id)
    return kind_map


def expand_pin_map(
    pin_map: PinMapPatch,
    *,
    universe_ids: dict[str, str] | None = None,
) -> list[list[str]]:
    """Expand a :class:`PinMapPatch` into a full ``nx × ny`` universe pattern.

    Parameters
    ----------
    pin_map
        The pin map patch with special coordinates.
    universe_ids
        Optional override mapping of kind → universe_id.  If ``None``, the
        function builds the map from ``pin_map.default_universe_id`` (fuel)
        and leaves special kinds unresolved (caller should supply this from
        the UniversesPatch).

    Returns
    -------
    list[list[str]]
        Nested ``[row][col]`` universe id grid of shape ``nx × ny``.

    Raises
    ------
    ValueError
        If a coordinate is out of bounds or two groups overlap.
    """
    nx, ny = pin_map.lattice_size
    if nx <= 0 or ny <= 0:
        raise ValueError(f"invalid lattice_size ({nx}, {ny})")

    kind_map = universe_ids or {}
    default_uid = kind_map.get("fuel_pin", pin_map.default_universe_id)

    # Initialize full grid with default universe.
    grid: list[list[str]] = [[default_uid for _ in range(ny)] for _ in range(nx)]

    conv = pin_map.coordinate_convention

    coord_groups: dict[str, list[tuple[int, int]]] = {
        "guide_tube": pin_map.guide_tube_coords,
        "instrument_tube": pin_map.instrument_tube_coords,
        "pyrex_rod": pin_map.pyrex_rod_coords,
        "thimble_plug": pin_map.thimble_plug_coords,
        "water_cell": pin_map.water_cell_coords,
    }

    assigned: dict[tuple[int, int], str] = {}

    for group_name, raw_coords in coord_groups.items():
        uid = kind_map.get(_COORD_GROUP_TO_KIND.get(group_name, group_name), "")
        if not uid:
            # Skip groups without a universe mapping — the caller should
            # supply the mapping from UniversesPatch.
            continue
        normalized = normalized_coords(raw_coords, conv, (nx, ny))
        for row, col in normalized:
            if row < 0 or row >= nx or col < 0 or col >= ny:
                raise ValueError(
                    f"{group_name} coord ({row}, {col}) out of bounds "
                    f"for lattice {nx}x{ny}"
                )
            if (row, col) in assigned and assigned[(row, col)] != uid:
                raise ValueError(
                    f"coord ({row}, {col}) assigned to both "
                    f"{assigned[(row, col)]!r} and {uid!r}"
                )
            grid[row][col] = uid
            assigned[(row, col)] = uid

    return grid


# ---------------------------------------------------------------------------
# Patch extraction helper
# ---------------------------------------------------------------------------


def _extract_patches(
    patches: list[Any],
) -> dict[str, Any]:
    """Index patches by patch_type.  Returns dict[str, patch_model]."""
    result: dict[str, Any] = {}
    for patch in patches:
        ptype = getattr(patch, "patch_type", None)
        if ptype and ptype not in result:
            result[ptype] = patch
    return result


# ---------------------------------------------------------------------------
# Per-component assemblers
# ---------------------------------------------------------------------------


def _assemble_materials(
    patch: MaterialsPatch,
    *,
    policy: MaterialCompositionPolicy = DEFAULT_MATERIAL_POLICY,
) -> tuple[list[ComplexMaterialSpec], list[PlanAssemblyIssue], MaterialCompositionReport]:
    issues: list[PlanAssemblyIssue] = []
    materials: list[ComplexMaterialSpec] = []
    decisions: dict[str, Any] = {}
    rewritten_patches: list[MaterialSpecPatch] = []

    for mat in patch.materials:
        rewritten, decision = apply_policy_to_material_patch(mat, policy)
        rewritten_patches.append(rewritten)
        decisions[rewritten.material_id] = decision
        if decision.issue_code == "materials.alloy_library_applied":
            issues.append(PlanAssemblyIssue(
                code="materials.alloy_library_applied",
                severity="info",
                message=decision.reason,
                path=f"materials[{rewritten.material_id}]",
            ))
        elif decision.issue_code == "materials.alloy_library_missing":
            issues.append(PlanAssemblyIssue(
                code="materials.alloy_library_missing",
                severity="warning",
                message=decision.reason,
                path=f"materials[{rewritten.material_id}]",
            ))

    for mat in rewritten_patches:
        composition: list[NuclideSpec] = []
        percent_type = "ao" if mat.composition_basis == "atom_frac" else "wo"
        for name, fraction in mat.composition.items():
            if fraction <= 0:
                continue
            try:
                composition.append(NuclideSpec(
                    name=name,
                    percent=fraction,
                    percent_type=percent_type,
                    kind="nuclide",
                ))
            except Exception:
                composition.append(NuclideSpec(
                    name=name,
                    percent=fraction,
                    percent_type="ao",
                    kind="element",
                ))

        assumptions = list(mat.warnings)
        if mat.composition_status in ("approximate", "needs_library", "placeholder"):
            assumptions.append(
                f"material {mat.material_id}: composition_status="
                f"{mat.composition_status}"
            )
        if mat.source_note:
            assumptions.append(f"material {mat.material_id}: {mat.source_note}")

        requires_conf: list[str] = []
        if mat.composition_status in ("needs_confirmation", "placeholder"):
            requires_conf.append(
                f"material {mat.material_id}: composition needs confirmation"
            )

        try:
            mat_spec = ComplexMaterialSpec(
                id=mat.material_id,
                name=mat.name,
                density_value=mat.density_g_cm3,
                density_unit="g/cm3" if mat.density_g_cm3 is not None else None,
                composition=composition,
                temperature_k=mat.temperature_K,
                source=mat.source_note,
                assumptions=assumptions,
                requires_human_confirmation=requires_conf,
            )
            materials.append(mat_spec)
        except Exception as exc:
            issues.append(PlanAssemblyIssue(
                code="assembly.simulation_plan_schema_invalid",
                severity="error",
                message=f"material {mat.material_id!r} failed schema: {exc}",
                path=f"materials[{mat.material_id}]",
            ))

    report = build_composition_report(rewritten_patches, policy=policy, decisions=decisions)
    return materials, issues, report


def _assemble_universes(
    patch: UniversesPatch,
    material_ids: set[str],
) -> tuple[list[UniverseSpec], list[CellSpec], list[SurfaceSpec], list[RegionSpec], list[PlanAssemblyIssue]]:
    issues: list[PlanAssemblyIssue] = []
    universes: list[UniverseSpec] = []
    all_cells: list[CellSpec] = []
    all_surfaces: list[SurfaceSpec] = []
    all_regions: list[RegionSpec] = []

    for univ in patch.universes:
        cell_ids: list[str] = []
        prev_surface_id: str | None = None

        for cell_patch in univ.cells:
            cell_id = f"{univ.universe_id}_{cell_patch.id}"
            fill_type = _patch_fill_type_to_schema(cell_patch, material_ids)
            fill_id = _patch_fill_id_to_schema(cell_patch, material_ids)

            # Build surface/region from cell geometry (region_kind / r_min / r_max).
            region_id: str | None = None
            if cell_patch.region_kind == "cylinder" and cell_patch.r_max_cm is not None:
                # Innermost solid cylinder (e.g. fuel pellet).
                surf_id = f"surf_{cell_id}"
                all_surfaces.append(SurfaceSpec(
                    id=surf_id, kind="zcylinder",
                    parameters={"r": cell_patch.r_max_cm},
                    purpose=f"Auto-generated pin cylinder for {cell_id}",
                ))
                region_id = f"reg_{cell_id}_in"
                all_regions.append(RegionSpec(
                    id=region_id, expression=f"-{surf_id}",
                    surface_ids=[surf_id],
                    purpose=f"Inside cylinder for {cell_id}",
                ))
                prev_surface_id = surf_id

            elif cell_patch.region_kind == "annulus" and cell_patch.r_max_cm is not None:
                # Annular layer (e.g. clad, gap).
                surf_id = f"surf_{cell_id}"
                all_surfaces.append(SurfaceSpec(
                    id=surf_id, kind="zcylinder",
                    parameters={"r": cell_patch.r_max_cm},
                    purpose=f"Auto-generated pin cylinder for {cell_id}",
                ))
                if prev_surface_id is not None:
                    region_id = f"reg_{cell_id}_annulus"
                    all_regions.append(RegionSpec(
                        id=region_id,
                        expression=f"+{prev_surface_id} -{surf_id}",
                        surface_ids=[prev_surface_id, surf_id],
                        purpose=f"Annulus for {cell_id}",
                    ))
                prev_surface_id = surf_id

            elif cell_patch.region_kind == "background":
                # Outermost region (e.g. coolant outside cladding).
                if prev_surface_id is not None:
                    region_id = f"reg_{cell_id}_out"
                    all_regions.append(RegionSpec(
                        id=region_id,
                        expression=f"+{prev_surface_id}",
                        surface_ids=[prev_surface_id],
                        purpose=f"Outside region for {cell_id}",
                    ))

            try:
                cell = CellSpec(
                    id=cell_id,
                    name=cell_patch.id,
                    fill_type=fill_type,
                    fill_id=fill_id,
                    region_id=region_id,
                    purpose=cell_patch.role,
                )
                all_cells.append(cell)
                cell_ids.append(cell_id)
            except Exception as exc:
                issues.append(PlanAssemblyIssue(
                    code="assembly.simulation_plan_schema_invalid",
                    severity="error",
                    message=f"cell {cell_id!r} failed schema: {exc}",
                    path=f"universes[{univ.universe_id}].cells[{cell_patch.id}]",
                ))
        try:
            universes.append(UniverseSpec(
                id=univ.universe_id,
                name=univ.kind,
                cell_ids=cell_ids,
                purpose=univ.kind,
            ))
        except Exception as exc:
            issues.append(PlanAssemblyIssue(
                code="assembly.simulation_plan_schema_invalid",
                severity="error",
                message=f"universe {univ.universe_id!r} failed schema: {exc}",
                path=f"universes[{univ.universe_id}]",
            ))

    return universes, all_cells, all_surfaces, all_regions, issues


def _patch_fill_type_to_schema(
    cell: CellLayerPatch,
    material_ids: set[str],
) -> Literal["material", "universe", "lattice", "void"]:
    if cell.material_id is None and cell.fill_universe_id is None:
        return "void"
    if cell.fill_universe_id is not None:
        return "universe"
    return "material"


def _patch_fill_id_to_schema(
    cell: CellLayerPatch,
    material_ids: set[str],
) -> str | None:
    if cell.fill_universe_id is not None:
        return cell.fill_universe_id
    return cell.material_id


def _assemble_lattice(
    pin_map: PinMapPatch,
    facts: FactsPatch | None,
    universes_patch: UniversesPatch | None,
    universe_ids_on_plan: set[str],
) -> tuple[LatticeSpec | None, list[PlanAssemblyIssue], dict[str, int]]:
    issues: list[PlanAssemblyIssue] = []

    kind_map = _build_kind_to_universe_map(universes_patch, pin_map)

    try:
        universe_pattern = expand_pin_map(pin_map, universe_ids=kind_map)
    except ValueError as exc:
        issues.append(PlanAssemblyIssue(
            code="assembly.pin_map_expansion_failed",
            severity="error",
            message=str(exc),
            path="pin_map",
        ))
        return None, issues, {}

    nx, ny = pin_map.lattice_size
    pitch = facts.pin_pitch_cm if facts and facts.pin_pitch_cm else 1.26

    outer_universe = pin_map.default_universe_id
    if outer_universe not in universe_ids_on_plan:
        issues.append(PlanAssemblyIssue(
            code="assembly.unresolved_universe_reference",
            severity="warning",
            message=(
                f"default_universe_id {outer_universe!r} not in assembled universes"
            ),
            path="pin_map.default_universe_id",
        ))

    universe_kind_by_id = {
        univ.universe_id: univ.kind
        for univ in (universes_patch.universes if universes_patch is not None else [])
    }
    actual_pin_counts = compute_pin_role_counts(universe_pattern, universe_kind_by_id)

    expected_counts = _expected_counts_from_facts(facts)
    if expected_counts:
        expected_counts = _reconcile_expected_counts_with_actual(
            expected_counts,
            actual_pin_counts,
            total_cells=nx * ny,
            issues=issues,
        )

    try:
        lattice = LatticeSpec(
            id="assembly_lattice",
            name="assembly lattice",
            kind="rect",
            pitch_cm=(pitch, pitch),
            outer_universe_id=outer_universe,
            universe_pattern=universe_pattern,
            shape=(nx, ny),
            expected_counts=expected_counts,
            purpose="Expanded from PinMapPatch by deterministic assembler",
        )
    except Exception as exc:
        issues.append(PlanAssemblyIssue(
            code="assembly.simulation_plan_schema_invalid",
            severity="error",
            message=f"lattice assembly failed: {exc}",
            path="lattice",
        ))
        return None, issues, actual_pin_counts

    return lattice, issues, actual_pin_counts


def _expected_counts_from_facts(facts: FactsPatch | None) -> dict[str, int] | None:
    if facts is None or facts.expected_pin_count is None:
        return None
    expected_counts = {"fuel_pin": facts.expected_pin_count}
    if facts.expected_guide_tube_count is not None:
        expected_counts["guide_tube"] = facts.expected_guide_tube_count
    if facts.expected_instrument_tube_count is not None:
        expected_counts["instrument_tube"] = facts.expected_instrument_tube_count
    if facts.expected_pyrex_count is not None:
        expected_counts["pyrex_rod"] = facts.expected_pyrex_count
    if facts.expected_thimble_plug_count is not None:
        expected_counts["thimble_plug"] = facts.expected_thimble_plug_count
    return expected_counts


def _reconcile_expected_counts_with_actual(
    expected_counts: dict[str, int],
    actual_pin_counts: dict[str, int],
    *,
    total_cells: int,
    issues: list[PlanAssemblyIssue],
) -> dict[str, int]:
    actual_total = sum(actual_pin_counts.values())
    mismatches = {
        role: {"expected": expected, "actual": actual_pin_counts.get(role, 0)}
        for role, expected in expected_counts.items()
        if actual_pin_counts.get(role, 0) != expected
    }
    expected_sum = sum(expected_counts.values())
    if mismatches and actual_total == total_cells:
        canonical = {
            role: actual_pin_counts.get(role, 0)
            for role in (
                "fuel_pin",
                "guide_tube",
                "instrument_tube",
                "pyrex_rod",
                "thimble_plug",
            )
            if role in actual_pin_counts
        }
        issues.append(PlanAssemblyIssue(
            code="assembly.expected_counts_reconciled",
            severity="warning",
            message=(
                "facts expected_counts disagreed with deterministic pin_map; "
                "using actual expanded pin counts for final lattice"
            ),
            path="lattice.expected_counts",
            expected=expected_counts,
            actual=actual_pin_counts,
        ))
        return canonical
    if expected_sum != total_cells and actual_total == total_cells:
        issues.append(PlanAssemblyIssue(
            code="assembly.expected_counts_reconciled",
            severity="warning",
            message=(
                f"facts expected_counts sum {expected_sum} != lattice size "
                f"{total_cells}; using actual expanded pin counts"
            ),
            path="lattice.expected_counts",
            expected=expected_counts,
            actual=actual_pin_counts,
        ))
        return dict(actual_pin_counts)
    return expected_counts


def _assemble_axial_layers(
    patch: AxialLayersPatch,
    lattice_id: str,
    material_ids: set[str],
) -> tuple[list[AxialLayerSpec], list[PlanAssemblyIssue], dict[str, str]]:
    issues: list[PlanAssemblyIssue] = []
    layers: list[AxialLayerSpec] = []
    aliases_applied: dict[str, str] = {}

    for item in patch.layers:
        fill, alias = _layer_fill_ref(item, lattice_id, material_ids)
        if alias is not None:
            original_id, resolved_id = alias
            aliases_applied[original_id] = resolved_id
            issues.append(PlanAssemblyIssue(
                code="assembly.material_alias_resolved",
                severity="info",
                message=(
                    f"axial layer {item.layer_id!r} fill material "
                    f"{original_id!r} resolved to {resolved_id!r}"
                ),
                path=f"axial_layers[{item.layer_id}].fill_id",
                expected=resolved_id,
                actual=original_id,
            ))
        try:
            layer = AxialLayerSpec(
                id=item.layer_id,
                name=item.role,
                z_min_cm=item.z_min_cm if item.z_min_cm is not None else 0.0,
                z_max_cm=item.z_max_cm if item.z_max_cm is not None else 0.0,
                fill=fill,
                purpose=item.role,
            )
            layers.append(layer)
        except Exception as exc:
            issues.append(PlanAssemblyIssue(
                code="assembly.simulation_plan_schema_invalid",
                severity="error",
                message=f"axial layer {item.layer_id!r} failed schema: {exc}",
                path=f"axial_layers[{item.layer_id}]",
            ))

    return layers, issues, aliases_applied


def _layer_fill_ref(
    item: AxialLayerPatchItem,
    lattice_id: str,
    material_ids: set[str],
) -> tuple[FillRefSpec, tuple[str, str] | None]:
    if item.fill_type == "lattice":
        return FillRefSpec(type="lattice", id=lattice_id), None
    if item.fill_type == "void":
        return FillRefSpec(type="void", id=None), None
    if item.fill_type == "material" and item.fill_id:
        resolution = resolve_material_id(item.fill_id, material_ids)
        if resolution.ok and resolution.resolved_id:
            alias = (
                (item.fill_id, resolution.resolved_id)
                if resolution.resolved_id != item.fill_id else None
            )
            return FillRefSpec(type="material", id=resolution.resolved_id), alias
        return FillRefSpec(type="material", id=item.fill_id), None
    if item.fill_type == "universe" and item.fill_id:
        return FillRefSpec(type="universe", id=item.fill_id), None
    return FillRefSpec(type="void", id=None), None


def _assemble_axial_overlays(
    patch: AxialOverlaysPatch,
    lattice_id: str,
    material_ids: set[str],
    material_aliases: dict[str, str] | None = None,
) -> tuple[list[AxialOverlaySpec], list[PlanAssemblyIssue], dict[str, str]]:
    issues: list[PlanAssemblyIssue] = []
    overlays: list[AxialOverlaySpec] = []
    aliases_applied: dict[str, str] = {}

    for item in patch.overlays:
        material_id = item.material_id
        if material_id is not None:
            resolution = resolve_material_id(material_id, material_ids, material_aliases)
            if resolution.ok and resolution.resolved_id is not None:
                if resolution.resolved_id != material_id:
                    aliases_applied[material_id] = resolution.resolved_id
                    issues.append(PlanAssemblyIssue(
                        code="assembly.material_alias_resolved",
                        severity="info",
                        message=(
                            f"overlay {item.overlay_id!r} material_id "
                            f"{material_id!r} resolved to {resolution.resolved_id!r}"
                        ),
                        path=f"axial_overlays[{item.overlay_id}].material_id",
                        expected=resolution.resolved_id,
                        actual=material_id,
                    ))
                material_id = resolution.resolved_id
            elif item.geometry_mode != "skeleton":
                issues.append(PlanAssemblyIssue(
                    code="assembly.unresolved_material_reference",
                    severity="error",
                    message=resolution.reason or (
                        f"overlay {item.overlay_id!r} references missing "
                        f"material_id {material_id!r}"
                    ),
                    path=f"axial_overlays[{item.overlay_id}].material_id",
                    actual=material_id,
                ))
        try:
            overlay = AxialOverlaySpec(
                id=item.overlay_id,
                overlay_kind=item.overlay_kind,
                z_min_cm=item.z_min_cm,
                z_max_cm=item.z_max_cm,
                target_lattice_id=item.target_lattice_id or lattice_id,
                material_id=material_id,
                geometry_mode=item.geometry_mode,
                through_path_preserved=item.through_path_preserved,
                volume_fraction=item.volume_fraction,
                effective_density_g_cm3=item.effective_density_g_cm3,
                requires_human_confirmation=item.requires_human_confirmation,
                assumptions=list(item.assumptions),
                source_note=item.source_note,
            )
            overlays.append(overlay)
        except Exception as exc:
            issues.append(PlanAssemblyIssue(
                code="assembly.simulation_plan_schema_invalid",
                severity="error",
                message=f"overlay {item.overlay_id!r} failed schema: {exc}",
                path=f"axial_overlays[{item.overlay_id}]",
            ))

    return overlays, issues, aliases_applied


# ---------------------------------------------------------------------------
# Required patch checking
# ---------------------------------------------------------------------------

_REQUIRED_3D: tuple[str, ...] = (
    "facts", "materials", "universes", "pin_map", "axial_layers", "settings",
)


def _check_required_patches(
    indexed: dict[str, Any],
    facts: FactsPatch | None,
) -> list[PlanAssemblyIssue]:
    issues: list[PlanAssemblyIssue] = []
    for ptype in _REQUIRED_3D:
        if ptype not in indexed:
            hint = ""
            if ptype == "pin_map":
                hint = (
                    "; possible_cause: feature detection did not see structural "
                    "signals (guide tubes / large lattice / benchmark variant) — "
                    "check that the requirement includes the input file content"
                )
            issues.append(PlanAssemblyIssue(
                code="assembly.missing_patch",
                severity="error",
                message=f"required {ptype} patch is missing{hint}",
                path=ptype,
            ))
    if facts and facts.has_spacer_grids and "axial_overlays" not in indexed:
        issues.append(PlanAssemblyIssue(
            code="assembly.missing_patch",
            severity="error",
            message="required axial_overlays patch is missing (spacer grids expected)",
            path="axial_overlays",
        ))
    return issues


# ---------------------------------------------------------------------------
# Main assembler API
# ---------------------------------------------------------------------------


def assemble_simulation_plan_from_patches(
    patches: list[Any],
    *,
    strict: bool = True,
    material_policy: str | MaterialCompositionPolicy | None = None,
) -> PlanAssemblyResult:
    """Assemble validated patches into a complete ``SimulationPlan``.

    Parameters
    ----------
    patches
        List of parsed patch models (from ``parse_patch_content``).
    strict
        When True, missing required patches or unresolved references produce
        errors that block assembly.
    material_policy
        Optional material composition policy. Accepts the enum, a string value,
        or None (uses :data:`DEFAULT_MATERIAL_POLICY`).

    Returns
    -------
    PlanAssemblyResult
        ``ok=True`` with a valid ``SimulationPlan`` on success;
        ``ok=False`` with structured issues on failure. The
        ``material_composition_report`` field is populated on success.
    """
    policy = policy_from_value(material_policy)
    issues: list[PlanAssemblyIssue] = []
    indexed = _extract_patches(patches)
    actual_pin_counts: dict[str, int] = {}
    material_aliases_applied: dict[str, str] = {}
    material_composition_report: MaterialCompositionReport | None = None

    facts: FactsPatch | None = indexed.get("facts")
    materials_patch: MaterialsPatch | None = indexed.get("materials")
    universes_patch: UniversesPatch | None = indexed.get("universes")
    pin_map_patch: PinMapPatch | None = indexed.get("pin_map")
    axial_layers_patch: AxialLayersPatch | None = indexed.get("axial_layers")
    axial_overlays_patch: AxialOverlaysPatch | None = indexed.get("axial_overlays")
    settings_patch: SettingsPatch | None = indexed.get("settings")

    # 1. Check required patches.
    missing_issues = _check_required_patches(indexed, facts)
    issues.extend(missing_issues)
    if missing_issues and strict:
        return PlanAssemblyResult(
            ok=False,
            issues=issues,
            summary={"error_count": len(missing_issues)},
        )

    # 2. Assemble materials.
    if materials_patch is None:
        materials_patch = MaterialsPatch(materials=[])
    plan_materials, mat_issues, material_composition_report = _assemble_materials(
        materials_patch, policy=policy,
    )
    issues.extend(mat_issues)
    material_ids = {m.id for m in plan_materials}

    # 3. Assemble universes + cells.
    if universes_patch is None:
        universes_patch = UniversesPatch(universes=[])
    plan_universes, plan_cells, pin_surfaces, pin_regions, univ_issues = _assemble_universes(
        universes_patch, material_ids,
    )
    issues.extend(univ_issues)
    universe_ids = {u.id for u in plan_universes}

    # 4. Assemble lattice from pin map.
    lattice_id = "assembly_lattice"
    if pin_map_patch is not None:
        lattice, lat_issues, actual_pin_counts = _assemble_lattice(
            pin_map_patch, facts, universes_patch, universe_ids,
        )
        issues.extend(lat_issues)
    else:
        lattice = None

    # 5. Assemble axial layers.
    if axial_layers_patch is not None:
        axial_layers, al_issues, axial_layer_aliases = _assemble_axial_layers(
            axial_layers_patch, lattice_id, material_ids,
        )
        issues.extend(al_issues)
        material_aliases_applied.update(axial_layer_aliases)
    else:
        axial_layers = []

    # 6. Assemble axial overlays.
    if axial_overlays_patch is not None:
        axial_overlays, ao_issues, overlay_aliases = _assemble_axial_overlays(
            axial_overlays_patch, lattice_id, material_ids,
        )
        issues.extend(ao_issues)
        material_aliases_applied.update(overlay_aliases)
    else:
        axial_overlays = []

    # 7. Build assembly spec.
    assembly_pitch = (
        facts.assembly_pitch_cm if facts and facts.assembly_pitch_cm
        else None
    )
    assembly = AssemblySpec(
        id="assembly_1",
        name="assembled assembly",
        lattice_id=lattice.id if lattice else None,
        pitch_cm=assembly_pitch,
        boundary="reflective",
        purpose="Assembled from incremental patches",
    )

    # 8. Build core spec.
    core = CoreSpec(
        id="core_1",
        name="assembled core",
        lattice_id=lattice.id if lattice else None,
        assembly_ids=[assembly.id],
        axial_layers=axial_layers,
        axial_overlays=axial_overlays,
        boundary="vacuum",
        purpose="Assembled from incremental patches",
    )

    # 9. Collect all assumptions and confirmations.
    all_assumptions: list[str] = []
    all_confirms: list[str] = []
    for mat in plan_materials:
        all_assumptions.extend(mat.assumptions)
        all_confirms.extend(mat.requires_human_confirmation)
    if facts and facts.assumptions:
        all_assumptions.extend(facts.assumptions)
    if facts and facts.missing_facts:
        all_confirms.extend(f"missing fact: {f}" for f in facts.missing_facts)

    # 10. Build ComplexModelSpec.
    lattices = [lattice] if lattice else []
    complex_model = ComplexModelSpec(
        name=assembly.name,
        kind="assembly",
        materials=plan_materials,
        cells=plan_cells,
        surfaces=pin_surfaces,
        regions=pin_regions,
        universes=plan_universes,
        lattices=lattices,
        assemblies=[assembly],
        core=core,
        assumptions=list(dict.fromkeys(all_assumptions)),
        requires_human_confirmation=list(dict.fromkeys(all_confirms)),
    )

    # 11. Build plot specs — derive ranges from actual geometry, not hardcoded.
    plot_strategy = (
        settings_patch.plot_strategy if settings_patch else "full_assembly"
    )
    # Compute actual axial domain from assembled layers.
    if axial_layers:
        axial_z_min = min(l.z_min_cm for l in axial_layers)
        axial_z_max = max(l.z_max_cm for l in axial_layers)
    else:
        axial_z_min, axial_z_max = -1.0, 1.0
    axial_height = axial_z_max - axial_z_min
    # XY origin centered on assembly.
    pitch = assembly_pitch or 21.50
    xy_origin = (pitch / 2.0, pitch / 2.0, (axial_z_min + axial_z_max) / 2.0)
    xz_origin = (pitch / 2.0, 0.0, (axial_z_min + axial_z_max) / 2.0)
    if plot_strategy == "full_assembly":
        plot_specs = [
            PlotSpec(
                basis="xy",
                origin=xy_origin,
                width_cm=(pitch, pitch),
                filename="assembly_xy.png",
            ),
            PlotSpec(
                basis="xz",
                origin=xz_origin,
                width_cm=(pitch, axial_height),
                filename="assembly_xz.png",
            ),
        ]
    else:
        plot_specs = [
            PlotSpec(
                basis="xy",
                origin=xy_origin,
                width_cm=(pitch, pitch),
                filename="assembly_xy.png",
            ),
        ]

    # 12. Build execution check.
    execution_check = ExecutionCheckSpec(
        enabled=True,
        settings=RunSettingsSpec(batches=5, inactive=1, particles=100),
    )

    # 13. Build capability report — non-executable skeleton until renderer
    #     assesses the assembled plan.
    capability = RenderCapabilityReport(
        renderability="none",
        is_executable=False,
        supported_renderer="none",
        reasons=["assembled from incremental patches; awaiting capability assessment"],
    )

    # 14. Assemble SimulationPlan.
    try:
        plan = SimulationPlan(
            schema_version="simulation_plan.v2",
            complex_model=complex_model,
            capability_report=capability,
            plot_specs=plot_specs,
            execution_check=execution_check,
            expert_assumptions=list(dict.fromkeys(all_assumptions)),
        )
    except Exception as exc:
        issues.append(PlanAssemblyIssue(
            code="assembly.simulation_plan_schema_invalid",
            severity="error",
            message=f"final SimulationPlan validation failed: {exc}",
        ))
        return PlanAssemblyResult(
            ok=False,
            issues=issues,
            summary={"error_count": sum(1 for i in issues if i.severity == "error")},
        )

    errors = [i for i in issues if i.severity == "error"]
    return PlanAssemblyResult(
        ok=len(errors) == 0,
        plan=plan,
        plan_dict=plan.model_dump(mode="json"),
        issues=issues,
        summary={
            "error_count": len(errors),
            "warning_count": sum(1 for i in issues if i.severity == "warning"),
            "info_count": sum(1 for i in issues if i.severity == "info"),
            "material_count": len(plan_materials),
            "universe_count": len(plan_universes),
            "lattice_present": lattice is not None,
            "axial_layer_count": len(axial_layers),
            "axial_overlay_count": len(axial_overlays),
            "actual_pin_counts": actual_pin_counts,
            "material_aliases_applied": material_aliases_applied,
            "material_composition_policy": policy.value,
            "material_composition_report_present": material_composition_report is not None,
        },
        material_composition_report=material_composition_report,
    )


__all__ = [
    "PlanAssemblyIssue",
    "PlanAssemblyResult",
    "expand_pin_map",
    "compute_pin_role_counts",
    "assemble_simulation_plan_from_patches",
]
