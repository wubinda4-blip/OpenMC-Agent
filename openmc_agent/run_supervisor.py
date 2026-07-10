"""LLM Run Supervisor schemas and decision engine.

The supervisor observes workflow state and proposes a single routing action.
It never executes tools, generates code, or modifies the plan.
Python policy decides which actions are allowed and may veto the LLM's choice.
"""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunSupervisorAction(str, Enum):
    CONTINUE_TO_RENDER = "continue_to_render"
    CONTINUE_PATCH_GENERATION = "continue_patch_generation"
    RETRY_PATCH = "retry_patch"
    REQUEST_HUMAN_CONFIRMATION = "request_human_confirmation"
    DOWNGRADE_TO_SKELETON = "downgrade_to_skeleton"
    STOP = "stop"


class RunSupervisorMode(str, Enum):
    OFF = "off"
    ADVISORY = "advisory"
    CONTROLLED_ROUTE = "controlled_route"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class SupervisorEvidence(AgentBaseModel):
    source_type: Literal[
        "validation_issue",
        "semantic_finding",
        "repair_decision",
        "patch_status",
        "capability",
        "requirement",
        "workflow_history",
        "retry_budget",
    ]
    source_id: str | None = None
    summary: str


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class RunSupervisorInput(AgentBaseModel):
    decision_id: str

    current_stage: str
    planning_mode: str | None = None

    schema_valid: bool | None = None
    blocking_issue_codes: list[str] = Field(default_factory=list)
    warning_issue_codes: list[str] = Field(default_factory=list)

    patch_status: dict[str, str] = Field(default_factory=dict)
    required_patch_types: list[str] = Field(default_factory=list)
    failed_patch_type: str | None = None

    semantic_findings: list[dict[str, Any]] = Field(default_factory=list)
    repair_decisions: list[dict[str, Any]] = Field(default_factory=list)

    renderability: str | None = None
    supported_renderer: str | None = None
    capability_summary: dict[str, Any] = Field(default_factory=dict)

    human_confirmation_required: bool = False
    unresolved_fact_gaps: list[str] = Field(default_factory=list)

    allowed_actions: list[RunSupervisorAction]
    allowed_retry_patch_types: list[str] = Field(default_factory=list)

    retry_budget_remaining: int
    retry_budget_by_patch: dict[str, int] = Field(default_factory=dict)

    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    state_fingerprint: str

    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Decision (what the LLM returns)
# ---------------------------------------------------------------------------

class RunSupervisorDecision(AgentBaseModel):
    decision_id: str
    action: RunSupervisorAction

    target_patch_type: str | None = None

    rationale: str
    evidence: list[SupervisorEvidence] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0)

    expected_state_change: str | None = None
    requires_human_confirmation: bool = False


# ---------------------------------------------------------------------------
# Result (full execution result)
# ---------------------------------------------------------------------------

class RunSupervisorResult(AgentBaseModel):
    decision_id: str
    mode: RunSupervisorMode

    proposed_decision: RunSupervisorDecision | None = None
    final_action: RunSupervisorAction | None = None

    accepted: bool = False
    executed: bool = False
    vetoed: bool = False

    veto_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    supervisor: str = "deterministic"
    model: str | None = None
    fallback_used: bool = False

    state_fingerprint: str
    state_changed: bool | None = None

    duration_ms: float | None = None
    raw_response_chars: int | None = None


# ---------------------------------------------------------------------------
# Veto codes
# ---------------------------------------------------------------------------

SUPERVISOR_VETO_ACTION_NOT_ALLOWED = "supervisor.action_not_in_allowed"
SUPERVISOR_VETO_RENDER_WITH_BLOCKER = "supervisor.render_with_blocking_issue"
SUPERVISOR_VETO_RETRY_BUDGET_EXHAUSTED = "supervisor.retry_budget_exhausted"
SUPERVISOR_VETO_INVALID_PATCH_TARGET = "supervisor.invalid_patch_target"
SUPERVISOR_VETO_HUMAN_CONFIRMATION_BYPASS = "supervisor.human_confirmation_bypass"
SUPERVISOR_VETO_UNSUPPORTED_ACTION = "supervisor.unsupported_action"
SUPERVISOR_VETO_MONOLITHIC_FALLBACK_FORBIDDEN = "supervisor.monolithic_fallback_forbidden"
SUPERVISOR_VETO_LOOP_DETECTED = "supervisor.loop_detected"
SUPERVISOR_VETO_DECISION_ID_MISMATCH = "supervisor.decision_id_mismatch"
SUPERVISOR_VETO_INCREMENTAL_NOT_ACTIVE = "supervisor.incremental_not_active"
SUPERVISOR_VETO_NO_TARGET_PATCH_TYPE = "supervisor.no_target_patch_type"
SUPERVISOR_VETO_SKELETON_NOT_AVAILABLE = "supervisor.skeleton_not_available"
SUPERVISOR_VETO_REQUIRED_PATCH_MISSING = "supervisor.required_patch_missing"

