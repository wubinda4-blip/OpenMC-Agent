from openmc_agent.plan_builder.validation_repair import (
    PatchRepairOperation, PatchRepairProposal, compute_validation_issue_fingerprint,
)
from openmc_agent.schemas import ValidationIssue, ValidationReport


def test_issue_fingerprint_uses_only_stable_issue_fields() -> None:
    first = ValidationReport.from_issues([ValidationIssue(severity="error", code="x", message="first wording", schema_path="core.a[0]")])
    second = ValidationReport.from_issues([ValidationIssue(severity="error", code="x", message="different wording", schema_path="core.a[1]")])
    assert compute_validation_issue_fingerprint(first, target_patch_type="pin_map") == compute_validation_issue_fingerprint(second, target_patch_type="pin_map")


def test_patch_repair_models_accept_rfc6902_subset() -> None:
    proposal = PatchRepairProposal(repair_id="r", target_patch_type="pin_map", operations=[PatchRepairOperation(op="replace", path="/default_universe_id", value="fuel")], rationale="fix", confidence=0.8)
    assert proposal.operations[0].op == "replace"
