"""Evaluation-only VERA4 base-case acceptance (P2-FULLCORE-2D-A).

Checks deterministic VERA4 base-case fidelity at five levels:
  A. Input/Facts — axial domain, boundaries, counts, coordinates
  B. Patch/Plan — exact Pyrex/thimble/RCCA coordinates, profile references
  C. Concrete Geometry — universes, materials, state separation
  D. XML — model.py not skeleton, all XML, refs complete
  E. Runtime — geometry debug, transport smoke

This module is evaluation-only. Production planner, assembler, materializer,
and renderer must NOT import it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ACCEPTANCE_CONTRACT_VERSION = "2.0.0"


@dataclass
class AcceptanceCheck:
    code: str
    passed: bool
    message: str = ""
    level: str = ""


@dataclass
class AcceptanceResult:
    ok: bool = True
    checks: list[AcceptanceCheck] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def failed_codes(self) -> list[str]:
        return [c.code for c in self.checks if not c.passed]


def check_facts_level(facts: Any) -> list[AcceptanceCheck]:
    """Level A: Input/Facts checks."""
    checks: list[AcceptanceCheck] = []

    domain = getattr(facts, "axial_domain_cm", None)
    checks.append(AcceptanceCheck(
        code="facts.domain_min",
        passed=domain is not None and domain[0] == -55.0,
        message=f"axial domain min={domain[0] if domain else None}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.domain_max",
        passed=domain is not None and abs(domain[1] - 463.937) < 1e-3,
        message=f"axial domain max={domain[1] if domain else None}",
        level="A",
    ))

    scope = getattr(facts, "model_scope", "")
    checks.append(AcceptanceCheck(
        code="facts.multi_assembly_scope",
        passed=scope == "multi_assembly_core",
        message=f"scope={scope}",
        level="A",
    ))

    boundary = getattr(facts, "assembly_type_counts", {})
    checks.append(AcceptanceCheck(
        code="facts.assembly_counts",
        passed=boundary == {"corner": 4, "edge": 4, "center_rcca": 1},
        message=f"counts={boundary}",
        level="A",
    ))

    scoped = {c.role: c.value for c in getattr(facts, "scoped_expected_counts", [])}
    checks.append(AcceptanceCheck(
        code="facts.fuel_pin_count",
        passed=scoped.get("fuel_pin") == 2376,
        message=f"fuel_pin={scoped.get('fuel_pin')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.guide_tube_count",
        passed=scoped.get("guide_tube") == 216,
        message=f"guide_tube={scoped.get('guide_tube')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.pyrex_count",
        passed=scoped.get("pyrex_rod") == 80,
        message=f"pyrex={scoped.get('pyrex_rod')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.thimble_count",
        passed=scoped.get("thimble_plug") == 112,
        message=f"thimble={scoped.get('thimble_plug')}",
        level="A",
    ))

    return checks


def check_plan_level(
    plan: Any,
    *,
    expected_pyrex: int = 80,
    expected_thimble: int = 112,
    expected_rcca: int = 24,
) -> list[AcceptanceCheck]:
    """Level B: Patch/Plan checks."""
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model

    uv_ids = {u.id for u in model.universes}
    mat_ids = {m.id for m in model.materials}

    # Check key universes exist
    for uv_name in ["fuel_active_r1", "fuel_active_r2", "guide_tube", "inst_tube",
                    "pyrex_poison", "thimble_plug", "rcca_aic", "rcca_b4c"]:
        checks.append(AcceptanceCheck(
            code=f"plan.universe.{uv_name}",
            passed=uv_name in uv_ids,
            message=f"universe {uv_name}={'present' if uv_name in uv_ids else 'MISSING'}",
            level="B",
        ))

    # Check key materials exist
    for mat_name in ["fuel_r1", "fuel_r2", "water", "zircaloy4", "ss304",
                     "lower_nozzle_mix", "upper_nozzle_mix",
                     "lower_core_plate_mix", "upper_core_plate_mix",
                     "pyrex_glass", "rcca_aic_mat", "rcca_b4c_mat"]:
        checks.append(AcceptanceCheck(
            code=f"plan.material.{mat_name}",
            passed=mat_name in mat_ids,
            message=f"material {mat_name}={'present' if mat_name in mat_ids else 'MISSING'}",
            level="B",
        ))

    # Check axial layers have whole-plane and detailed mix
    layers = model.core.axial_layers if model.core else []
    fill_types = {l.fill.type for l in layers}
    checks.append(AcceptanceCheck(
        code="plan.axial_layers.has_material_fill",
        passed="material" in fill_types,
        message=f"fill types={fill_types}",
        level="B",
    ))
    checks.append(AcceptanceCheck(
        code="plan.axial_layers.has_lattice_fill",
        passed="lattice" in fill_types,
        message=f"fill types={fill_types}",
        level="B",
    ))

    # Check domain coverage
    if layers:
        checks.append(AcceptanceCheck(
            code="plan.axial_layers.domain_min",
            passed=abs(layers[0].z_min_cm - (-55.0)) < 1e-3,
            message=f"first layer z_min={layers[0].z_min_cm}",
            level="B",
        ))
        checks.append(AcceptanceCheck(
            code="plan.axial_layers.domain_max",
            passed=abs(layers[-1].z_max_cm - 463.937) < 1e-3,
            message=f"last layer z_max={layers[-1].z_max_cm}",
            level="B",
        ))

    return checks


def check_geometry_level(plan: Any) -> list[AcceptanceCheck]:
    """Level C: Concrete Geometry checks."""
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model

    uv_ids = {u.id for u in model.universes}
    lat_ids = {l.id for l in model.lattices}
    cell_ids = {c.id for c in model.cells}
    mat_ids = {m.id for m in model.materials}

    # Reference integrity: every cell fill references exist
    broken_cells = 0
    for cell in model.cells:
        if cell.fill_type == "lattice" and cell.fill_id not in lat_ids:
            broken_cells += 1
        elif cell.fill_type == "material" and cell.fill_id not in mat_ids:
            broken_cells += 1
    checks.append(AcceptanceCheck(
        code="geo.cell_refs_ok",
        passed=broken_cells == 0,
        message=f"broken cell refs={broken_cells}",
        level="C",
    ))

    # Lattice universe refs
    broken_lat_refs = 0
    for lat in model.lattices:
        if lat.universe_pattern:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid not in uv_ids:
                        broken_lat_refs += 1
        if lat.outer_universe_id and lat.outer_universe_id not in uv_ids:
            broken_lat_refs += 1
    checks.append(AcceptanceCheck(
        code="geo.lattice_refs_ok",
        passed=broken_lat_refs == 0,
        message=f"broken lattice refs={broken_lat_refs}",
        level="C",
    ))

    # Check mixture materials have components
    mix_ok = True
    for mat in model.materials:
        if mat.is_mixture:
            if not mat.mixture_component_ids:
                mix_ok = False
            elif not all(cid in mat_ids for cid in mat.mixture_component_ids):
                mix_ok = False
    checks.append(AcceptanceCheck(
        code="geo.mixture_refs_ok",
        passed=mix_ok,
        message="mixture component refs",
        level="C",
    ))

    # Check RCCA universes are reachable in derived lattices
    rcca_in_lattices = False
    for lat in model.lattices:
        if lat.universe_pattern and lat.id not in {"core_lattice"}:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid in ("rcca_aic", "rcca_b4c"):
                        rcca_in_lattices = True
    checks.append(AcceptanceCheck(
        code="geo.rcca_reachable",
        passed=rcca_in_lattices,
        message="RCCA universes in derived lattices",
        level="C",
    ))

    # Pyrex reachable
    pyrex_in_lattices = False
    for lat in model.lattices:
        if lat.universe_pattern and lat.id not in {"core_lattice"}:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid in ("pyrex_poison", "pyrex_plenum"):
                        pyrex_in_lattices = True
    checks.append(AcceptanceCheck(
        code="geo.pyrex_reachable",
        passed=pyrex_in_lattices,
        message="Pyrex universes in derived lattices",
        level="C",
    ))

    return checks


def check_fuel_fidelity_level(plan: Any) -> list[AcceptanceCheck]:
    """Level G: Fuel source fidelity — VERA4-specific fuel variant checks.

    Verifies that Region 1 (2.11%) and Region 2 (2.619%) are correctly
    bound to their assembly types and reachable in the final geometry.
    """
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model
    if model is None:
        checks.append(AcceptanceCheck(
            code="fuel.variant_count", passed=False,
            message="no complex_model", level="G",
        ))
        return checks

    materials = model.materials or []
    # ComplexMaterialSpec has no role field; identify fuel by composition or id
    fuel_mats = []
    for m in materials:
        mid = (getattr(m, "id", "") or "").lower()
        formula = getattr(m, "chemical_formula", "") or ""
        comp = getattr(m, "composition", []) or []
        has_u235 = any(
            getattr(n, "nuclide", "").startswith("U235")
            or getattr(n, "nuclide", "") == "U235"
            for n in comp
        )
        if "fuel" in mid or "region" in mid or formula == "UO2" or has_u235:
            fuel_mats.append(m)

    # Check exactly 2 fuel variants
    checks.append(AcceptanceCheck(
        code="fuel.variant_count", passed=len(fuel_mats) == 2,
        message=f"{len(fuel_mats)} fuel materials (expected 2)",
        level="G",
    ))

    # Check Region 1 material present (2.11%)
    r1 = next((m for m in fuel_mats
               if "fuel_r1" in m.id.lower() or "region1" in m.id.lower()
               or "2.11" in getattr(m, "name", "")), None)
    checks.append(AcceptanceCheck(
        code="fuel.region1_material_present", passed=r1 is not None,
        message=f"region1 material={'found' if r1 else 'MISSING'}",
        level="G",
    ))

    # Check Region 2 material present (2.619%)
    r2 = next((m for m in fuel_mats
               if "fuel_r2" in m.id.lower() or "region2" in m.id.lower()
               or "2.619" in getattr(m, "name", "")), None)
    checks.append(AcceptanceCheck(
        code="fuel.region2_material_present", passed=r2 is not None,
        message=f"region2 material={'found' if r2 else 'MISSING'}",
        level="G",
    ))

    # Check materials are referenced in geometry (not just defined)
    cells = model.cells or []
    cells_by_id = {c.id: c for c in cells}
    all_mat_refs: set[str] = set()
    for c in cells:
        if c.fill_type == "material" and c.fill_id:
            all_mat_refs.add(c.fill_id)
    if r1:
        checks.append(AcceptanceCheck(
            code="fuel.region1_reachable", passed=r1.id in all_mat_refs,
            message=f"region1 ({r1.id}) {'referenced' if r1.id in all_mat_refs else 'NOT referenced'} in geometry",
            level="G",
        ))
    if r2:
        checks.append(AcceptanceCheck(
            code="fuel.region2_reachable", passed=r2.id in all_mat_refs,
            message=f"region2 ({r2.id}) {'referenced' if r2.id in all_mat_refs else 'NOT referenced'} in geometry",
            level="G",
        ))

    # Check correct binding in assembly catalog
    catalog = getattr(model, "assembly_catalog", None)
    universes = model.universes or []
    uv_by_id = {u.id: u for u in universes}
    if catalog and hasattr(catalog, "assembly_types"):
        for atype in catalog.assembly_types:
            tid = atype.assembly_type_id
            pm = atype.pin_map
            default_uv = pm.default_universe_id if pm else None
            # Find the universe's fuel material via cell_ids → cells → fill_id
            uv_fuel_mat = None
            uv = uv_by_id.get(default_uv)
            if uv:
                for cid in (uv.cell_ids or []):
                    cell = cells_by_id.get(cid)
                    if cell and cell.fill_type == "material" and cell.fill_id:
                        comp_role = getattr(cell, "component_role", "") or ""
                        if "fuel" in comp_role.lower() or "fuel" in (cell.name or "").lower():
                            uv_fuel_mat = cell.fill_id
                            break

            if tid in ("corner", "center_rcca", "C", "R"):
                expected = r1.id if r1 else "fuel_r1"
                checks.append(AcceptanceCheck(
                    code=f"fuel.{tid}_binding", passed=uv_fuel_mat == expected,
                    message=f"{tid}: fuel_material={uv_fuel_mat}, expected={expected}",
                    level="G",
                ))
            elif tid in ("edge", "E"):
                expected = r2.id if r2 else "fuel_r2"
                checks.append(AcceptanceCheck(
                    code=f"fuel.{tid}_binding", passed=uv_fuel_mat == expected,
                    message=f"{tid}: fuel_material={uv_fuel_mat}, expected={expected}",
                    level="G",
                ))

    return checks


def run_full_acceptance(
    plan: Any,
    *,
    xml_dir: Path | None = None,
    smoke_result: dict[str, Any] | None = None,
) -> AcceptanceResult:
    """Run all acceptance levels and return combined result."""
    result = AcceptanceResult()

    # Level A: Facts (extract from plan)
    model = plan.complex_model
    # Facts are embedded in the plan's assumptions/settings
    # We check the model structure directly
    checks_a = []
    layers = model.core.axial_layers if model.core else []
    checks_a.append(AcceptanceCheck(
        code="facts.domain_span",
        passed=layers and abs(layers[0].z_min_cm - (-55.0)) < 1e-3 and abs(layers[-1].z_max_cm - 463.937) < 1e-3,
        message=f"domain=[{layers[0].z_min_cm if layers else None}, {layers[-1].z_max_cm if layers else None}]",
        level="A",
    ))
    result.checks.extend(checks_a)

    # Level B: Plan
    result.checks.extend(check_plan_level(plan))

    # Level C: Geometry
    result.checks.extend(check_geometry_level(plan))

    # Level F: Grid geometry (VERA4-specific)
    result.checks.extend(check_grid_geometry_level(plan))

    # Level G: Fuel fidelity (VERA4-specific fuel variant binding)
    result.checks.extend(check_fuel_fidelity_level(plan))

    # Level H: RCCA placement (VERA4-specific control rod placement)
    result.checks.extend(check_rcca_placement_level(plan))

    # Level D: XML (if provided)
    if xml_dir and xml_dir.exists():
        xml_checks = check_xml_level(xml_dir)
        result.checks.extend(xml_checks)

    # Level E: Runtime (if provided)
    if smoke_result:
        rt_checks = check_runtime_level(smoke_result)
        result.checks.extend(rt_checks)

    result.ok = all(c.passed for c in result.checks)
    result.summary = {
        "total": len(result.checks),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "failed_codes": result.failed_codes,
    }
    return result


def check_grid_geometry_level(plan: Any) -> list[AcceptanceCheck]:
    """Level F: Grid geometry acceptance — VERA4-specific quantity checks.

    These checks hardcode VERA4 expected counts (8 bands, 72 instances,
    18 end grids, 54 middle grids).  The generic validator in
    ``grid_geometry_validation.py`` handles reactor-neutral checks.
    """
    from openmc_agent.plan_builder.grid_geometry_validation import (
        build_grid_geometry_reachability_report,
        validate_grid_geometry_materialization,
    )

    checks: list[AcceptanceCheck] = []
    model = plan.complex_model
    if model is None or model.core is None:
        checks.append(AcceptanceCheck(
            code="grid.model_present", passed=False,
            message="No complex_model/core", level="F",
        ))
        return checks

    overlays = [
        ov for ov in (model.core.axial_overlays or [])
        if getattr(ov, "overlay_kind", None) == "spacer_grid"
        and getattr(ov, "geometry_mode", "skeleton") != "skeleton"
    ]

    # --- Grid band count ---
    checks.append(AcceptanceCheck(
        code="grid.band_count",
        passed=len(overlays) == 8,
        message=f"grid_bands={len(overlays)} expected=8",
        level="F",
    ))

    # --- Physical instances: 8 bands × 9 assemblies = 72 ---
    core_lat = next((l for l in model.lattices if l.id == model.core.lattice_id), None)
    n_assemblies = 0
    if core_lat and core_lat.universe_pattern:
        n_assemblies = sum(len(row) for row in core_lat.universe_pattern)
    expected_instances = len(overlays) * n_assemblies if overlays else 0
    checks.append(AcceptanceCheck(
        code="grid.instance_count",
        passed=n_assemblies > 0 and expected_instances == 72,
        message=f"instances={expected_instances} ({len(overlays)}×{n_assemblies}) expected=72",
        level="F",
    ))

    # --- End grids (Inconel) vs Middle grids (Zircaloy) ---
    end_grids = [ov for ov in overlays if getattr(ov, "material_id", "") == "inconel718"]
    mid_grids = [ov for ov in overlays if getattr(ov, "material_id", "") == "zircaloy4"]
    checks.append(AcceptanceCheck(
        code="grid.end_grid_count",
        passed=len(end_grids) == 2,
        message=f"end_grids={len(end_grids)} expected=2",
        level="F",
    ))
    checks.append(AcceptanceCheck(
        code="grid.middle_grid_count",
        passed=len(mid_grids) == 6,
        message=f"middle_grids={len(mid_grids)} expected=6",
        level="F",
    ))

    # --- Grid-decorated universes exist ---
    decorated = [u for u in model.universes if "__grid__" in u.id]
    checks.append(AcceptanceCheck(
        code="grid.decorated_universes_exist",
        passed=len(decorated) > 0,
        message=f"decorated_universes={len(decorated)}",
        level="F",
    ))

    # --- Lattices reference decorated IDs ---
    lattices_with_grid = []
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if "__grid__" in uid:
                    lattices_with_grid.append(lat.id)
                    break
            else:
                continue
            break
    checks.append(AcceptanceCheck(
        code="grid.lattices_reference_decorated",
        passed=len(lattices_with_grid) > 0,
        message=f"lattices_with_grid={len(lattices_with_grid)}",
        level="F",
    ))

    # --- Grid materials reachable ---
    val_result = validate_grid_geometry_materialization(plan)
    checks.append(AcceptanceCheck(
        code="grid.validator_passes",
        passed=val_result.ok,
        message=f"validator_ok={val_result.ok} errors={len(val_result.errors)}",
        level="F",
    ))

    # --- Reachability report ---
    rep = build_grid_geometry_reachability_report(plan)
    checks.append(AcceptanceCheck(
        code="grid.reachability_passes",
        passed=rep.result == "pass",
        message=f"reachability={rep.result} missing={len(rep.missing_refs)} unreachable={len(rep.unreachable_refs)}",
        level="F",
    ))

    # --- Frame cells exist ---
    frame_cells = []
    for uv in decorated:
        for cid in (uv.cell_ids or []):
            cell = next((c for c in model.cells if c.id == cid), None)
            if cell and ("grid_frame" in (cell.component_role or "").lower()
                         or "grid_frame" in cid.lower()):
                frame_cells.append(cid)
    checks.append(AcceptanceCheck(
        code="grid.frame_cells_exist",
        passed=len(frame_cells) > 0,
        message=f"frame_cells={len(frame_cells)}",
        level="F",
    ))

    # --- Frame regions exist ---
    frame_regions = []
    for cid in frame_cells:
        cell = next((c for c in model.cells if c.id == cid), None)
        if cell and cell.region_id:
            region = next((r for r in model.regions if r.id == cell.region_id), None)
            if region:
                frame_regions.append(cell.region_id)
    checks.append(AcceptanceCheck(
        code="grid.frame_regions_exist",
        passed=len(frame_regions) > 0,
        message=f"frame_regions={len(frame_regions)}",
        level="F",
    ))

    # --- Grid material IDs present in material catalog ---
    grid_mat_ids = {getattr(ov, "material_id", None) for ov in overlays}
    grid_mat_ids.discard(None)
    all_mat_ids = {m.id for m in model.materials}
    missing_mats = grid_mat_ids - all_mat_ids
    checks.append(AcceptanceCheck(
        code="grid.materials_in_catalog",
        passed=len(missing_mats) == 0,
        message=f"grid_materials={sorted(grid_mat_ids)} missing={sorted(missing_mats)}",
        level="F",
    ))

    # --- Assembly gap has no grid material ---
    # The moderator_outer universe (assembly gap fill) should not contain
    # any cell referencing a grid material.
    mod_outer = next((u for u in model.universes if u.id == "moderator_outer"), None)
    gap_has_grid = False
    if mod_outer:
        for cid in (mod_outer.cell_ids or []):
            cell = next((c for c in model.cells if c.id == cid), None)
            if cell and cell.fill_type == "material" and cell.fill_id in grid_mat_ids:
                gap_has_grid = True
                break
    checks.append(AcceptanceCheck(
        code="grid.assembly_gap_no_grid_material",
        passed=not gap_has_grid,
        message=f"gap_has_grid_material={gap_has_grid}",
        level="F",
    ))

    return checks


def check_rcca_placement_level(plan: Any) -> list[AcceptanceCheck]:
    """Level H: RCCA placement acceptance — VERA4-specific.

    Verifies that RCCA control rods are actually *placed* in the final
    geometry, not just defined as universes. Checks root reachability,
    path counts, and clipping semantics for the center assembly.
    """
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model
    if model is None:
        checks.append(AcceptanceCheck(
            code="rcca.model_present", passed=False,
            message="No complex_model", level="H",
        ))
        return checks

    from openmc_agent.reachability import collect_active_dependencies
    deps = collect_active_dependencies(plan)
    reachable_uvs = set(deps.universe_ids)

    # Count RCCA paths: find the MAXIMUM number of RCCA positions in any single lattice
    # (not total across all lattices, since multiple axial layers may each have their own
    # derived lattice with the same RCCA positions)
    aic_max = 0
    b4c_max = 0
    aic_in_lattice = False
    b4c_in_lattice = False
    for lat in model.lattices:
        lat_aic = 0
        lat_b4c = 0
        for row in lat.universe_pattern:
            for uid in row:
                if uid == "rcca_aic":
                    lat_aic += 1
                    aic_in_lattice = True
                elif uid == "rcca_b4c":
                    lat_b4c += 1
                    b4c_in_lattice = True
        aic_max = max(aic_max, lat_aic)
        b4c_max = max(b4c_max, lat_b4c)

    # rcca.aic_paths_24
    checks.append(AcceptanceCheck(
        code="rcca.aic_paths_24",
        passed=aic_max == 24,
        message=f"AIC max paths in a single lattice: {aic_max} (expected 24)",
        level="H",
    ))

    # rcca.b4c_paths_24
    checks.append(AcceptanceCheck(
        code="rcca.b4c_paths_24",
        passed=b4c_max == 24,
        message=f"B4C max paths in a single lattice: {b4c_max} (expected 24)",
        level="H",
    ))

    # rcca.root_reachable
    aic_reachable = "rcca_aic" in reachable_uvs
    b4c_reachable = "rcca_b4c" in reachable_uvs
    checks.append(AcceptanceCheck(
        code="rcca.root_reachable",
        passed=aic_reachable and b4c_reachable,
        message=f"rcca_aic reachable={aic_reachable}, rcca_b4c reachable={b4c_reachable}",
        level="H",
    ))

    # rcca.plenum_clipped_out: plenum/endplug should NOT be reachable
    # (they are above the detailed domain upper bound)
    plenum_orphaned = "rcca_plenum" not in reachable_uvs
    endplug_orphaned = "rcca_endplug" not in reachable_uvs
    checks.append(AcceptanceCheck(
        code="rcca.plenum_clipped_out",
        passed=plenum_orphaned,
        message=f"rcca_plenum not root-reachable (clipped): {plenum_orphaned}",
        level="H",
    ))
    checks.append(AcceptanceCheck(
        code="rcca.endplug_clipped_out",
        passed=endplug_orphaned,
        message=f"rcca_endplug not root-reachable (clipped): {endplug_orphaned}",
        level="H",
    ))

    # rcca.xml_reachable: at least one derived lattice references RCCA
    checks.append(AcceptanceCheck(
        code="rcca.derived_lattice_references",
        passed=aic_in_lattice or b4c_in_lattice,
        message=f"AIC in lattice={aic_in_lattice}, B4C in lattice={b4c_in_lattice}",
        level="H",
    ))

    # rcca.guide_tube_preserved: guide_tube universe should still be reachable
    # (it's the host path outside RCCA z-range)
    gt_reachable = "guide_tube" in reachable_uvs
    checks.append(AcceptanceCheck(
        code="rcca.guide_tube_preserved",
        passed=gt_reachable,
        message=f"guide_tube reachable={gt_reachable}",
        level="H",
    ))

    return checks


def check_xml_level(xml_dir: Path) -> list[AcceptanceCheck]:
    """Level D: XML checks."""
    checks: list[AcceptanceCheck] = []

    for xml_name in ["materials.xml", "geometry.xml", "settings.xml"]:
        f = xml_dir / xml_name
        checks.append(AcceptanceCheck(
            code=f"xml.{xml_name}",
            passed=f.exists() and f.stat().st_size > 0,
            message=f"{xml_name}={'OK' if f.exists() else 'MISSING'}",
            level="D",
        ))

    model_py = xml_dir / "model.py"
    checks.append(AcceptanceCheck(
        code="xml.model_py_not_skeleton",
        passed=model_py.exists() and model_py.stat().st_size > 10000,
        message=f"model.py={model_py.stat().st_size if model_py.exists() else 0} bytes",
        level="D",
    ))

    return checks


def check_runtime_level(smoke: dict[str, Any]) -> list[AcceptanceCheck]:
    """Level E: Runtime checks."""
    checks: list[AcceptanceCheck] = []

    checks.append(AcceptanceCheck(
        code="rt.smoke_returncode",
        passed=smoke.get("returncode") == 0,
        message=f"returncode={smoke.get('returncode')}",
        level="E",
    ))

    keff = smoke.get("keff")
    keff_std = smoke.get("keff_std")
    checks.append(AcceptanceCheck(
        code="rt.keff_finite",
        passed=keff is not None and keff > 0 and keff < 10,
        message=f"keff={keff}",
        level="E",
    ))
    checks.append(AcceptanceCheck(
        code="rt.keff_std_finite",
        passed=keff_std is not None and keff_std >= 0,
        message=f"keff_std={keff_std}",
        level="E",
    ))

    lost = smoke.get("lost_particles", 0)
    checks.append(AcceptanceCheck(
        code="rt.zero_lost",
        passed=lost == 0,
        message=f"lost={lost}",
        level="E",
    ))

    return checks


__all__ = [
    "AcceptanceCheck",
    "AcceptanceResult",
    "check_facts_level",
    "check_plan_level",
    "check_geometry_level",
    "check_grid_geometry_level",
    "check_fuel_fidelity_level",
    "check_xml_level",
    "check_runtime_level",
    "run_full_acceptance",
]
