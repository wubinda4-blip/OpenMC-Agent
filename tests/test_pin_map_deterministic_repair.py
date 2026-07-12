from __future__ import annotations

from openmc_agent.plan_builder.pin_map_repair import diagnose_pin_map_count_mismatch
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.validation_repair import PatchRepairProposal, build_patch_repair_request, evaluate_patch_repair_proposal
from openmc_agent.plan_builder.validation_repair_policy import policy_for_issue_code
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan

from tests.test_pin_map_repair_diagnosis import _state_and_mismatch


def test_deterministic_default_repair_passes_clone_acceptance() -> None:
    state, plan, report, _replacement_id = _state_and_mismatch()
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    request = build_patch_repair_request(
        state=state, report=report, target_patch_type="pin_map",
        allowed_path_patterns=policy.allowed_path_patterns, forbidden_path_patterns=[],
    )
    diagnosis = diagnose_pin_map_count_mismatch(
        state=state, plan=plan, report=report,
        target_patch=parse_patch_content("pin_map", state.patches["pin_map"].content),
    )
    proposal = PatchRepairProposal(
        repair_id=request.repair_id, target_patch_type="pin_map", operations=diagnosis.deterministic_operations,
        rationale="The default lattice universe was an axial profile replacement; the expected base universe has an equal and opposite count delta matching all default positions.",
        confidence=1.0,
    )
    evaluation = evaluate_patch_repair_proposal(state=state, request=request, proposal=proposal, requirement="generic assembly")
    assert evaluation.accepted is True
    assert evaluation.candidate_preview["actual_counts"]["fuel_pin"] == 264
    repaired = evaluation.repaired_plan
    assert not any(
        issue.code == "lattice.pin_count_mismatch"
        for issue in validate_simulation_plan(SimulationPlan.model_validate(repaired)).issues
    )


def test_pin_map_preflight_rejects_an_unchanged_candidate() -> None:
    state, _plan, report, replacement_id = _state_and_mismatch()
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    request = build_patch_repair_request(
        state=state, report=report, target_patch_type="pin_map",
        allowed_path_patterns=policy.allowed_path_patterns, forbidden_path_patterns=[],
    )
    proposal = PatchRepairProposal(
        repair_id=request.repair_id, target_patch_type="pin_map",
        operations=[{"op": "replace", "path": "/default_universe_id", "value": replacement_id}],
        rationale="unchanged", confidence=0.0,
    )
    evaluation = evaluate_patch_repair_proposal(state=state, request=request, proposal=proposal, requirement="generic assembly")
    assert evaluation.status == "rejected_no_improvement"
    assert evaluation.reasons == ["candidate_preflight_no_effect"]
