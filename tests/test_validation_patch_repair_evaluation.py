from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.plan_builder.validation_repair import (
    PatchRepairOperation, PatchRepairProposal, build_patch_repair_request,
    evaluate_patch_repair_proposal, stable_json_hash,
)
from openmc_agent.plan_builder.validation_repair_policy import policy_for_issue_code
from openmc_agent.schemas import SimulationPlan
from openmc_agent.schemas import ValidationIssue, ValidationReport
from openmc_agent.validator import validate_simulation_plan


def _broken_state() -> tuple[PlanBuildState, object]:
    raw = json.loads((Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text())
    state = PlanBuildState(state_id="repair", requirement_text="VERA3 3A")
    for payload in raw["patches"]:
        content = dict(payload)
        if content["patch_type"] == "pin_map":
            content["default_universe_id"] = "fuel_pin_endplug"
        env = PlanPatchEnvelope(patch_id=content["patch_type"], patch_type=content["patch_type"], content=content, status="valid")
        state.add_patch(env)
    assembled = assemble_simulation_plan_from_patches([parse_patch_content(p.patch_type, p.content) for p in state.patches.values()])
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    # The fixture is structurally complete; model the validator issue that the
    # original VERA transcript reports for its wrong base universe.
    report = ValidationReport.from_issues([ValidationIssue(
        severity="error", code="lattice.pin_count_mismatch",
        schema_path="complex_model.lattices.assembly_lattice.universe_pattern",
        message="base lattice uses fuel_pin_endplug instead of fuel_pin",
    )])
    return state, report


def _request(state, report):
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    return build_patch_repair_request(state=state, report=report, target_patch_type="pin_map", allowed_path_patterns=policy.allowed_path_patterns, forbidden_path_patterns=[])


def test_allowed_candidate_is_evaluated_on_clone_and_accepted() -> None:
    state, report = _broken_state()
    request = _request(state, report)
    before = state.patches["pin_map"].content["default_universe_id"]
    proposal = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[PatchRepairOperation(op="replace", path="/default_universe_id", value="fuel_pin")], rationale="correct base pin", confidence=1.0)
    evaluation = evaluate_patch_repair_proposal(state=state, request=request, proposal=proposal, requirement=state.requirement_text)
    assert evaluation.accepted is True
    assert state.patches["pin_map"].content["default_universe_id"] == before


def test_protected_path_and_duplicate_candidate_are_rejected() -> None:
    state, report = _broken_state()
    request = _request(state, report)
    unsafe = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[PatchRepairOperation(op="add", path="/materials/0/density_g_cm3", value=1)], rationale="unsafe", confidence=0.1)
    assert evaluate_patch_repair_proposal(state=state, request=request, proposal=unsafe, requirement="x").status == "rejected_unsafe_path"
    fixed = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[PatchRepairOperation(op="replace", path="/default_universe_id", value="fuel_pin")], rationale="fix", confidence=1.0)
    candidate = dict(request.previous_patch_content, default_universe_id="fuel_pin")
    duplicate = request.model_copy(update={"prior_candidate_hashes": [stable_json_hash(candidate)]})
    assert evaluate_patch_repair_proposal(state=state, request=duplicate, proposal=fixed, requirement="x").status == "rejected_duplicate_candidate"


def test_no_operations_is_no_progress() -> None:
    state, report = _broken_state()
    request = _request(state, report)
    empty = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[], rationale="none", confidence=0.0)
    assert evaluate_patch_repair_proposal(state=state, request=request, proposal=empty, requirement="x").status == "rejected_no_improvement"
