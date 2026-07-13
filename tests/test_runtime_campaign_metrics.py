"""Runtime campaign metrics and promotion gate tests."""

from __future__ import annotations

from openmc_agent.runtime_metrics import (
    aggregate_fault_matrix,
    aggregate_real_campaign,
    real_campaign_status,
)


def _pass(case_id="F00", **kw):
    d = {"case_id": case_id, "passed": True, "final_disposition": "recovered",
         "unsafe_accepted_count": 0, "artifact_complete": True,
         "execution_backend": "real_openmc", "requires_real_openmc": True}
    d.update(kw)
    return d


def _pass_mocked(case_id="F02", **kw):
    d = {"case_id": case_id, "passed": True, "final_disposition": "safe_stop",
         "unsafe_accepted_count": 0, "artifact_complete": True,
         "execution_backend": "injected_tool", "requires_real_openmc": False}
    d.update(kw)
    return d


# --- Fault Matrix promotion gate ---


def test_fault_matrix_20_pass_is_passed():
    results = [_pass(f"F{i:02d}") if i == 0 else _pass_mocked(f"F{i:02d}") for i in range(20)]
    m = aggregate_fault_matrix(results)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PASSED"
    assert m["pass_rate"] == 1.0


def test_fault_matrix_pending_is_partial():
    results = [_pass()] + [_pass_mocked(f"F{i:02d}") for i in range(1, 20)]
    results.append({"case_id": "F20", "final_disposition": "pending_real_openmc"})
    m = aggregate_fault_matrix(results, required_case_count=21)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    assert m["pending_count"] == 1


def test_fault_matrix_unsafe_is_failed():
    results = [_pass(unsafe_accepted_count=1)]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_FAILED"


def test_fault_matrix_protected_change_is_failed():
    results = [_pass(protected_field_change_count=1)]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_FAILED"


def test_fault_matrix_failed_case_is_failed():
    results = [{"case_id": "F00", "passed": False, "final_disposition": "safe_stop",
                "artifact_complete": True}]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_FAILED"


def test_fault_matrix_incomplete_artifact_is_partial():
    results = [_pass(artifact_complete=False)]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"


def test_fault_matrix_mocked_real_case_is_partial():
    """A requires_real_openmc case using mocked backend → PARTIAL."""
    results = [_pass(execution_backend="injected_tool")]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    assert m["real_openmc_mocked_count"] == 1


def test_fault_matrix_under_required_count_is_partial():
    results = [_pass()] * 19
    m = aggregate_fault_matrix(results, required_case_count=20)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    assert "case_count=19 < required=20" in m["promotion_reasons"]


def test_fault_matrix_promotion_reasons_populated():
    results = [_pass(unsafe_accepted_count=1)]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert len(m["promotion_reasons"]) > 0
    assert any("unsafe" in r for r in m["promotion_reasons"])


# --- Real-LLM Campaign status ---


def test_real_campaign_no_key_not_run():
    m = aggregate_real_campaign([], requested_runs=10)
    assert real_campaign_status(m, real_environment_available=False) == "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"


def test_real_campaign_confirmation_required():
    m = aggregate_real_campaign([], requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True) == "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"


def test_real_campaign_executor_not_implemented():
    m = aggregate_real_campaign([], requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True, confirmed=True) == "VERA3B_REAL_LLM_EXECUTOR_NOT_IMPLEMENTED"


def test_real_campaign_pilot_pending_zero_runs():
    m = aggregate_real_campaign([], requested_runs=3)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_PILOT_PENDING"


def test_real_campaign_pilot_passed_3():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": True, "runtime_iterations": 0}
    ] * 3
    m = aggregate_real_campaign(runs, requested_runs=3)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_PILOT_PASSED"


def test_real_campaign_stability_accepted():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": True, "runtime_iterations": 0}
    ] * 10
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_STABILITY_ACCEPTED"


def test_real_campaign_stability_failed_unsafe():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 1, "artifact_complete": True, "runtime_iterations": 0}
    ] * 10
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_STABILITY_FAILED"
