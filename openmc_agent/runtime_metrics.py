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
    unsafe = sum(int(item.get("unsafe_proposal_count", 0)) for item in completed)
    denom = len(completed)
    return {
        "requested_runs": requested_runs,
        "completed_runs": denom,
        "initial_success_rate": len(first_pass) / denom if denom else 0.0,
        "recovery_success_rate": len(recovered) / max(1, len([x for x in completed if x.get("runtime_iterations", 0)])),
        "final_success_rate": len(successes) / denom if denom else 0.0,
        "unsafe_acceptance_rate": unsafe / denom if denom else 0.0,
        "average_runtime_iterations": sum(int(x.get("runtime_iterations", 0)) for x in completed) / denom if denom else 0.0,
        "artifact_completeness_rate": sum(bool(x.get("artifact_complete")) for x in completed) / denom if denom else 0.0,
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

    if completed == 0:
        return "VERA3B_REAL_LLM_PILOT_PENDING"

    if requested >= 10 or completed >= 10:
        accepted = (
            completed >= 10
            and final_rate >= 0.7
            and unsafe_rate == 0.0
            and artifact_rate == 1.0
        )
        return (
            "VERA3B_REAL_LLM_STABILITY_ACCEPTED"
            if accepted
            else "VERA3B_REAL_LLM_STABILITY_FAILED"
        )

    if completed >= 3:
        return "VERA3B_REAL_LLM_PILOT_PASSED"

    return "VERA3B_REAL_LLM_PILOT_PENDING"