ALL_VETO_CODES = {
    SUPERVISOR_VETO_ACTION_NOT_ALLOWED,
    SUPERVISOR_VETO_RENDER_WITH_BLOCKER,
    SUPERVISOR_VETO_RETRY_BUDGET_EXHAUSTED,
    SUPERVISOR_VETO_INVALID_PATCH_TARGET,
    SUPERVISOR_VETO_HUMAN_CONFIRMATION_BYPASS,
    SUPERVISOR_VETO_UNSUPPORTED_ACTION,
    SUPERVISOR_VETO_MONOLITHIC_FALLBACK_FORBIDDEN,
    SUPERVISOR_VETO_LOOP_DETECTED,
    SUPERVISOR_VETO_DECISION_ID_MISMATCH,
    SUPERVISOR_VETO_INCREMENTAL_NOT_ACTIVE,
    SUPERVISOR_VETO_NO_TARGET_PATCH_TYPE,
    SUPERVISOR_VETO_SKELETON_NOT_AVAILABLE,
    SUPERVISOR_VETO_REQUIRED_PATCH_MISSING,
}


# ---------------------------------------------------------------------------
# LLM client protocol + factory
# ---------------------------------------------------------------------------

class RunSupervisorLLMClient(Protocol):
    def decide(
        self,
        supervisor_input: RunSupervisorInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]: ...


class _CallableRunSupervisorClient:
    """Adapt any callable or object with a ``.decide`` method."""

    def __init__(self, fn: Any):
        if hasattr(fn, "decide"):
            self._fn = fn.decide
        else:
            self._fn = fn

    def decide(
        self,
        supervisor_input: RunSupervisorInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]:
        return self._fn(supervisor_input, prompt=prompt, json_schema=json_schema)


