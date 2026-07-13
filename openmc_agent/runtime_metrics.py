"""Small, auditable metrics and promotion gates for R7/R8 campaigns."""

from __future__ import annotations

from typing import Any


def aggregate_fault_matrix(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(bool(item.get("passed")) for item in results)
    unsafe = sum(int(item.get("unsafe_accepted_count", 0)) for item in results)
    complete = sum(bool(item.get("artifact_complete")) for item in results)
    return {
        "case_count": total,
        "pass_count": passed,
        "pass_rate": passed / total if total else 0.0,
        "unsafe_accepted_patch": unsafe,
        "artifact_completeness_rate": complete / total if total else 0.0,
        "status": "VERA3B_RUNTIME_FAULT_MATRIX_PASSED" if total and passed == total and unsafe == 0 and complete == total else "VERA3B_RUNTIME_FAULT_MATRIX_FAILED",
    }


def aggregate_real_campaign(results: list[dict[str, Any]], *, requested_runs: int) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") not in {"infrastructure_failure", "not_run"}]
    successes = [item for item in completed if item.get("final_disposition") in {"FIRST_PASS_SUCCESS", "RECOVERED_SUCCESS"}]
    first_pass = [item for item in completed if item.get("final_disposition") == "FIRST_PASS_SUCCESS"]
    recovered = [item for item in completed if item.get("final_disposition") == "RECOVERED_SUCCESS"]
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


def real_campaign_status(metrics: dict[str, Any], *, real_environment_available: bool) -> str:
    if not real_environment_available:
        return "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"
    if metrics.get("requested_runs", 0) < 10:
        return "VERA3B_REAL_LLM_PILOT_PASSED"
    accepted = (
        metrics.get("completed_runs", 0) >= 10
        and metrics.get("final_success_rate", 0.0) >= 0.7
        and metrics.get("unsafe_acceptance_rate", 1.0) == 0.0
        and metrics.get("artifact_completeness_rate", 0.0) == 1.0
    )
    return "VERA3B_REAL_LLM_STABILITY_ACCEPTED" if accepted else "VERA3B_REAL_LLM_STABILITY_FAILED"
