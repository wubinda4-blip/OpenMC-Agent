import pytest


def test_accepted_repair_does_not_require_graph_retry_increment(monkeypatch) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _make_validate_plan_node
    from openmc_agent.schemas import ValidationIssue
    from openmc_agent.plan_builder.validation_repair import PatchRepairEvaluation
    from tests.test_workflow_trace import _complex_plan_with_pin_count_mismatch

    repaired = _complex_plan_with_pin_count_mismatch().model_copy(deep=True)
    repaired.complex_model.lattices[0].universe_pattern = [["fuel_pin", "guide_tube"], ["fuel_pin", "instrument_tube"]]
    evaluation = PatchRepairEvaluation(accepted=True, status="accepted", issues_before=["lattice.pin_count_mismatch"], issues_after=[], resolved_issue_codes=["lattice.pin_count_mismatch"], introduced_issue_codes=[], issue_fingerprint_before="f", repaired_plan=repaired.model_dump(mode="json"))
    monkeypatch.setattr("openmc_agent.graph._try_incremental_validation_patch_repair", lambda **_kw: (None, evaluation, {"status": "accepted"}))
    updates = _make_validate_plan_node(2)({"simulation_plan": _complex_plan_with_pin_count_mismatch(), "requirement": "assembly", "retry_count": 0, "plan_build_state": {"patches": {"x": {}}}, "incremental_execution_result": {"planning_mode": "incremental", "monolithic_reflect_plan_allowed": False}})
    assert updates["retry_count"] == 0
    assert updates["incremental_patch_repair_accepted"] is True
