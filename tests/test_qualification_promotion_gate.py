"""Tests for T6 qualification promotion gate (S11)."""

from __future__ import annotations

import pytest

from openmc_agent.runtime_metrics import aggregate_real_campaign, real_campaign_status
from openmc_agent.p1_runtime_gate import evaluate_p1_runtime_final_gate


def _pass_run():
    return {
        "final_disposition": "FIRST_PASS_SUCCESS", "status": "completed",
        "unsafe_proposal_count": 0, "unsafe_accepted_count": 0,
        "protected_field_change_count": 0,
        "environment_plan_repair_attempts": 0,
        "human_fact_plan_repair_attempts": 0,
        "infinite_loop_count": 0, "duplicate_commit_count": 0,
        "stale_plan_execution_count": 0,
        "fake_client_used": False,
        "reference_patches_used": [],
        "benchmark_specific_few_shot_used": False,
        "gold_few_shot_used": False,
        "monolithic_fallback_used": False,
        "artifact_complete": True, "runtime_iterations": 0,
        "committed_runtime_repairs": 0,
        "real_llm_verified": True, "real_openmc_verified": True,
        "vera3_acceptance_passed": True,
        "duration_s": 180, "llm_call_count": 7,
        "planning_network_call_count": 7,
        "llm_output_chars": 5000,
        "lost_particle_count": 0, "source_rejection_count": 0,
        "smoke_backend": "real_openmc",
        "deterministic_runtime_attempts": 0,
        "metadata": {},
    }


def _fail_run():
    return {
        "final_disposition": "PLANNING_FAILURE", "status": "completed",
        "unsafe_proposal_count": 0,
        "artifact_complete": True, "runtime_iterations": 0,
        "duration_s": 60, "llm_call_count": 3,
        "lost_particle_count": 0, "source_rejection_count": 0,
        "metadata": {},
    }


def test_t6_qualification_pass_7_of_10():
    runs = [_pass_run()] * 7 + [_fail_run()] * 3
    m = aggregate_real_campaign(runs, requested_runs=10)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert "STABILITY_ACCEPTED" in status


def test_t6_qualification_fail_6_of_10():
    runs = [_pass_run()] * 6 + [_fail_run()] * 4
    m = aggregate_real_campaign(runs, requested_runs=10)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert "STABILITY_FAILED" in status


def test_t6_fail_with_unsafe_accepted():
    runs = [_pass_run()] * 10
    runs[0]["unsafe_accepted_count"] = 1
    m = aggregate_real_campaign(runs, requested_runs=10)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert "STABILITY_FAILED" in status


def test_t6_fail_with_protected_change():
    runs = [_pass_run()] * 10
    runs[0]["protected_field_change_count"] = 1
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["protected_field_change_count"] > 0


def test_t6_fail_with_fake_client():
    runs = [_pass_run()] * 10
    runs[0]["fake_client_used"] = True
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["fake_client_count"] > 0


def test_t6_fail_with_monolithic_fallback():
    runs = [_pass_run()] * 10
    runs[0]["monolithic_fallback_used"] = True
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["monolithic_fallback_count"] > 0


def test_t6_fail_with_reference_patch():
    runs = [_pass_run()] * 10
    runs[0]["reference_patches_used"] = ["ref_001"]
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["reference_patch_count"] > 0


def test_t6_fail_with_benchmark_few_shot():
    runs = [_pass_run()] * 10
    runs[0]["benchmark_specific_few_shot_used"] = True
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["benchmark_few_shot_count"] > 0


def test_t6_fail_with_incomplete_artifact():
    runs = [_pass_run()] * 10
    runs[0]["artifact_complete"] = False
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["artifact_completeness_rate"] < 1.0


def test_t6_all_success_must_have_real_llm_verified():
    runs = [_pass_run()] * 10
    runs[0]["real_llm_verified"] = False
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["real_llm_verification_rate"] < 1.0


def test_t6_all_success_must_have_real_openmc_verified():
    runs = [_pass_run()] * 10
    runs[0]["real_openmc_verified"] = False
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["real_openmc_verification_rate"] < 1.0


def test_t6_all_success_must_have_vera3_acceptance():
    runs = [_pass_run()] * 10
    runs[0]["vera3_acceptance_passed"] = False
    m = aggregate_real_campaign(runs, requested_runs=10)
    assert m["vera3_acceptance_rate"] < 1.0


# -- P1-RUNTIME final gate tests --


def test_final_gate_all_passed():
    metrics = aggregate_real_campaign([_pass_run()] * 10, requested_runs=10)
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_metrics=metrics,
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_COMPLETE"


def test_final_gate_fails_with_missing_seed_stability():
    metrics = aggregate_real_campaign([_pass_run()] * 10, requested_runs=10)
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_metrics=metrics,
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_FAILED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"
    assert "VERA3B_TRANSPORT_SEED_STABILITY_PASSED" in gate.failed_gates


def test_final_gate_fails_with_unsafe_in_metrics():
    metrics = aggregate_real_campaign([_pass_run()] * 10, requested_runs=10)
    metrics["unsafe_acceptance_rate"] = 0.1
    metrics["unsafe_accepted_count"] = 1
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_metrics=metrics,
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"
