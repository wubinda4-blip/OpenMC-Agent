"""Graph-level integration tests for the deterministic grid migration oracle.

Exercises ``_try_incremental_validation_patch_repair`` against the VERA3B grid
regression: the deterministic grid migration oracle must fire, accept the
repair, and return the ``deterministic_grid_migration`` strategy without ever
calling an LLM.  A clean VERA3A plan (no grid issue) must skip the oracle.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from openmc_agent.graph import _try_incremental_validation_patch_repair
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture(variant: str = "3b") -> list[dict]:
    path = _FIXTURE_DIR / f"vera3_{variant}_patches.json"
    with open(path) as f:
        return json.load(f)["patches"]


def _inject_grid_loadings(patches: list[dict]) -> list[dict]:
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


def _build_state(
    variant: str = "3b",
    inject_grid: bool = True,
) -> tuple[PlanBuildState, SimulationPlan, "ValidationReport"]:  # type: name-error
    patches = _load_fixture(variant)
    if inject_grid:
        patches = _inject_grid_loadings(patches)
    state = PlanBuildState(state_id="test-grid-graph", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"],
            patch_type=content["patch_type"],
            content=content,
            status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches(
        [parse_patch_content(p.patch_type, p.content) for p in state.patches.values()],
        strict=True,
    )
    assert assembled.ok and assembled.plan is not None, (
        f"fixture {variant!r} failed to assemble: {assembled.summary}"
    )
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    plan = assembled.plan
    report = validate_simulation_plan(plan, requirement="generic assembly")
    return state, plan, report


def _graph_state(state: PlanBuildState, plan: SimulationPlan, tmp_path) -> dict:
    return {
        "plan_build_state": state.model_dump(mode="json"),
        "simulation_plan": plan,
        "requirement": "generic assembly",
        "output_dir": str(tmp_path),
        "model": "fake",
    }


def test_grid_migration_oracle_in_repair_chain(tmp_path) -> None:
    state, plan, report = _build_state("3b", inject_grid=True)

    _repaired, evaluation, meta = _try_incremental_validation_patch_repair(
        state=_graph_state(state, plan, tmp_path),
        report=report,
        target_patch_types=["axial_layers"],
        llm_client=None,
    )
    assert meta["status"] == "accepted", f"expected accepted, got {meta!r}"
    assert meta["strategy"] == "deterministic_grid_migration", (
        f"expected deterministic_grid_migration, got {meta.get('strategy')!r}"
    )
    assert evaluation is not None and evaluation.accepted


def test_grid_migration_does_not_require_llm_client(tmp_path) -> None:
    state, plan, report = _build_state("3b", inject_grid=True)

    class NeverCalled:
        def generate_patch_json(self, **_kwargs):
            raise AssertionError("deterministic grid migration must not call the LLM")

    _repaired, evaluation, meta = _try_incremental_validation_patch_repair(
        state=_graph_state(state, plan, tmp_path),
        report=report,
        target_patch_types=["axial_layers"],
        llm_client=NeverCalled(),
    )
    assert meta["status"] == "accepted"
    assert meta["strategy"] == "deterministic_grid_migration"
    assert evaluation is not None and evaluation.accepted


def test_clean_plan_skips_grid_oracle(tmp_path) -> None:
    state, plan, report = _build_state("3a", inject_grid=False)

    grid_error_codes = {
        "lattice_transform.replacement_universe_missing",
        "lattice_transform.cell_id_used_as_universe",
        "assembly3d.spacer_grid_transformation_misuse",
        "lattice_transform.source_universe_missing",
    }
    assert not any(i.code in grid_error_codes for i in report.issues if i.severity == "error"), (
        "clean 3a fixture should have no grid/lattice error issues"
    )

    _repaired, _evaluation, meta = _try_incremental_validation_patch_repair(
        state=_graph_state(state, plan, tmp_path),
        report=report,
        target_patch_types=["axial_layers"],
        llm_client=None,
    )
    assert meta.get("strategy") != "deterministic_grid_migration", (
        "clean plan must not trigger the grid migration oracle"
    )
    assert meta.get("status") in ("unavailable", "requires_human_confirmation"), (
        f"clean plan should fall through, got {meta!r}"
    )
