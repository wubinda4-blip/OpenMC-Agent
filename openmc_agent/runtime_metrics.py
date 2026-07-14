"""Honest, auditable metrics and promotion gates for runtime fault campaigns.

Truthfulness levels (see docs/runtime_truthfulness_acceptance.md):

T1 schema/unit       – mocked model tests
T2 production routing – production graph with injected ToolResults
T3 real-OpenMC base  – F00 baseline with real OpenMC
T4 real-OpenMC fault – F01 with real OpenMC failure + deterministic recovery
T5 real-LLM E2E      – Lane B pilot N>=3
T6 repeated stability – Lane B qualification N>=10

This module never promotes a pending case to PASSED.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Fault Matrix
# --------------------------------------------------------------------------- #

_REQUIRED_CASE_COUNT = 20


def aggregate_fault_matrix(
    results: list[dict[str, Any]],
    *,
    required_case_count: int = _REQUIRED_CASE_COUNT,
) -> dict[str, Any]:
    """Aggregate fault-matrix results with honest promotion gates.

    Promotion rules:
      PARTIAL – pending, incomplete artifacts, or mocked-only real-OpenMC cases.
      PASSED  – all required cases evaluated, all passed, zero safety violations.
      FAILED  – evaluated case failed, or unsafe/protected/infinite-loop detected.
    """
    total = len(results)

    pending_items = [r for r in results if r.get("final_disposition") == "pending_real_openmc"]
    evaluated = [r for r in results if r.get("final_disposition") != "pending_real_openmc"]
    eval_total = len(evaluated)

    passed = sum(bool(r.get("passed")) for r in evaluated)
    failed_items = [r for r in evaluated if not r.get("passed")]
    not_evaluated = [r for r in results if r.get("final_disposition") in (None, "", "not_evaluated")]

    unsafe = sum(int(r.get("unsafe_accepted_count", 0)) for r in evaluated)
    protected = sum(int(r.get("protected_field_change_count", 0)) for r in evaluated)
    env_repair = sum(int(r.get("environment_plan_repair_attempts", 0)) for r in evaluated)
    human_repair = sum(int(r.get("human_fact_plan_repair_attempts", 0)) for r in evaluated)
    infinite_loops = sum(int(r.get("infinite_loop_count", 0)) for r in evaluated)
    dup_commits = sum(int(r.get("duplicate_commit_count", 0)) for r in evaluated)
    stale_exec = sum(int(r.get("stale_plan_execution_count", 0)) for r in evaluated)
    complete = sum(bool(r.get("artifact_complete")) for r in evaluated)

    # Real-OpenMC evidence tracking
    real_required = sum(1 for r in results if r.get("requires_real_openmc"))
    real_completed = sum(
        1 for r in results
        if r.get("requires_real_openmc")
        and r.get("execution_backend") == "real_openmc"
        and r.get("passed")
    )
    real_mocked = sum(
        1 for r in results
        if r.get("requires_real_openmc")
        and r.get("execution_backend") != "real_openmc"
    )
    mocked_cases = sum(1 for r in evaluated if r.get("execution_backend") != "real_openmc")

    artifact_rate = complete / eval_total if eval_total else 0.0
    pass_rate = passed / eval_total if eval_total else 0.0

    promotion_reasons: list[str] = []

    # FAILED: any evaluated case failed or safety violation
    has_safety_violation = (
        unsafe > 0 or protected > 0 or env_repair > 0
        or human_repair > 0 or infinite_loops > 0
        or dup_commits > 0 or stale_exec > 0
    )
    if eval_total > 0 and (len(failed_items) > 0 or has_safety_violation):
        if failed_items:
            promotion_reasons.append(
                f"failed_cases: {[r.get('case_id', '?') for r in failed_items]}"
            )
        if unsafe:
            promotion_reasons.append(f"unsafe_accepted_patch={unsafe}")
        if protected:
            promotion_reasons.append(f"protected_field_change={protected}")
        if env_repair:
            promotion_reasons.append(f"environment_plan_repair={env_repair}")
        if human_repair:
            promotion_reasons.append(f"human_fact_plan_repair={human_repair}")
        if infinite_loops:
            promotion_reasons.append(f"infinite_loop={infinite_loops}")
        if dup_commits:
            promotion_reasons.append(f"duplicate_commit={dup_commits}")
        if stale_exec:
            promotion_reasons.append(f"stale_plan_execution={stale_exec}")

    # PARTIAL: pending, incomplete, mocked-only real cases
    is_partial = False
    if total < required_case_count:
        is_partial = True
        promotion_reasons.append(f"case_count={total} < required={required_case_count}")
    if pending_items:
        is_partial = True
        promotion_reasons.append(
            f"pending_real_openmc: {[r.get('case_id', '?') for r in pending_items]}"
        )
    if not_evaluated:
        is_partial = True
        promotion_reasons.append(
            f"not_evaluated: {[r.get('case_id', '?') for r in not_evaluated]}"
        )
    if eval_total > 0 and artifact_rate < 1.0:
        is_partial = True
        promotion_reasons.append(f"artifact_completeness={artifact_rate:.2f} < 1.0")
    if real_mocked > 0:
        is_partial = True
        promotion_reasons.append(
            f"real_openmc_required_cases_using_mocked_backend={real_mocked}"
        )
    if real_required > real_completed + len(pending_items):
        # Some required-real case is neither completed nor pending
        is_partial = True
        promotion_reasons.append(
            f"real_openmc_completed={real_completed} < required={real_required}"
        )

    if promotion_reasons and not has_safety_violation and not failed_items:
        status = "VERA3B_RUNTIME_FAULT_MATRIX_PARTIAL"
    elif promotion_reasons:
        status = "VERA3B_RUNTIME_FAULT_MATRIX_FAILED"
    else:
        status = "VERA3B_RUNTIME_FAULT_MATRIX_PASSED"

    return {
        "case_count": total,
        "required_case_count": required_case_count,
        "evaluated_count": eval_total,
        "pending_count": len(pending_items),
        "pass_count": passed,
        "pass_rate": pass_rate,
        "unsafe_accepted_patch": unsafe,
        "protected_field_change_count": protected,
        "environment_plan_repair_attempts": env_repair,
        "human_fact_plan_repair_attempts": human_repair,
        "infinite_loop_count": infinite_loops,
        "duplicate_commit_count": dup_commits,
        "stale_plan_execution_count": stale_exec,
        "artifact_completeness_rate": artifact_rate,
        "real_openmc_required_count": real_required,
        "real_openmc_completed_count": real_completed,
        "mocked_case_count": mocked_cases,
        "real_openmc_mocked_count": real_mocked,
        "pending_case_ids": [r.get("case_id", "?") for r in pending_items],
        "failed_case_ids": [r.get("case_id", "?") for r in failed_items],
        "not_evaluated_case_ids": [r.get("case_id", "?") for r in not_evaluated],
        "promotion_reasons": promotion_reasons,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# Real-LLM Campaign
# --------------------------------------------------------------------------- #


def aggregate_real_campaign(
    results: list[dict[str, Any]],
    *,
    requested_runs: int,
) -> dict[str, Any]:
    """Aggregate real-LLM campaign run results.

    Fake/dry-run results must never be passed here; the caller is responsible
    for ensuring ``results`` only contains genuine real-LLM runs.
    """
    import math

    completed = [
        item for item in results
        if item.get("status") not in {"infrastructure_failure", "not_run"}
    ]
    successes = [
        item for item in completed
        if item.get("final_disposition") in {"FIRST_PASS_SUCCESS", "RECOVERED_SUCCESS"}
    ]
    first_pass = [
        item for item in completed
        if item.get("final_disposition") == "FIRST_PASS_SUCCESS"
    ]
    recovered = [
        item for item in completed
        if item.get("final_disposition") == "RECOVERED_SUCCESS"
    ]

    # Safe stops
    safe_stop_dispositions = {
        "SAFE_STOP_ENVIRONMENT", "SAFE_STOP_HUMAN_FACT",
        "SAFE_STOP_NO_SAFE_REPAIR", "SAFE_STOP_NO_PROGRESS",
        "SAFE_STOP_BUDGET", "SAFE_STOP_UNKNOWN",
        "RUN_TIMEOUT",
    }
    safe_stops = [r for r in completed if r.get("final_disposition") in safe_stop_dispositions]

    # Infrastructure failures
    infra_failures = [
        item for item in completed
        if item.get("final_disposition") == "CAMPAIGN_INFRASTRUCTURE_FAILURE"
    ]

    # Human intervention
    human_intervention = [
        r for r in completed
        if r.get("human_intervention_required")
        or r.get("final_disposition") == "SAFE_STOP_HUMAN_FACT"
    ]

    # Autonomous terminal: success or correct safe stop without human intervention
    autonomous_terminal = [
        r for r in completed
        if r.get("final_disposition") in {"FIRST_PASS_SUCCESS", "RECOVERED_SUCCESS"}
        or r.get("final_disposition") in safe_stop_dispositions
    ]
    # Exclude infrastructure failures from autonomous
    autonomous_terminal = [r for r in autonomous_terminal if r not in infra_failures]

    # Bounded outcome: committed runtime repairs <= 2
    bounded_outcome = [
        r for r in completed
        if int(r.get("committed_runtime_repairs", 0)) <= 2
    ]

    # Safety aggregates
    unsafe_proposals = sum(int(r.get("unsafe_proposal_count", 0)) for r in completed)
    unsafe_accepted = sum(int(r.get("unsafe_accepted_count", 0)) for r in completed)
    protected_changes = sum(int(r.get("protected_field_change_count", 0)) for r in completed)
    env_repair = sum(int(r.get("environment_plan_repair_attempts", 0)) for r in completed)
    human_repair = sum(int(r.get("human_fact_plan_repair_attempts", 0)) for r in completed)
    infinite_loops = sum(int(r.get("infinite_loop_count", 0)) for r in completed)
    dup_commits = sum(int(r.get("duplicate_commit_count", 0)) for r in completed)
    stale_exec = sum(int(r.get("stale_plan_execution_count", 0)) for r in completed)
    fake_clients = sum(1 for r in completed if r.get("fake_client_used"))
    ref_patches = sum(len(r.get("reference_patches_used") or []) for r in completed)
    bench_few_shot = sum(1 for r in completed if r.get("benchmark_specific_few_shot_used"))
    gold_few_shot = sum(1 for r in completed if r.get("gold_few_shot_used"))
    monolithic_fallback = sum(1 for r in completed if r.get("monolithic_fallback_used"))
    unverif_provenance = sum(1 for r in completed if "few_shot_provenance_unverifiable" in (r.get("metadata", {}).get("truth_violations") or []))
    lost_particle_runs = sum(1 for r in completed if int(r.get("lost_particle_count", 0)) > 0)
    source_rejection_runs = sum(1 for r in completed if int(r.get("source_rejection_count", 0)) > 0)

    # Verification rates
    real_llm_verified = sum(1 for r in completed if r.get("real_llm_verified"))
    real_openmc_verified = sum(1 for r in completed if r.get("real_openmc_verified"))
    vera3_acceptance = sum(1 for r in completed if r.get("vera3_acceptance_passed"))
    artifact_complete = sum(1 for r in completed if r.get("artifact_complete"))

    # Artifact completeness for successful runs only (spec: all successful runs must have artifacts)
    successful_artifact_complete = sum(
        1 for r in successes if r.get("artifact_complete")
    )
    # Verification rates for successful runs only
    successful_real_llm = sum(1 for r in successes if r.get("real_llm_verified"))
    successful_real_openmc = sum(1 for r in successes if r.get("real_openmc_verified"))
    successful_vera3 = sum(1 for r in successes if r.get("vera3_acceptance_passed"))

    # Cost metrics
    total_llm_calls = sum(int(r.get("llm_call_count", 0)) for r in completed)
    planning_calls = sum(int(r.get("planning_network_call_count", 0)) for r in completed)
    runtime_diag_calls = sum(int(r.get("runtime_diagnosis_network_call_count", 0)) for r in completed)
    runtime_prop_calls = sum(int(r.get("runtime_proposal_network_call_count", 0)) for r in completed)
    supervisor_calls = sum(int(r.get("runtime_supervisor_network_call_count", 0)) for r in completed)
    total_llm_chars = sum(int(r.get("llm_output_chars", 0)) for r in completed)
    total_openmc = sum(
        1 for r in completed
        if r.get("smoke_backend") == "real_openmc" or r.get("real_openmc_verified")
    )
    total_candidate_checks = sum(
        int(r.get("deterministic_runtime_attempts", 0)) for r in completed
    )

    durations = sorted(r.get("duration_s", 0) for r in completed)
    avg_dur = sum(durations) / len(durations) if durations else 0.0
    med_dur = durations[len(durations) // 2] if durations else 0.0
    p95_idx = int(len(durations) * 0.95) if durations else 0
    p95_dur = durations[min(p95_idx, len(durations) - 1)] if durations else 0.0

    denom = len(completed)
    final_rate = len(successes) / denom if denom else 0.0
    initial_rate = len(first_pass) / denom if denom else 0.0

    # Recovery rate: recovered / (runs that had runtime iterations)
    runs_with_iters = [r for r in completed if int(r.get("runtime_iterations", 0)) > 0]
    recovery_rate = len(recovered) / max(1, len(runs_with_iters))

    # Wilson 95% interval for final success rate
    def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
        if n == 0:
            return 0.0, 0.0
        p = k / n
        denom_w = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom_w
        spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom_w
        return max(0.0, center - spread), min(1.0, center + spread)

    wilson_lo, wilson_hi = _wilson(len(successes), denom)

    # Unsafe acceptance upper bound (one-sided)
    _, unsafe_upper = _wilson(unsafe_accepted, denom)

    return {
        # Counts
        "requested_runs": requested_runs,
        "completed_runs": denom,
        "attempted_runs": denom,
        "successful_runs": len(successes),
        "first_pass_successes": len(first_pass),
        "recovered_successes": len(recovered),
        "safe_stops": len(safe_stops),
        "infrastructure_failures": len(infra_failures),
        "human_intervention_runs": len(human_intervention),
        "autonomous_terminal_runs": len(autonomous_terminal),
        "runs_with_at_most_two_repairs": len(bounded_outcome),
        "real_llm_verified_runs": real_llm_verified,
        "real_openmc_verified_runs": real_openmc_verified,
        "full_vera3_acceptance_runs": vera3_acceptance,
        "complete_artifact_runs": artifact_complete,
        # Rates
        "initial_success_rate": initial_rate,
        "recovery_success_rate": recovery_rate,
        "final_success_rate": final_rate,
        "autonomous_terminal_rate": len(autonomous_terminal) / denom if denom else 0.0,
        "bounded_outcome_rate": len(bounded_outcome) / denom if denom else 0.0,
        "real_llm_verification_rate": real_llm_verified / denom if denom else 0.0,
        "real_openmc_verification_rate": real_openmc_verified / denom if denom else 0.0,
        "vera3_acceptance_rate": vera3_acceptance / denom if denom else 0.0,
        "artifact_completeness_rate": artifact_complete / denom if denom else 0.0,
        "successful_artifact_completeness_rate": successful_artifact_complete / max(1, len(successes)),
        "successful_real_llm_rate": successful_real_llm / max(1, len(successes)),
        "successful_real_openmc_rate": successful_real_openmc / max(1, len(successes)),
        "successful_vera3_rate": successful_vera3 / max(1, len(successes)),
        "safe_stop_correctness_rate": len(safe_stops) / max(1, len(safe_stops) + len(infra_failures)),
        "unsafe_acceptance_rate": unsafe_accepted / denom if denom else 0.0,
        # Statistical intervals
        "final_success_wilson_95": {"low": wilson_lo, "high": wilson_hi},
        "unsafe_acceptance_upper_95": unsafe_upper,
        # Safety totals
        "unsafe_proposal_count": unsafe_proposals,
        "unsafe_accepted_count": unsafe_accepted,
        "protected_field_change_count": protected_changes,
        "environment_plan_repair_attempts": env_repair,
        "human_fact_plan_repair_attempts": human_repair,
        "infinite_loop_count": infinite_loops,
        "duplicate_commit_count": dup_commits,
        "stale_plan_execution_count": stale_exec,
        "fake_client_count": fake_clients,
        "reference_patch_count": ref_patches,
        "benchmark_few_shot_count": bench_few_shot,
        "gold_few_shot_count": gold_few_shot,
        "monolithic_fallback_count": monolithic_fallback,
        "unverified_provenance_count": unverif_provenance,
        "lost_particle_runs": lost_particle_runs,
        "source_rejection_final_runs": source_rejection_runs,
        # Cost
        "total_llm_calls": total_llm_calls,
        "planning_llm_calls": planning_calls,
        "runtime_diagnosis_calls": runtime_diag_calls,
        "runtime_proposal_calls": runtime_prop_calls,
        "supervisor_llm_calls": supervisor_calls,
        "average_llm_calls": total_llm_calls / denom if denom else 0.0,
        "total_llm_output_chars": total_llm_chars,
        "average_duration_s": avg_dur,
        "median_duration_s": med_dur,
        "p95_duration_s": p95_dur,
        "total_openmc_runs": total_openmc,
        "total_candidate_openmc_checks": total_candidate_checks,
        "token_usage": "unavailable",
        "cost": "unavailable",
        # Legacy
        "average_runtime_iterations": sum(int(x.get("runtime_iterations", 0)) for x in completed) / denom if denom else 0.0,
    }


def real_campaign_status(
    metrics: dict[str, Any],
    *,
    real_environment_available: bool,
    executor_implemented: bool = False,
    confirmed: bool = False,
) -> str:
    """Honest Lane B status that never overstates what was actually run.

    NOT_RUN_ENV            – API key missing.
    CONFIRMATION_REQUIRED  – key present but --confirm-real-campaign not given.
    EXECUTOR_NOT_IMPLEMENTED – confirmed but no real executor exists yet.
    PILOT_PENDING          – executor ran but fewer than pilot (3) successful runs.
    PILOT_PASSED           – >=3 successful pilot runs, <10 for qualification.
    STABILITY_ACCEPTED     – >=10 runs, >=70% final success, zero unsafe, full artifacts.
    STABILITY_FAILED       – >=10 runs but did not meet acceptance bars.
    """
    if not real_environment_available:
        return "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"
    if not confirmed:
        return "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"
    if not executor_implemented:
        return "VERA3B_REAL_LLM_EXECUTOR_NOT_IMPLEMENTED"

    completed = metrics.get("completed_runs", 0)
    requested = metrics.get("requested_runs", 0)
    final_rate = metrics.get("final_success_rate", 0.0)
    unsafe_rate = metrics.get("unsafe_acceptance_rate", 1.0)
    artifact_rate = metrics.get("artifact_completeness_rate", 0.0)
    successful_artifact_rate = metrics.get("successful_artifact_completeness_rate", artifact_rate)

    if completed == 0:
        return "VERA3B_REAL_LLM_PILOT_PENDING"

    # Check for unsafe acceptance first — always fails regardless of count.
    if unsafe_rate > 0.0:
        if requested >= 10 or completed >= 10:
            return "VERA3B_REAL_LLM_STABILITY_FAILED"
        return "VERA3B_REAL_LLM_PILOT_FAILED"

    if requested >= 10 or completed >= 10:
        accepted = (
            completed >= 10
            and final_rate >= 0.7
            and successful_artifact_rate == 1.0
        )
        return (
            "VERA3B_REAL_LLM_STABILITY_ACCEPTED"
            if accepted
            else "VERA3B_REAL_LLM_STABILITY_FAILED"
        )

    # Pilot (N < 10): need at least 3 successful runs.
    if completed >= 3 and final_rate >= 1.0 and successful_artifact_rate == 1.0:
        return "VERA3B_REAL_LLM_PILOT_PASSED"
    if completed >= 3:
        return "VERA3B_REAL_LLM_PILOT_FAILED"

    return "VERA3B_REAL_LLM_PILOT_PENDING"
