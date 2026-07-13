"""Post-execution supervisor contracts for bounded runtime recovery.

This is intentionally separate from ``run_supervisor.py``: planning actions
and runtime recovery actions have different authority, budgets, and vetoes.
"""

from __future__ import annotations

import time
import json
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class RuntimeSupervisorAction(str, Enum):
    FINISH_SUCCESS = "finish_success"
    ATTEMPT_DETERMINISTIC_REPAIR = "attempt_deterministic_repair"
    ATTEMPT_LLM_REPAIR = "attempt_llm_repair"
    RETRY_SAME_PLAN = "retry_same_plan"
    REQUEST_HUMAN_CONFIRMATION = "request_human_confirmation"
    STOP = "stop"


class RuntimeLoopBudget(AgentBaseModel):
    max_runtime_iterations: int = 4
    max_committed_repairs: int = 3
    max_reexecutions: int = 3
    max_deterministic_attempts: int = 3
    max_llm_diagnoses: int = 2
    max_llm_proposals: int = 2
    max_transient_retries: int = 1
    max_candidate_openmc_checks: int = 4
    max_no_progress_steps: int = 2
    max_same_fingerprint_after_commit: int = 0


class RuntimeSupervisorInput(AgentBaseModel):
    decision_id: str
    current_runtime_iteration: int = 0
    current_stage: str = "post_execution"
    plan_hash: str | None = None
    build_state_hash: str | None = None
    current_primary_failure: dict[str, Any] | None = None
    current_secondary_failures: list[dict[str, Any]] = Field(default_factory=list)
    current_failure_fingerprint: str | None = None
    current_failure_classification: str | None = None
    runtime_policy_summary: dict[str, Any] = Field(default_factory=dict)
    deterministic_repair_available: bool = False
    llm_diagnosis_available: bool = False
    llm_proposal_available: bool = False
    last_repair_evaluation: dict[str, Any] | None = None
    last_diagnosis_summary: dict[str, Any] | None = None
    last_proposal_summary: dict[str, Any] | None = None
    committed_repair_count: int = 0
    reexecution_count: int = 0
    deterministic_attempt_count: int = 0
    llm_diagnosis_count: int = 0
    llm_proposal_count: int = 0
    transient_retry_count: int = 0
    candidate_openmc_check_count: int = 0
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    failure_history: list[dict[str, Any]] = Field(default_factory=list)
    repair_history: list[dict[str, Any]] = Field(default_factory=list)
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    no_progress_count: int = 0
    user_cancelled: bool = False
    execution_succeeded: bool = False
    allowed_actions: list[RuntimeSupervisorAction] = Field(default_factory=list)
    state_fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeSupervisorDecision(AgentBaseModel):
    decision_id: str
    action: RuntimeSupervisorAction
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    expected_state_change: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    requires_human_confirmation: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeSupervisorResult(AgentBaseModel):
    decision_id: str
    proposed_decision: RuntimeSupervisorDecision | None = None
    final_action: RuntimeSupervisorAction | None = None
    accepted: bool = False
    vetoed: bool = False
    veto_reasons: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    supervisor: str = "deterministic"
    model: str | None = None
    state_fingerprint: str = ""
    duration_ms: float | None = None
    artifact_paths: list[str] = Field(default_factory=list)


class RuntimeIterationState(AgentBaseModel):
    iteration: int
    execution_attempt_id: str = Field(default_factory=lambda: f"exec_{uuid4().hex[:12]}")
    plan_hash_before_execution: str | None = None
    build_state_hash: str | None = None
    tool_stage: str = "execute_tools"
    primary_failure_fingerprint: str | None = None
    primary_issue_code: str | None = None
    supervisor_action: str | None = None
    repair_channel: str | None = None
    repair_candidate_hash: str | None = None
    plan_hash_after_repair: str | None = None
    execution_result: str | None = None
    progress_made: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    artifact_dir: str | None = None


class RuntimeSupervisorClient(Protocol):
    def decide(self, supervisor_input: RuntimeSupervisorInput, *, prompt: str, json_schema: dict[str, Any]) -> str | dict[str, Any]: ...


