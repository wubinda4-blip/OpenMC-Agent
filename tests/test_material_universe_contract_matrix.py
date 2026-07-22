"""Phase 4: Material-Universe contract matrix (four row kinds)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_binding import build_material_universe_binding_view
from openmc_agent.plan_builder.closed_loop.material_universe_evidence import build_material_universe_contract_matrix
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-matrix", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly", "fuel_variant_requirements": [{"variant_id": "v1", "enrichment_wt_percent": 3.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [
        {"material_id": "fuel_v1", "name": "f", "role": "fuel", "density_g_cm3": 10.0, "source_variant_id": "v1"},
        {"material_id": "clad", "name": "c", "role": "cladding", "density_g_cm3": 6.5},
    ]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [
            {"id": "pellet", "role": "fuel", "material_id": "fuel_v1", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4},
            {"id": "clad", "role": "clad", "material_id": "clad", "region_kind": "annulus", "r_min_cm": 0.4, "r_max_cm": 0.45},
            {"id": "bg", "role": "background", "material_id": "clad", "region_kind": "background"},
        ]},
    ]}, status="valid"))
    return state


def test_contract_matrix_has_four_row_kinds() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    matrix = build_material_universe_contract_matrix(view)
    kinds = {r.row_kind for r in matrix.rows}
    assert "source_material_coverage" in kinds
    assert "material_to_cell_binding" in kinds
    assert "fuel_variant_identity" in kinds
    assert "required_universe_material_structure" in kinds


def test_source_material_coverage_row_for_fuel_variant() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    matrix = build_material_universe_contract_matrix(view)
    fuel_rows = [r for r in matrix.rows if r.row_kind == "source_material_coverage" and r.material_role == "fuel"]
    assert fuel_rows
    assert fuel_rows[0].coverage_status == "pass"


def test_source_material_coverage_uses_matching_variant_material_id() -> None:
    state = PlanBuildState(state_id="mu-matrix-variant", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly", "fuel_variant_requirements": [
        {"variant_id": "region_1", "enrichment_wt_percent": 2.11},
        {"variant_id": "region_2", "enrichment_wt_percent": 2.619},
    ]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [
        {"material_id": "fuel_region_1", "name": "r1", "role": "fuel", "density_g_cm3": 10.0, "source_variant_id": "region_1"},
        {"material_id": "fuel_region_2", "name": "r2", "role": "fuel", "density_g_cm3": 10.0, "source_variant_id": "region_2"},
    ]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [
            {"id": "pellet", "role": "fuel", "material_id": "fuel_region_2", "region_kind": "cylinder"}
        ]},
    ]}, status="valid"))

    view = build_material_universe_binding_view(state=state)
    matrix = build_material_universe_contract_matrix(view)
    rows = {row.row_id: row for row in matrix.rows}

    assert rows["smc:fuel_variant:region_1"].material_id == "fuel_region_1"
    assert rows["smc:fuel_variant:region_2"].material_id == "fuel_region_2"


def test_fuel_variant_identity_row_passes_when_material_and_universe_exist() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    matrix = build_material_universe_contract_matrix(view)
    variant_rows = [r for r in matrix.rows if r.row_kind == "fuel_variant_identity"]
    assert variant_rows
    assert variant_rows[0].variant_id == "v1"
    assert variant_rows[0].coverage_status == "pass"


def test_required_universe_material_structure_detects_missing_fuel_cell() -> None:
    state = PlanBuildState(state_id="mu-matrix-fail", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "clad", "name": "c", "role": "cladding", "density_g_cm3": 6.5}]}, status="valid"))
    # A fuel_pin universe with no fuel cell → structure row should fail.
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [
        {"universe_id": "bad_fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "clad", "material_id": "clad", "region_kind": "annulus"}]},
    ]}, status="valid"))
    view = build_material_universe_binding_view(state=state)
    matrix = build_material_universe_contract_matrix(view)
    struct_rows = [r for r in matrix.rows if r.row_kind == "required_universe_material_structure" and r.universe_id == "bad_fuel"]
    assert struct_rows
    assert struct_rows[0].coverage_status == "fail"


def test_input_hash_is_stable() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    m1 = build_material_universe_contract_matrix(view)
    m2 = build_material_universe_contract_matrix(view)
    assert m1.input_hash == m2.input_hash
