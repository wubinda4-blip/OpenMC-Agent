from __future__ import annotations

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.validation_repair import PatchRepairProposal, build_patch_repair_request, evaluate_patch_repair_proposal
from openmc_agent.plan_builder.validation_repair_policy import policy_for_issue_code
from openmc_agent.schemas import ValidationIssue, ValidationReport

from tests.test_pin_map_repair_diagnosis import _state_and_mismatch


def test_existing_warning_is_not_an_introduced_issue() -> None:
    state, _plan, _report, _replacement_id = _state_and_mismatch()
    report = ValidationReport.from_issues([
        ValidationIssue(code="lattice.pin_count_mismatch", severity="error", schema_path="complex_model.lattices.assembly_lattice.universe_pattern", message="counts"),
        ValidationIssue(code="plan.complex_model.non_executable", severity="warning", schema_path="capability_report", message="existing"),
    ])
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    request = build_patch_repair_request(state=state, report=report, target_patch_type="pin_map", allowed_path_patterns=policy.allowed_path_patterns, forbidden_path_patterns=[])
    proposal = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}], rationale="base", confidence=1.0)
    evaluation = evaluate_patch_repair_proposal(state=state, request=request, proposal=proposal, requirement="generic assembly")
    assert evaluation.accepted is True
    assert "plan.complex_model.non_executable" not in evaluation.introduced_issue_codes
    assert not evaluation.introduced_warnings


def test_new_error_is_an_introduced_blocker(monkeypatch) -> None:
    state, _plan, _report, _replacement_id = _state_and_mismatch()
    report = ValidationReport.from_issues([
        ValidationIssue(code="lattice.pin_count_mismatch", severity="error", schema_path="complex_model.lattices.assembly_lattice.universe_pattern", message="counts"),
        ValidationIssue(code="plan.complex_model.non_executable", severity="warning", schema_path="capability_report", message="existing"),
    ])
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    request = build_patch_repair_request(state=state, report=report, target_patch_type="pin_map", allowed_path_patterns=policy.allowed_path_patterns, forbidden_path_patterns=[])
    proposal = PatchRepairProposal(repair_id=request.repair_id, target_patch_type="pin_map", operations=[{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}], rationale="base", confidence=1.0)

    def with_new_blocker(*_args, **_kwargs):
        return ValidationReport.from_issues([
            ValidationIssue(code="plan.complex_model.non_executable", severity="warning", schema_path="capability_report", message="existing"),
            ValidationIssue(code="new.blocker", severity="error", schema_path="complex_model", message="new"),
        ])

    monkeypatch.setattr("openmc_agent.plan_builder.validation_repair.validate_simulation_plan", with_new_blocker)
    evaluation = evaluate_patch_repair_proposal(state=state, request=request, proposal=proposal, requirement="generic assembly")
    assert evaluation.status == "rejected_new_blocker"
    assert evaluation.introduced_blockers == ["new.blocker|complex_model|error"]
    assert not evaluation.introduced_warnings