class FakeRunSupervisorClient:
    """Deterministic supervisor that picks an action based on input state.

    Never reads evaluation expected actions — purely state-driven.
    """

    def decide(
        self,
        supervisor_input: RunSupervisorInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        from openmc_agent.run_supervisor_policy import (
            determine_deterministic_supervisor_action,
        )

        decision = determine_deterministic_supervisor_action(supervisor_input)
        return decision.model_dump(mode="json")


def make_run_supervisor_client(
    *,
    llm: Any | None = None,
    model_name: str | None = None,
    temperature: float = 0.0,
    output_mode: Literal["auto", "json_schema", "json_object", "plain_prompt"] = "auto",
) -> RunSupervisorLLMClient:
    """Build a real LLM-backed supervisor client.

    Requires ``llm`` (an OpenAI-compatible chat callable).
    Falls back to :class:`FakeRunSupervisorClient` if ``llm`` is ``None``.
    """
    if llm is None:
        return FakeRunSupervisorClient()
    return _CallableRunSupervisorClient(llm)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def run_supervisor_decision(
    supervisor_input: RunSupervisorInput,
    *,
    mode: RunSupervisorMode = RunSupervisorMode.ADVISORY,
    client: RunSupervisorLLMClient | None = None,
    model_name: str | None = None,
    allow_fallback: bool = True,
) -> RunSupervisorResult:
    """Run one supervisor decision cycle.

    Flow:
    1. If mode is OFF, return immediately.
    2. If no client, use fake client (deterministic).
    3. Build prompt, call client (retry once on parse failure).
    4. Validate decision against allowed_actions and veto rules.
    5. Detect loops and check retry budget.
    6. If vetoed and fallback allowed, use deterministic fallback.
    7. Return result with all metadata.
    """
    from openmc_agent.run_supervisor_policy import (
        detect_supervisor_loop,
        validate_supervisor_decision,
    )
    from openmc_agent.run_supervisor_prompts import build_run_supervisor_prompt

    started = time.perf_counter()

    if mode == RunSupervisorMode.OFF:
        return RunSupervisorResult(
            decision_id=supervisor_input.decision_id,
            mode=mode,
            state_fingerprint=supervisor_input.state_fingerprint,
            duration_ms=0.0,
        )

    fallback_used = False
    if client is None:
        client = FakeRunSupervisorClient()
        fallback_used = True

    prompt = build_run_supervisor_prompt(supervisor_input)
    schema = RunSupervisorDecision.model_json_schema()

    warnings: list[str] = []
    raw_response_chars: int | None = None
    decision: RunSupervisorDecision | None = None

    for attempt in range(2):
        try:
            raw = client.decide(supervisor_input, prompt=prompt, json_schema=schema)
            raw_response_chars = len(raw) if isinstance(raw, str) else len(
                json.dumps(raw, ensure_ascii=False)
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and "decision_id" not in data:
                data["decision_id"] = supervisor_input.decision_id
            decision = RunSupervisorDecision.model_validate(data)
            if model_name and decision.confidence > 0:
                pass
            break
        except Exception as exc:
            warnings.append(f"supervisor attempt {attempt + 1} failed: {exc}")

    if decision is None:
        if allow_fallback:
            from openmc_agent.run_supervisor_policy import (
                determine_deterministic_supervisor_action,
            )

            decision = determine_deterministic_supervisor_action(supervisor_input)
            fallback_used = True
            warnings.append("supervisor.llm_fallback_used")
        else:
            return RunSupervisorResult(
                decision_id=supervisor_input.decision_id,
                mode=mode,
                proposed_decision=None,
                state_fingerprint=supervisor_input.state_fingerprint,
                fallback_used=False,
                warnings=warnings,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    veto_reasons = validate_supervisor_decision(decision, supervisor_input)

    looped = detect_supervisor_loop(
        fingerprint=supervisor_input.state_fingerprint,
        proposed_action=decision.action,
        target_patch_type=decision.target_patch_type,
        history=supervisor_input.recent_actions,
    )
    if looped:
        veto_reasons.append(SUPERVISOR_VETO_LOOP_DETECTED)

    accepted = not veto_reasons
    final_action: RunSupervisorAction | None = None

    if accepted:
        final_action = decision.action
    elif allow_fallback:
        from openmc_agent.run_supervisor_policy import (
            determine_deterministic_supervisor_action,
        )

        fb = determine_deterministic_supervisor_action(supervisor_input)
        fb_vetoes = validate_supervisor_decision(fb, supervisor_input)
        if not fb_vetoes:
            final_action = fb.action
            fallback_used = True
        else:
            final_action = RunSupervisorAction.STOP
            fallback_used = True
            warnings.append("supervisor.fallback_also_vetoed")

    result = RunSupervisorResult(
        decision_id=supervisor_input.decision_id,
        mode=mode,
        proposed_decision=decision if decision else None,
        final_action=final_action,
        accepted=accepted,
        executed=accepted and mode == RunSupervisorMode.CONTROLLED_ROUTE,
        vetoed=not accepted,
        veto_reasons=veto_reasons,
        warnings=warnings + (
            [f"raw_response_chars={raw_response_chars}"]
            if raw_response_chars is not None
            else []
        ),
        supervisor="llm" if not fallback_used else "deterministic",
        model=model_name if not fallback_used else None,
        fallback_used=fallback_used,
        state_fingerprint=supervisor_input.state_fingerprint,
        duration_ms=(time.perf_counter() - started) * 1000,
        raw_response_chars=raw_response_chars,
    )
    return result


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def write_run_supervisor_artifacts(
    root_dir: str,
    supervisor_input: RunSupervisorInput,
    result: RunSupervisorResult,
    *,
    prompt: str | None = None,
    raw_response: str | None = None,
) -> None:
    """Write supervisor artifacts to ``root_dir``.

    Multi-round runs are saved under ``decisions/NNN/`` subdirectories.
    """
    from pathlib import Path

    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)

    history = supervisor_input.recent_actions
    round_idx = len(history)

    if round_idx == 0:
        target = root
    else:
        target = root / "decisions" / f"{round_idx:03d}"
        target.mkdir(parents=True, exist_ok=True)

    (target / "input.json").write_text(
        json.dumps(supervisor_input.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if result.proposed_decision:
        (target / "proposed_decision.json").write_text(
            json.dumps(
                result.proposed_decision.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    (target / "result.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    full_history = [*history, {
        "decision_id": result.decision_id,
        "proposed_action": result.proposed_decision.action.value if result.proposed_decision else None,
        "final_action": result.final_action.value if result.final_action else None,
        "accepted": result.accepted,
        "vetoed": result.vetoed,
        "veto_reasons": result.veto_reasons,
        "fallback_used": result.fallback_used,
        "state_fingerprint": result.state_fingerprint,
    }]
    (root / "action_history.json").write_text(
        json.dumps(full_history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if prompt is not None:
        (target / "prompt.txt").write_text(prompt, encoding="utf-8")
    if raw_response is not None:
        (target / "raw_response.txt").write_text(raw_response, encoding="utf-8")


# ---------------------------------------------------------------------------
# Compact plan summary helper
# ---------------------------------------------------------------------------

def _compact_plan_summary(plan: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Extract a compact summary from a plan dict/model (no full pin maps)."""
    from openmc_agent.run_supervisor_policy import _dump

    plan_dict = _dump(plan)
    if not plan_dict:
        return {}
    cap = plan_dict.get("capability_report") or {}
    cm = plan_dict.get("complex_model") or {}
    return {
        "has_model_spec": bool(plan_dict.get("model_spec")),
        "has_complex_model": bool(cm),
        "complex_kind": cm.get("kind") if isinstance(cm, dict) else None,
        "renderability": cap.get("renderability"),
        "supported_renderer": cap.get("supported_renderer"),
        "is_executable": cap.get("is_executable"),
        "unsupported_subsystems": cap.get("unsupported_subsystems", []),
        "required_human_confirmations": cap.get("required_human_confirmations", []),
    }
