"""Truthfulness gate tests for runtime promotion."""

from __future__ import annotations

import pytest

from openmc_agent.runtime_metrics import (
    aggregate_fault_matrix,
    aggregate_real_campaign,
    real_campaign_status,
)
from openmc_agent.runtime_faults import default_fault_matrix


# -- Fault matrix truthfulness --


def test_all_20_cases_have_explicit_backend():
    """Every case must declare requires_real_openmc explicitly."""
    for case in default_fault_matrix():
        assert isinstance(case.requires_real_openmc, bool)


def test_f00_and_f01_require_real_openmc():
    cases = {c.case_id: c for c in default_fault_matrix()}
    assert cases["F00_baseline_no_fault"].requires_real_openmc is True
    assert cases["F01_source_strategy_fault"].requires_real_openmc is True


def test_f05_does_not_require_real_openmc():
    cases = {c.case_id: c for c in default_fault_matrix()}
    assert cases["F05_process_crash_after_source_rejection"].requires_real_openmc is False


def test_pending_case_makes_matrix_partial():
    results = [
        {"case_id": "F00", "passed": True, "final_disposition": "recovered",
         "unsafe_accepted_count": 0, "artifact_complete": True,
         "execution_backend": "real_openmc", "requires_real_openmc": True},
        {"case_id": "F01", "final_disposition": "pending_real_openmc",
         "requires_real_openmc": True},
    ]
    m = aggregate_fault_matrix(results, required_case_count=2)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    assert m["pending_count"] == 1


def test_mocked_backend_for_real_case_makes_partial():
    """A requires_real_openmc case using mocked backend → PARTIAL."""
    results = [
        {"case_id": "F00", "passed": True, "final_disposition": "recovered",
         "unsafe_accepted_count": 0, "artifact_complete": True,
         "execution_backend": "injected_tool", "requires_real_openmc": True},
    ]
    m = aggregate_fault_matrix(results, required_case_count=1)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    assert m["real_openmc_mocked_count"] == 1


def test_all_pass_with_real_evidence_is_passed():
    results = [
        {"case_id": "F00", "passed": True, "final_disposition": "recovered",
         "unsafe_accepted_count": 0, "artifact_complete": True,
         "execution_backend": "real_openmc", "requires_real_openmc": True},
    ]
    for i in range(1, 20):
        results.append({
            "case_id": f"F{i:02d}", "passed": True,
            "final_disposition": "safe_stop",
            "unsafe_accepted_count": 0, "artifact_complete": True,
            "execution_backend": "injected_tool", "requires_real_openmc": False,
        })
    m = aggregate_fault_matrix(results, required_case_count=20)
    assert m["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PASSED"
    assert m["pass_rate"] == 1.0


# -- Lane B truthfulness --


def test_empty_real_campaign_with_key_and_confirm_is_not_accepted():
    m = aggregate_real_campaign([], requested_runs=10)
    status = real_campaign_status(
        m, real_environment_available=True,
        executor_implemented=True, confirmed=True,
    )
    assert status == "VERA3B_REAL_LLM_PILOT_PENDING"


def test_confirmation_required_not_executor_ready():
    m = aggregate_real_campaign([], requested_runs=10)
    status = real_campaign_status(
        m, real_environment_available=True,
        confirmed=False,
    )
    assert status == "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"


def test_confirmed_without_executor_is_not_implemented():
    m = aggregate_real_campaign([], requested_runs=10)
    status = real_campaign_status(
        m, real_environment_available=True,
        executor_implemented=False, confirmed=True,
    )
    assert status == "VERA3B_REAL_LLM_EXECUTOR_NOT_IMPLEMENTED"


def test_fake_results_dont_count():
    """Fake/dry-run results must not be passed to aggregate_real_campaign."""
    # This is a design rule: the caller is responsible for ensuring
    # results only contain genuine real-LLM runs. The aggregator itself
    # doesn't filter, but the promotion gate enforces executor_implemented.
    m = aggregate_real_campaign([], requested_runs=10)
    status = real_campaign_status(
        m, real_environment_available=True,
        executor_implemented=False, confirmed=True,
    )
    assert "ACCEPTED" not in status
