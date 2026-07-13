"""Pure deterministic policy for bounded post-execution runtime recovery."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

from openmc_agent.runtime_supervisor import (
    RuntimeLoopBudget,
    RuntimeSupervisorAction,
    RuntimeSupervisorDecision,
    RuntimeSupervisorInput,
)

VETO_ACTION_NOT_ALLOWED = "runtime_supervisor.action_not_allowed"
VETO_DECISION_ID_MISMATCH = "runtime_supervisor.decision_id_mismatch"
VETO_ENVIRONMENT_REPAIR_FORBIDDEN = "runtime_supervisor.environment_repair_forbidden"
VETO_HUMAN_FACT_BYPASS = "runtime_supervisor.human_fact_bypass"
VETO_DETERMINISTIC_UNAVAILABLE = "runtime_supervisor.deterministic_repair_unavailable"
VETO_LLM_UNAVAILABLE = "runtime_supervisor.llm_repair_unavailable"
VETO_TRANSIENT_RETRY_NOT_ALLOWED = "runtime_supervisor.transient_retry_not_allowed"
VETO_TRANSIENT_RETRY_EXHAUSTED = "runtime_supervisor.transient_retry_exhausted"
VETO_RUNTIME_BUDGET_EXHAUSTED = "runtime_supervisor.runtime_budget_exhausted"
VETO_REEXECUTION_BUDGET_EXHAUSTED = "runtime_supervisor.reexecution_budget_exhausted"
VETO_COMMITTED_REPAIR_BUDGET_EXHAUSTED = "runtime_supervisor.committed_repair_budget_exhausted"
VETO_SAME_FINGERPRINT_AFTER_COMMIT = "runtime_supervisor.same_fingerprint_after_commit"
VETO_NO_PROGRESS = "runtime_supervisor.no_progress"
VETO_USER_CANCELLED = "runtime_supervisor.user_cancelled"
VETO_UNSAFE_ROUTE = "runtime_supervisor.unsafe_route"
VETO_MONOLITHIC_REGENERATION_FORBIDDEN = "runtime_supervisor.monolithic_regeneration_forbidden"


def compute_runtime_supervisor_state_fingerprint(value: RuntimeSupervisorInput) -> str:
    payload = {
        "plan_hash": value.plan_hash,
        "build_state_hash": value.build_state_hash,
        "failure": value.current_failure_fingerprint,
        "issue": (value.current_primary_failure or {}).get("primary_issue_code"),
        "stage": value.current_stage,
        "last_action": value.recent_actions[-1].get("action") if value.recent_actions else None,
        "budget": value.budget_remaining,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "rts_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_runtime_supervisor_input(
    state: Mapping[str, Any], *, budget: RuntimeLoopBudget | None = None,
) -> RuntimeSupervisorInput:
    b = budget or RuntimeLoopBudget()
    primary = state.get("runtime_primary_failure") or None
    classification = primary.get("classification") if isinstance(primary, dict) else None
    fingerprint = primary.get("error_fingerprint") if isinstance(primary, dict) else None
    policy = state.get("runtime_policy_summary") or {}
    used = {
        "iterations": state.get("runtime_iteration_count", 0),
        "commits": state.get("runtime_committed_repair_count", 0),
        "reexecutions": state.get("runtime_reexecution_count", 0),
        "deterministic": state.get("runtime_repair_count", 0),
        "llm_diagnoses": state.get("runtime_llm_diagnosis_count", 0),
        "llm_proposals": state.get("runtime_llm_proposal_count", 0),
        "transient": state.get("runtime_transient_retry_count", 0),
    }
    remaining = {
        "iterations": max(0, b.max_runtime_iterations - used["iterations"]),
        "commits": max(0, b.max_committed_repairs - used["commits"]),
        "reexecutions": max(0, b.max_reexecutions - used["reexecutions"]),
        "deterministic": max(0, b.max_deterministic_attempts - used["deterministic"]),
        "llm_diagnoses": max(0, b.max_llm_diagnoses - used["llm_diagnoses"]),
        "llm_proposals": max(0, b.max_llm_proposals - used["llm_proposals"]),
        "transient": max(0, b.max_transient_retries - used["transient"]),
    }
    value = RuntimeSupervisorInput(
        decision_id=f"rts_{uuid4().hex[:12]}",
        current_runtime_iteration=used["iterations"],
        plan_hash=state.get("runtime_last_plan_hash"),
        build_state_hash=state.get("runtime_last_build_state_hash"),
        current_primary_failure=primary,
        current_secondary_failures=state.get("runtime_failures", [])[1:],
        current_failure_fingerprint=fingerprint,
        current_failure_classification=classification,
        runtime_policy_summary=policy,
        deterministic_repair_available=bool(policy.get("deterministic_repair_supported")),
        llm_diagnosis_available=bool(policy.get("llm_diagnosis_supported")),
        llm_proposal_available=bool(policy.get("llm_proposal_supported")),
        committed_repair_count=used["commits"], reexecution_count=used["reexecutions"],
        deterministic_attempt_count=used["deterministic"], llm_diagnosis_count=used["llm_diagnoses"],
        llm_proposal_count=used["llm_proposals"], transient_retry_count=used["transient"],
        budget_remaining=remaining, failure_history=state.get("runtime_failure_history", []),
        repair_history=state.get("runtime_repair_history", []),
        recent_actions=state.get("runtime_supervisor_history", []),
        no_progress_count=state.get("runtime_no_progress_count", 0),
        user_cancelled=bool(state.get("runtime_user_cancelled", False)),
        execution_succeeded=bool(state.get("runtime_execution_succeeded", False)),
    )
    value.state_fingerprint = compute_runtime_supervisor_state_fingerprint(value)
    value.allowed_actions = compute_allowed_runtime_supervisor_actions(value, budget=b)
    return value


def compute_allowed_runtime_supervisor_actions(value: RuntimeSupervisorInput, *, budget: RuntimeLoopBudget | None = None) -> list[RuntimeSupervisorAction]:
    b = budget or RuntimeLoopBudget()
    if value.execution_succeeded:
        return [RuntimeSupervisorAction.FINISH_SUCCESS, RuntimeSupervisorAction.STOP]
    if value.user_cancelled or value.no_progress_count >= b.max_no_progress_steps:
        return [RuntimeSupervisorAction.STOP]
    classification = value.current_failure_classification
    if classification == "environment":
        return [RuntimeSupervisorAction.REQUEST_HUMAN_CONFIRMATION, RuntimeSupervisorAction.STOP]
    if classification == "human_fact":
        return [RuntimeSupervisorAction.REQUEST_HUMAN_CONFIRMATION, RuntimeSupervisorAction.STOP]
    if value.budget_remaining.get("iterations", 0) <= 0:
        return [RuntimeSupervisorAction.STOP]
    if classification == "plan_fixable":
        if value.deterministic_repair_available and value.budget_remaining.get("deterministic", 0) > 0:
            return [RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR, RuntimeSupervisorAction.STOP]
        if value.llm_diagnosis_available and value.budget_remaining.get("llm_diagnoses", 0) > 0:
            return [RuntimeSupervisorAction.ATTEMPT_LLM_REPAIR, RuntimeSupervisorAction.STOP]
        return [RuntimeSupervisorAction.STOP]
    if classification == "transient" and value.budget_remaining.get("transient", 0) > 0:
        return [RuntimeSupervisorAction.RETRY_SAME_PLAN, RuntimeSupervisorAction.STOP]
    return [RuntimeSupervisorAction.STOP]


def determine_deterministic_runtime_action(value: RuntimeSupervisorInput) -> RuntimeSupervisorDecision:
    action = value.allowed_actions[0] if value.allowed_actions else RuntimeSupervisorAction.STOP
    return RuntimeSupervisorDecision(
        decision_id=value.decision_id, action=action,
        rationale="Deterministic runtime policy selected the highest-priority allowed action.",
        confidence=1.0,
        requires_human_confirmation=action == RuntimeSupervisorAction.REQUEST_HUMAN_CONFIRMATION,
    )


def validate_runtime_supervisor_decision(decision: RuntimeSupervisorDecision, value: RuntimeSupervisorInput) -> list[str]:
    vetoes: list[str] = []
    if decision.decision_id != value.decision_id:
        vetoes.append(VETO_DECISION_ID_MISMATCH)
    if decision.action not in value.allowed_actions:
        vetoes.append(VETO_ACTION_NOT_ALLOWED)
    if value.user_cancelled and decision.action != RuntimeSupervisorAction.STOP:
        vetoes.append(VETO_USER_CANCELLED)
    if value.current_failure_classification == "environment" and decision.action in {RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR, RuntimeSupervisorAction.ATTEMPT_LLM_REPAIR}:
        vetoes.append(VETO_ENVIRONMENT_REPAIR_FORBIDDEN)
    if value.current_failure_classification == "human_fact" and decision.action not in {RuntimeSupervisorAction.REQUEST_HUMAN_CONFIRMATION, RuntimeSupervisorAction.STOP}:
        vetoes.append(VETO_HUMAN_FACT_BYPASS)
    if decision.action == RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR and not value.deterministic_repair_available:
        vetoes.append(VETO_DETERMINISTIC_UNAVAILABLE)
    if decision.action == RuntimeSupervisorAction.ATTEMPT_LLM_REPAIR and not value.llm_diagnosis_available:
        vetoes.append(VETO_LLM_UNAVAILABLE)
    if decision.action == RuntimeSupervisorAction.RETRY_SAME_PLAN and value.current_failure_classification != "transient":
        vetoes.append(VETO_TRANSIENT_RETRY_NOT_ALLOWED)
    return vetoes
