"""R7/R8 runtime campaign metrics and resume tests."""

from __future__ import annotations

from openmc_agent.runtime_metrics import (
    aggregate_fault_matrix,
    aggregate_real_campaign,
    real_campaign_status,
)


def test_fault_matrix_all_pass():
    results = [
        {"passed": True, "unsafe_accepted_count": 0, "artifact_complete": True},
        {"passed": True, "unsafe_accepted_count": 0, "artifact_complete": True},
    ]
    m = aggregate_fault_matrix(results)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PASSED"
    assert m["pass_rate"] == 1.0


def test_fault_matrix_with_unsafe_fails():
    results = [
        {"passed": True, "unsafe_accepted_count": 1, "artifact_complete": True},
    ]
    m = aggregate_fault_matrix(results)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_FAILED"


def test_fault_matrix_pending_excluded():
    results = [
        {"passed": True, "unsafe_accepted_count": 0, "artifact_complete": True},
        {"final_disposition": "pending_real_openmc"},
    ]
    m = aggregate_fault_matrix(results)
    assert m["evaluated_count"] == 1
    assert m["pending_count"] == 1
    assert m["pass_rate"] == 1.0
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PASSED"


def test_real_campaign_no_key_not_run():
    m = aggregate_real_campaign([], requested_runs=10)
    assert real_campaign_status(m, real_environment_available=False) == "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"


def test_real_campaign_accepted():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": True, "runtime_iterations": 0}
    ] * 10
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True) == "VERA3B_REAL_LLM_STABILITY_ACCEPTED"


def test_real_campaign_failed_unsafe():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 1, "artifact_complete": True, "runtime_iterations": 0}
    ] * 10
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert real_campaign_status(m, real_environment_available=True) == "VERA3B_REAL_LLM_STABILITY_FAILED"