class FakeRuntimeSupervisorClient:
    """Test-only deterministic facade; never used as an implicit real fallback."""

    def decide(self, supervisor_input: RuntimeSupervisorInput, *, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        from openmc_agent.runtime_supervisor_policy import determine_deterministic_runtime_action
        decision = determine_deterministic_runtime_action(supervisor_input)
        return decision.model_dump(mode="json")


def run_runtime_supervisor_decision(
    supervisor_input: RuntimeSupervisorInput,
    *,
    client: RuntimeSupervisorClient | None = None,
    model_name: str | None = None,
    allow_fallback: bool = False,
) -> RuntimeSupervisorResult:
    """Get a runtime action and apply deterministic vetoes.

    With no client, choose the deterministic action directly. A fake client is
    never substituted for a missing real client.
    """
    from openmc_agent.runtime_supervisor_policy import (
        determine_deterministic_runtime_action,
        validate_runtime_supervisor_decision,
    )
    from openmc_agent.runtime_supervisor_prompts import build_runtime_supervisor_prompt

    started = time.perf_counter()
    if client is None:
        decision = determine_deterministic_runtime_action(supervisor_input)
        return RuntimeSupervisorResult(
            decision_id=supervisor_input.decision_id,
            proposed_decision=decision,
            final_action=decision.action,
            accepted=True,
            fallback_used=False,
            supervisor="deterministic",
            state_fingerprint=supervisor_input.state_fingerprint,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    try:
        raw = client.decide(
            supervisor_input,
            prompt=build_runtime_supervisor_prompt(supervisor_input),
            json_schema=RuntimeSupervisorDecision.model_json_schema(),
        )
        import json
        payload = json.loads(raw) if isinstance(raw, str) else raw
        decision = RuntimeSupervisorDecision.model_validate(payload)
    except Exception:
        if not allow_fallback:
            return RuntimeSupervisorResult(
                decision_id=supervisor_input.decision_id,
                vetoed=True,
                veto_reasons=["runtime_supervisor.client_unavailable"],
                supervisor="unavailable",
                state_fingerprint=supervisor_input.state_fingerprint,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        decision = determine_deterministic_runtime_action(supervisor_input)

    vetoes = validate_runtime_supervisor_decision(decision, supervisor_input)
    accepted = not vetoes
    return RuntimeSupervisorResult(
        decision_id=supervisor_input.decision_id,
        proposed_decision=decision,
        final_action=decision.action if accepted else RuntimeSupervisorAction.STOP,
        accepted=accepted,
        vetoed=not accepted,
        veto_reasons=vetoes,
        fallback_used=not accepted and allow_fallback,
        supervisor="llm" if accepted else "deterministic",
        model=model_name if accepted else None,
        state_fingerprint=supervisor_input.state_fingerprint,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def write_runtime_iteration_manifest(
    output_dir: str | Path,
    iteration: RuntimeIterationState,
    supervisor_input: RuntimeSupervisorInput,
    result: RuntimeSupervisorResult,
    *,
    final_disposition: str | None = None,
) -> list[str]:
    """Write compact, resumable runtime-loop artifacts for one iteration."""
    root = Path(output_dir) / "runtime_loop" / f"iteration_{iteration.iteration:03d}"
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "iteration_manifest.json": iteration.model_dump(mode="json"),
        "supervisor_input.json": supervisor_input.model_dump(mode="json"),
        "supervisor_result.json": result.model_dump(mode="json"),
        "budget_snapshot.json": supervisor_input.budget_remaining,
        "final_disposition.json": {"disposition": final_disposition},
    }
    paths: list[str] = []
    for name, payload in payloads.items():
        path = root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(str(path))
    loop_manifest = Path(output_dir) / "runtime_loop_manifest.json"
    loop_manifest.write_text(
        json.dumps({
            "current_iteration": iteration.iteration,
            "last_complete_execution": iteration.execution_attempt_id,
            "plan_hash": iteration.plan_hash_before_execution,
            "build_state_hash": iteration.build_state_hash,
            "supervisor_action": result.final_action.value if result.final_action else None,
            "resume_safety_status": "committed_source_only",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.append(str(loop_manifest))
    return paths
