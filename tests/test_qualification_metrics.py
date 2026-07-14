"""Tests for expanded qualification metrics (S10)."""

from __future__ import annotations

from openmc_agent.runtime_metrics import aggregate_real_campaign, real_campaign_status


def _success_run(**kw):
    d = {
        "final_disposition": "FIRST_PASS_SUCCESS",
        "status": "completed",
        "unsafe_proposal_count": 0,
        "unsafe_accepted_count": 0,
        "protected_field_change_count": 0,
        "artifact_complete": True,
        "runtime_iterations": 0,
        "committed_runtime_repairs": 0,
        "real_llm_verified": True,
        "real_openmc_verified": True,
        "vera3_acceptance_passed": True,
        "duration_s": 180,
        "llm_call_count": 7,
        "planning_network_call_count": 7,
        "llm_output_chars": 5000,
        "lost_particle_count": 0,
        "source_rejection_count": 0,
        "smoke_backend": "real_openmc",
        "deterministic_runtime_attempts": 0,
    }
    d.update(kw)
    return d


def _failed_run(**kw):
    d = {
        "final_disposition": "PLANNING_FAILURE",
        "status": "completed",
        "unsafe_proposal_count": 0,
        "artifact_complete": True,
        "runtime_iterations": 0,
        "duration_s": 60,
        "llm_call_count": 3,
        "lost_particle_count": 0,
        "source_rejection_count": 0,
    }
    d.update(kw)
    return d


def test_metrics_count_requested_and_completed():
    m = aggregate_real_campaign([_success_run()] * 10, requested_runs=10)
    assert m["requested_runs"] == 10
    assert m["completed_runs"] == 10


def test_metrics_first_pass_and_recovered():
    runs = [_success_run()] * 7 + [_success_run(final_disposition="RECOVERED_SUCCESS", committed_runtime_repairs=1, runtime_iterations=1)] * 3
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["first_pass_successes"] == 7
    assert m["recovered_successes"] == 3
    assert m["successful_runs"] == 10


def test_metrics_final_success_rate_7_of_10():
    runs = [_success_run()] * 7 + [_failed_run()] * 3
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["final_success_rate"] == 0.7


def test_metrics_wilson_interval():
    m = aggregate_real_campaign([_success_run()] * 10, requested_runs=10)
    w = m["final_success_wilson_95"]
    assert w["low"] > 0.5
    assert w["high"] <= 1.0


def test_metrics_autonomous_terminal_rate():
    runs = [_success_run()] * 8 + [_failed_run(final_disposition="SAFE_STOP_ENVIRONMENT")] * 2
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["autonomous_terminal_rate"] == 1.0


def test_metrics_bounded_outcome_rate():
    runs = [_success_run()] * 8 + [_success_run(committed_runtime_repairs=3)] * 2
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["bounded_outcome_rate"] == 0.8


def test_metrics_real_llm_verification_rate():
    runs = [_success_run()] * 8 + [_success_run(real_llm_verified=False)] * 2
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["real_llm_verification_rate"] == 0.8


def test_metrics_unsafe_upper_bound():
    runs = [_success_run(unsafe_accepted_count=1)] * 1 + [_success_run()] * 9
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["unsafe_acceptance_upper_95"] > 0


def test_metrics_cost_fields_present():
    m = aggregate_real_campaign([_success_run()] * 3, requested_runs=3)
    assert "total_llm_calls" in m
    assert "average_duration_s" in m
    assert "median_duration_s" in m
    assert "p95_duration_s" in m
    assert m["token_usage"] == "unavailable"
    assert m["cost"] == "unavailable"


def test_metrics_zero_runs():
    m = aggregate_real_campaign([], requested_runs=10)
    assert m["completed_runs"] == 0
    assert m["final_success_rate"] == 0.0


def test_stability_accepted_7_of_10():
    runs = [_success_run()] * 7 + [_failed_run()] * 3
    m = aggregate_real_campaign(runs, requested_runs=10)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert status == "VERA3B_REAL_LLM_STABILITY_ACCEPTED"


def test_stability_failed_6_of_10():
    runs = [_success_run()] * 6 + [_failed_run()] * 4
    m = aggregate_real_campaign(runs, requested_runs=10)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert status == "VERA3B_REAL_LLM_STABILITY_FAILED"
