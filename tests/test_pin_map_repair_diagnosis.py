from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.pin_map_repair import diagnose_pin_map_count_mismatch, preview_pin_map_candidate_counts
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import ValidationIssue, ValidationReport


def _state_and_mismatch():
    raw = json.loads((Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text())
    state = PlanBuildState(state_id="pin-map", requirement_text="generic assembly")
    for payload in raw["patches"]:
        content = dict(payload)
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"], patch_type=content["patch_type"], content=content, status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches([
        parse_patch_content(item.patch_type, item.content) for item in state.patches.values()
    ], strict=True)
    assert assembled.ok and assembled.plan is not None
    axial_payload = next(item for item in raw["patches"] if item["patch_type"] == "axial_layers")
    replacement_id = axial_payload["lattice_loadings"][0]["transformations"][0]["replacement_universe_id"]
    # Model the pre-fix assembled output while retaining a complete plan. The
    # target patch is the profile default that produced it in the old path.
    lattice = assembled.plan.complex_model.lattices[0]
    lattice.universe_pattern = [
        [replacement_id if item == "fuel_pin" else item for item in row]
        for row in lattice.universe_pattern
    ]
    state.patches["pin_map"].content["default_universe_id"] = replacement_id
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    report = ValidationReport.from_issues([ValidationIssue(
        code="lattice.pin_count_mismatch", severity="error",
        schema_path="complex_model.lattices.assembly_lattice.universe_pattern", message="counts differ",
    )])
    return state, assembled.plan, report, replacement_id


def test_equal_opposite_default_deltas_are_diagnosed() -> None:
    state, plan, report, replacement_id = _state_and_mismatch()
    target = parse_patch_content("pin_map", state.patches["pin_map"].content)
    diagnosis = diagnose_pin_map_count_mismatch(state=state, plan=plan, report=report, target_patch=target)
    assert diagnosis.default_position_count == 264
    assert diagnosis.expected_counts["fuel_pin"] == 264
    assert diagnosis.actual_counts[replacement_id] == 264
    assert diagnosis.deterministic_repair_available is True
    assert diagnosis.deterministic_operations[0].path == "/default_universe_id"
    assert diagnosis.deterministic_operations[0].value == "fuel_pin"
    assert replacement_id in diagnosis.axial_profile_replacement_ids


def test_real_profile_swap_regression_fixture_matches_oracle_shape() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/regressions/pin_map_default_universe_profile_swap.json").read_text())
    state, plan, report, _replacement_id = _state_and_mismatch()
    target = parse_patch_content("pin_map", state.patches["pin_map"].content)
    diagnosis = diagnose_pin_map_count_mismatch(state=state, plan=plan, report=report, target_patch=target)
    assert fixture["default_position_count"] == diagnosis.default_position_count
    assert fixture["expected_operation"] == diagnosis.deterministic_operations[0].model_dump(mode="json")


def test_preview_matches_assembler_base_pattern() -> None:
    state, _plan, _report, replacement_id = _state_and_mismatch()
    candidate = parse_patch_content("pin_map", {**state.patches["pin_map"].content, "default_universe_id": "fuel_pin"})
    preview = preview_pin_map_candidate_counts(state=state, candidate_patch=candidate)
    assert preview["ok"] is True
    assert preview["actual_counts"]["fuel_pin"] == 264
    assert replacement_id not in preview["actual_counts"]


def test_ambiguous_or_unequal_deltas_do_not_auto_repair() -> None:
    state, plan, report, _replacement_id = _state_and_mismatch()
    plan = plan.model_copy(deep=True)
    plan.complex_model.lattices[0].universe_pattern[0][0] = "guide_tube"
    target = parse_patch_content("pin_map", state.patches["pin_map"].content)
    diagnosis = diagnose_pin_map_count_mismatch(state=state, plan=plan, report=report, target_patch=target)
    assert diagnosis.deterministic_repair_available is False
