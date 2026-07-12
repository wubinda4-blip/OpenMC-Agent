"""Grid-loading repair diagnosis for the P0-D5B spacer-grid migration.

Tests :func:`diagnose_grid_loading_failure` against the VERA3B regression where
an LLM emitted two ``replace_universe_family`` transformations referencing an
undefined ``grid_cell`` while the plan already carries 8 correct spacer_grid
axial overlays.  The diagnosis must identify ``grid_cell`` as undefined, find
the overlapping overlays, and select the ``remove_redundant_grid_transformation``
repair branch.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.grid_loading_repair import (
    GridLoadingRepairDiagnosis,
    diagnose_grid_loading_failure,
)
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture(variant: str = "3a") -> list[dict]:
    path = _FIXTURE_DIR / f"vera3_{variant}_patches.json"
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data["patches"]
    return data


def _inject_grid_cell_regression(patches: list[dict]) -> list[dict]:
    out = [copy.deepcopy(p) for p in patches]
    for p in out:
        if p.get("patch_type") != "axial_layers":
            continue
        p.setdefault("lattice_loadings", []).append({
            "loading_id": "spacer_grid_loadings",
            "base_lattice_id": "assembly_lattice",
            "derived_lattice_id": "assembly_lattice_grid",
            "transformations": [
                {
                    "operation_id": "replace_water_with_grid",
                    "operation_kind": "replace_universe_family",
                    "replacement_universe_id": "grid_cell",
                    "source_universe_id": "guide_tube",
                    "purpose": "Replace water with spacer grid",
                }
            ],
            "purpose": "Redundant spacer grid loading",
        })
        p["lattice_loadings"].append({
            "loading_id": "top_grid_loading",
            "base_lattice_id": "assembly_lattice",
            "derived_lattice_id": "assembly_lattice_top_grid",
            "transformations": [
                {
                    "operation_id": "replace_water_with_top_grid",
                    "operation_kind": "replace_universe_family",
                    "replacement_universe_id": "grid_cell",
                    "source_universe_id": "guide_tube",
                    "purpose": "Replace water with top spacer grid",
                }
            ],
            "purpose": "Redundant top spacer grid loading",
        })
        for layer in p.get("layers", []):
            if layer["layer_id"] == "active_fuel_pyrex_span":
                layer["loading_id"] = None
                layer["loading_ids"] = ["pyrex_active_loading", "spacer_grid_loadings"]
            elif layer["layer_id"] == "upper_plenum_middle_thimble":
                layer["loading_ids"] = [
                    "plenum_loading", "thimble_plug_loading", "top_grid_loading",
                ]
    return out


def _build_state(variant: str = "3a") -> tuple[PlanBuildState, SimulationPlan]:
    patches = _load_fixture(variant)
    if variant == "3b":
        patches = _inject_grid_cell_regression(patches)
    state = PlanBuildState(state_id="test", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"],
            patch_type=content["patch_type"],
            content=content, status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches(
        [parse_patch_content(p.patch_type, p.content) for p in state.patches.values()],
        strict=True,
    )
    assert assembled.ok and assembled.plan is not None, (
        f"fixture {variant!r} failed to assemble: {assembled.summary}"
    )
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    return state, assembled.plan


def _diagnose_3b() -> GridLoadingRepairDiagnosis:
    state, plan = _build_state("3b")
    report = validate_simulation_plan(plan, requirement="generic assembly")
    diagnosis = diagnose_grid_loading_failure(
        state=state, plan=plan, issues=report.issues,
    )
    assert diagnosis is not None, "expected a diagnosis for the 3b grid regression"
    return diagnosis


def test_diagnosis_identifies_grid_cell_as_undefined() -> None:
    diagnosis = _diagnose_3b()
    op = diagnosis.operations[0]
    assert op.replacement_is_universe is False, (
        f"grid_cell should not be a universe: {op.replacement_universe_id!r}"
    )
    assert op.replacement_is_cell is False, (
        f"grid_cell should not be a cell: {op.replacement_universe_id!r}"
    )
    assert op.replacement_is_material is False, (
        f"grid_cell should not be a material: {op.replacement_universe_id!r}"
    )


def test_diagnosis_finds_existing_grid_overlays() -> None:
    diagnosis = _diagnose_3b()
    assert diagnosis.operations[0].matching_overlay_ids, (
        "expected overlapping spacer_grid overlays for the first operation"
    )


def test_diagnosis_repair_kind_is_remove_redundant() -> None:
    diagnosis = _diagnose_3b()
    assert diagnosis.repair_kind == "remove_redundant_grid_transformation", (
        f"expected remove_redundant_grid_transformation, got {diagnosis.repair_kind!r}"
    )


def test_diagnosis_identifies_both_operations() -> None:
    diagnosis = _diagnose_3b()
    op_ids = {op.operation_id for op in diagnosis.operations}
    assert op_ids == {"replace_water_with_grid", "replace_water_with_top_grid"}, (
        f"expected both grid operations, got {op_ids}"
    )
    assert len(diagnosis.operations) == 2, (
        f"expected exactly 2 operations, got {len(diagnosis.operations)}"
    )


def test_diagnosis_finds_layers_using_loading() -> None:
    diagnosis = _diagnose_3b()
    assert diagnosis.operations[0].layers_using_loading, (
        "expected at least one layer using the grid loading"
    )


def test_diagnosis_returns_none_for_clean_plan() -> None:
    state, plan = _build_state("3a")
    report = validate_simulation_plan(plan, requirement="generic assembly")
    diagnosis = diagnose_grid_loading_failure(
        state=state, plan=plan, issues=report.issues,
    )
    assert diagnosis is None, "clean 3a plan should yield no grid diagnosis"


def test_diagnosis_facts_has_spacer_grids() -> None:
    diagnosis = _diagnose_3b()
    assert diagnosis.operations[0].facts_has_spacer_grids is True, (
        "facts.has_spacer_grids should be True for the 3B fixture"
    )
