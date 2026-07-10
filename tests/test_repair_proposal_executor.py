from openmc_agent.repair_proposal import (
    FakeRepairProposalClient, RepairProposalMode, RepairValidationSnapshot, run_repair_proposal_flow,
)
from openmc_agent.schemas import ValidationReport


def snapshot(codes):
    return RepairValidationSnapshot(schema_valid=True, issue_codes=codes, blocking_issue_codes=[], warning_issue_codes=[])


def test_safe_patch_validate_only_applies_clone_and_accepts(monkeypatch):
    calls = []
    def fake_validate(plan, *, context=None):
        calls.append(plan)
        return snapshot(context.get("extra_issue_codes", []) if context else [])
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", fake_validate)
    result = run_repair_proposal_flow(
        plan={"materials":[{"composition_status":"confirmed"}]}, validation_result={"issue_codes":["audit.material.nominal_reported_as_confirmed"]},
        mode=RepairProposalMode.VALIDATE_ONLY, client=FakeRepairProposalClient(),
        context={"extra_issue_codes":["audit.material.nominal_reported_as_confirmed"], "after_extra_issue_codes": []},
    )
    assert result.status == "accepted"
    assert result.applied_to_clone is True
    assert result.applied_to_workflow_plan is False
    assert calls[0]["materials"][0]["composition_status"] == "confirmed"


def test_proposal_only_never_applies_workflow_plan(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: snapshot(["audit.material.nominal_reported_as_confirmed"]))
    result = run_repair_proposal_flow(plan={"materials":[{"composition_status":"confirmed"}]}, validation_result={"issue_codes":["audit.material.nominal_reported_as_confirmed"]}, mode=RepairProposalMode.PROPOSAL_ONLY, client=FakeRepairProposalClient())
    assert result.status == "proposed"
    assert result.applied_to_workflow_plan is False


def test_apply_if_safe_marks_workflow_application(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: snapshot(context.get("extra_issue_codes", []) if context else []))
    result = run_repair_proposal_flow(plan={"materials":[{"composition_status":"confirmed"}]}, validation_result={"issue_codes":["audit.material.nominal_reported_as_confirmed"]}, mode=RepairProposalMode.APPLY_IF_SAFE, client=FakeRepairProposalClient(), context={"extra_issue_codes":["audit.material.nominal_reported_as_confirmed"], "after_extra_issue_codes": []})
    assert result.status == "accepted"
    assert result.applied_to_workflow_plan is True


def test_target_issue_not_improved_rejected(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: snapshot(["audit.material.nominal_reported_as_confirmed"]))
    result = run_repair_proposal_flow(plan={"materials":[{"composition_status":"confirmed"}]}, validation_result={"issue_codes":["audit.material.nominal_reported_as_confirmed"]}, mode=RepairProposalMode.VALIDATE_ONLY, client=FakeRepairProposalClient())
    assert result.status == "rejected"
    assert "repair.target_issue_not_improved" in result.rejection_reasons


def test_new_blocking_issue_rejected(monkeypatch):
    def fake_validate(plan, *, context=None):
        codes = context.get("extra_issue_codes", []) if context else []
        return RepairValidationSnapshot(schema_valid=True, issue_codes=codes, blocking_issue_codes=[c for c in codes if c == "new.blocking"], warning_issue_codes=[])
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", fake_validate)
    result = run_repair_proposal_flow(plan={"materials":[{"composition_status":"confirmed"}]}, validation_result={"issue_codes":["audit.material.nominal_reported_as_confirmed"]}, mode=RepairProposalMode.VALIDATE_ONLY, client=FakeRepairProposalClient(), context={"extra_issue_codes":["audit.material.nominal_reported_as_confirmed"], "after_extra_issue_codes":["new.blocking"]})
    assert result.status == "rejected"
    assert "repair.new_blocking_issue" in result.rejection_reasons
