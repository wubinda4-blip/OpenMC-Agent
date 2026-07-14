"""Tests for P1-RUNTIME final gate (S15)."""

from __future__ import annotations

from openmc_agent.p1_runtime_gate import evaluate_p1_runtime_final_gate
from openmc_agent.runtime_metrics import aggregate_real_campaign


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


def _full_qual_metrics():
    return aggregate_real_campaign([_pass_run()] * 10, requested_runs=10)


def test_all_gates_pass():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_metrics=_full_qual_metrics(),
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
    assert gate.all_passed


def test_missing_fault_matrix_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
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
    assert "VERA3B_RUNTIME_FAULT_MATRIX_PASSED" in gate.failed_gates


def test_missing_pilot_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PENDING",
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
    assert "RUNTIME_TRUTHFULNESS_T5_PASSED" in gate.failed_gates


def test_missing_qualification_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_status="VERA3B_REAL_LLM_STABILITY_FAILED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"


def test_missing_seed_stability_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
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


def test_missing_tests_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=False,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"
    assert "NON_OPENMC_TESTS_PASSED" in gate.failed_gates


def test_missing_benchmark_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=0.9,
        benchmark_total=21,
        worktree_clean=True,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"
    assert "BENCHMARK_21_21" in gate.failed_gates


def test_dirty_worktree_fails():
    gate = evaluate_p1_runtime_final_gate(
        fault_matrix_status="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        pilot_status="VERA3B_REAL_LLM_PILOT_PASSED",
        qualification_status="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        seed_stability_status="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        non_openmc_tests_passed=True,
        openmc_tests_passed=True,
        benchmark_pass_rate=1.0,
        benchmark_total=21,
        worktree_clean=False,
        artifact_manifest_complete=True,
    )
    assert gate.status == "P1_RUNTIME_STAGE_NOT_COMPLETE"
    assert "WORKTREE_CLEAN" in gate.failed_gates
