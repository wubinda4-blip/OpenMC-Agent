"""Run supervisor policy: allowed-action computation, veto rules, fingerprint, loop detection.

All functions are pure and deterministic — no LLM calls, no network, no I/O.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from openmc_agent.run_supervisor import (
    RunSupervisorAction,
    RunSupervisorDecision,
    RunSupervisorInput,
    SUPERVISOR_VETO_ACTION_NOT_ALLOWED,
    SUPERVISOR_VETO_DECISION_ID_MISMATCH,
    SUPERVISOR_VETO_HUMAN_CONFIRMATION_BYPASS,
    SUPERVISOR_VETO_INCREMENTAL_NOT_ACTIVE,
    SUPERVISOR_VETO_INVALID_PATCH_TARGET,
    SUPERVISOR_VETO_LOOP_DETECTED,
    SUPERVISOR_VETO_MONOLITHIC_FALLBACK_FORBIDDEN,
    SUPERVISOR_VETO_NO_TARGET_PATCH_TYPE,
    SUPERVISOR_VETO_RENDER_WITH_BLOCKER,
    SUPERVISOR_VETO_REQUIRED_PATCH_MISSING,
    SUPERVISOR_VETO_RETRY_BUDGET_EXHAUSTED,
    SUPERVISOR_VETO_SKELETON_NOT_AVAILABLE,
    SUPERVISOR_VETO_UNSUPPORTED_ACTION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dump(obj: Any) -> dict[str, Any]:
    """Serialize a pydantic model or pass through a dict."""
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    return {}


def _safe_get(data: Any, *keys: str, default: Any = None) -> Any:
    cur = data
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
        if cur is None:
            return default
    return cur


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULT_RUN_SUPERVISOR_CONFIG: dict[str, Any] = {
    "max_decisions": 5,
    "max_patch_retries": 2,
    "max_no_progress_steps": 2,
    "max_same_action_per_fingerprint": 2,
    "allow_monolithic_fallback": False,
}


# ---------------------------------------------------------------------------
# Build supervisor input from workflow state
# ---------------------------------------------------------------------------

def build_run_supervisor_input(
    workflow_state: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    decision_count: int = 0,
    retry_count_by_patch: dict[str, int] | None = None,
    no_progress_count: int = 0,
    action_history: list[dict[str, Any]] | None = None,
) -> RunSupervisorInput:
    """Construct a compact :class:`RunSupervisorInput` from graph state."""
    from uuid import uuid4

    cfg = {**DEFAULT_RUN_SUPERVISOR_CONFIG, **(config or {})}

    # --- validation ---
    vr = _dump(workflow_state.get("validation_report"))
    schema_valid = vr.get("is_valid")
    issues = vr.get("issues") or []
    blocking_codes = [
        i.get("code", "") for i in issues if i.get("severity") == "error"
    ]
    warning_codes = [
        i.get("code", "") for i in issues if i.get("severity") == "warning"
    ]

    # --- incremental ---
    pmd = workflow_state.get("planning_mode_decision") or {}
    planning_mode = pmd.get("mode") if isinstance(pmd, dict) else None
    inc = workflow_state.get("incremental_execution_result") or {}
    pbs = workflow_state.get("plan_build_state") or {}

    patch_status: dict[str, str] = {}
    if isinstance(pbs, dict):
        patch_status = dict(pbs.get("patch_status") or {})
    failed_patch_type = _safe_get(inc, "summary", "failed_patch_type")

    required_patch_types = _extract_required_patch_types(workflow_state)

    # --- semantic audit ---
    sa = workflow_state.get("semantic_audit_result") or {}
    sa_findings = sa.get("findings") or workflow_state.get("semantic_audit_findings") or []
    if isinstance(sa_findings, list) and sa_findings:
        # Keep only compact summaries.
        sa_findings = [
            {
                "finding_code": f.get("finding_code"),
                "severity": str(f.get("severity", "")),
                "suggested_patch_target": f.get("suggested_patch_target"),
                "requires_human_confirmation": f.get("requires_human_confirmation", False),
            }
            for f in sa_findings
            if isinstance(f, dict)
        ]

    # --- repair ---
    repair = workflow_state.get("repair_proposal_result") or {}
    repair_decisions: list[dict[str, Any]] = []
    if repair:
        repair_decisions = [{
            "status": workflow_state.get("repair_proposal_status") or repair.get("status"),
            "resolved_issue_codes": workflow_state.get("repair_resolved_issue_codes") or repair.get("resolved_issue_codes", []),
            "new_issue_codes": workflow_state.get("repair_new_issue_codes") or repair.get("new_issue_codes", []),
            "requires_human_confirmation": repair.get("requires_human_confirmation", False),
        }]

    # --- capability ---
    plan = _dump(workflow_state.get("simulation_plan"))
    cap = plan.get("capability_report") if plan else {}
    if not cap:
        cap = _dump(workflow_state.get("capability_report"))
    renderability = cap.get("renderability")
    supported_renderer = cap.get("supported_renderer")
    capability_summary = {
        "renderability": renderability,
        "supported_renderer": supported_renderer,
        "is_executable": cap.get("is_executable"),
        "unsupported_subsystems": cap.get("unsupported_subsystems", []),
    }

    # --- human confirmation ---
    human_confirmation_required = bool(cap.get("required_human_confirmations"))
    unresolved_fact_gaps: list[str] = []
    for i in issues:
        if i.get("requires_human_confirmation"):
            human_confirmation_required = True
            unresolved_fact_gaps.append(i.get("code", ""))
    for f in (sa.get("findings") or []):
        if isinstance(f, dict) and f.get("requires_human_confirmation"):
            human_confirmation_required = True
    if repair.get("requires_human_confirmation"):
        human_confirmation_required = True

    # --- retry budget ---
    max_retries = cfg["max_patch_retries"]
    retry_used = retry_count_by_patch or {}
    remaining = max(0, max_retries - sum(retry_used.values()))
    retry_budget_by_patch: dict[str, int] = {}
    if failed_patch_type:
        used = retry_used.get(failed_patch_type, 0)
        retry_budget_by_patch[failed_patch_type] = max(0, max_retries - used)

    # Determine allowed retry patch types (failed patches with remaining budget).
    allowed_retry: list[str] = []
    if failed_patch_type and retry_budget_by_patch.get(failed_patch_type, 0) > 0:
        # Check failure is retryable.
        if _is_retryable_failure(failed_patch_type, blocking_codes, warning_codes, issues):
            allowed_retry.append(failed_patch_type)

    # Determine current stage.
    current_stage = _determine_stage(workflow_state)

    # Build compact input.
    supervisor_input = RunSupervisorInput(
        decision_id=f"sup_{uuid4().hex[:12]}",
        current_stage=current_stage,
        planning_mode=planning_mode,
        schema_valid=schema_valid,
        blocking_issue_codes=[c for c in blocking_codes if c],
        warning_issue_codes=[c for c in warning_codes if c],
        patch_status=patch_status,
        required_patch_types=required_patch_types,
        failed_patch_type=failed_patch_type,
        semantic_findings=sa_findings,
        repair_decisions=repair_decisions,
        renderability=renderability,
        supported_renderer=supported_renderer,
        capability_summary=capability_summary,
        human_confirmation_required=human_confirmation_required,
        unresolved_fact_gaps=unresolved_fact_gaps,
        allowed_actions=[],  # filled below
        allowed_retry_patch_types=allowed_retry,
        retry_budget_remaining=remaining,
        retry_budget_by_patch=retry_budget_by_patch,
        recent_actions=action_history or [],
        state_fingerprint="",
    )

    # Compute fingerprint (needs all fields filled first).
    supervisor_input.state_fingerprint = compute_supervisor_state_fingerprint(supervisor_input)

    # Compute allowed actions.
    allowed, _reasons = compute_allowed_supervisor_actions(supervisor_input, config=cfg)
    supervisor_input.allowed_actions = allowed

    return supervisor_input


def _determine_stage(state: Mapping[str, Any]) -> str:
    """Infer the current workflow stage from state."""
    if state.get("simulation_plan") is not None:
        vr = state.get("validation_report")
        if vr is not None:
            return "post_validation"
        return "plan_generated"
    inc = state.get("incremental_execution_result")
    if isinstance(inc, dict) and inc.get("ok") is False:
        return "patch_generation_failed"
    if inc and inc.get("ok"):
        return "incremental_complete"
    return "planning"


def _extract_required_patch_types(state: Mapping[str, Any]) -> list[str]:
    """Extract required patch types from plan_build_state."""
    pbs = state.get("plan_build_state")
    if not isinstance(pbs, dict):
        return []
    tasks = pbs.get("component_tasks") or []
    return [t.get("patch_type") for t in tasks if t.get("patch_type")]


def _is_retryable_failure(
    patch_type: str | None,
    blocking_codes: list[str],
    warning_codes: list[str],
    issues: list[dict[str, Any]],
) -> bool:
    """Check if a patch failure is retryable.

    Non-retryable: fact gaps, human confirmation required, unsupported capability,
    unsafe repair, reference policy violation, repeated identical output.
    """
    if not patch_type:
        return False
    all_codes = set(blocking_codes) | set(warning_codes)
    non_retryable_substrings = (
        "fact_gap",
        "human_confirmation",
        "unsupported",
        "unsafe",
        "reference_policy",
        "monolithic",
        "protected_path",
        "forbidden",
    )
    for code in all_codes:
        for sub in non_retryable_substrings:
            if sub in code.lower():
                return False
    return True


# ---------------------------------------------------------------------------
# Allowed actions computation
# ---------------------------------------------------------------------------

def compute_allowed_supervisor_actions(
    supervisor_input: RunSupervisorInput,
    *,
    config: Mapping[str, Any] | None = None,
) -> tuple[list[RunSupervisorAction], list[str]]:
    """Compute which actions are permitted given the current state.

    Returns ``(allowed_actions, policy_reasons)``.
    """
    cfg = {**DEFAULT_RUN_SUPERVISOR_CONFIG, **(config or {})}
    reasons: list[str] = []
    allowed: list[RunSupervisorAction] = []

    has_blocker = bool(supervisor_input.blocking_issue_codes)
    needs_human = supervisor_input.human_confirmation_required
    schema_ok = supervisor_input.schema_valid is not False
    renderability = supervisor_input.renderability or "none"
    can_render = renderability in {"exportable", "runnable"}
    is_incremental = supervisor_input.planning_mode == "incremental"

    # --- continue_to_render ---
    render_blockers: list[str] = []
    if not schema_ok:
        render_blockers.append("schema_invalid")
    if has_blocker:
        render_blockers.append("blocking_issues")
    if needs_human:
        render_blockers.append("human_confirmation")
    if not can_render:
        render_blockers.append(f"renderability={renderability}")
    # Check required patches missing.
    pending_required = [
        pt for pt in supervisor_input.required_patch_types
        if supervisor_input.patch_status.get(pt) not in {"valid", "repaired", "skipped"}
    ]
    if pending_required:
        render_blockers.append(f"required_patches_pending={pending_required}")
    if not render_blockers:
        allowed.append(RunSupervisorAction.CONTINUE_TO_RENDER)
    else:
        reasons.append(f"continue_to_render blocked: {', '.join(render_blockers)}")

    # --- continue_patch_generation ---
    if is_incremental and pending_required and not needs_human:
        allowed.append(RunSupervisorAction.CONTINUE_PATCH_GENERATION)
    elif not is_incremental:
        reasons.append("continue_patch_generation: incremental not active")
    elif not pending_required:
        reasons.append("continue_patch_generation: no pending required patches")
    elif needs_human:
        reasons.append("continue_patch_generation: human confirmation required")

    # --- retry_patch ---
    failed = supervisor_input.failed_patch_type
    retry_allowed = supervisor_input.allowed_retry_patch_types
    budget = supervisor_input.retry_budget_by_patch.get(failed or "", 0) if failed else 0
    if failed and failed in retry_allowed and budget > 0:
        allowed.append(RunSupervisorAction.RETRY_PATCH)
    elif failed:
        if budget <= 0:
            reasons.append(f"retry_patch: budget exhausted for {failed}")
        elif failed not in retry_allowed:
            reasons.append(f"retry_patch: {failed} not in retryable list")
    else:
        reasons.append("retry_patch: no failed patch type")

    # --- request_human_confirmation ---
    if needs_human:
        allowed.append(RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION)

    # --- downgrade_to_skeleton ---
    if renderability == "skeleton" or (
        not can_render and renderability != "none" and not needs_human
    ):
        allowed.append(RunSupervisorAction.DOWNGRADE_TO_SKELETON)
    elif renderability == "none" and not needs_human and schema_ok:
        # Model semantically expressible but renderer unsupported.
        allowed.append(RunSupervisorAction.DOWNGRADE_TO_SKELETON)
    else:
        reasons.append(f"downgrade_to_skeleton: renderability={renderability}, skeleton not appropriate")

    # --- stop ---
    allowed.append(RunSupervisorAction.STOP)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[RunSupervisorAction] = []
    for a in allowed:
        if a.value not in seen:
            seen.add(a.value)
            deduped.append(a)
    return deduped, reasons


# ---------------------------------------------------------------------------
# Veto rules
# ---------------------------------------------------------------------------

def validate_supervisor_decision(
    decision: RunSupervisorDecision,
    supervisor_input: RunSupervisorInput,
) -> list[str]:
    """Validate a supervisor decision against policy.

    Returns a list of veto codes (empty = accepted).
    """
    vetoes: list[str] = []

    # Decision ID mismatch.
    if decision.decision_id != supervisor_input.decision_id:
        vetoes.append(SUPERVISOR_VETO_DECISION_ID_MISMATCH)

    # Action not in allowed list.
    allowed_values = {a.value for a in supervisor_input.allowed_actions}
    if decision.action.value not in allowed_values:
        vetoes.append(SUPERVISOR_VETO_ACTION_NOT_ALLOWED)

    # Action-specific checks.
    action = decision.action

    if action == RunSupervisorAction.CONTINUE_TO_RENDER:
        if supervisor_input.blocking_issue_codes:
            vetoes.append(SUPERVISOR_VETO_RENDER_WITH_BLOCKER)
        if supervisor_input.human_confirmation_required:
            vetoes.append(SUPERVISOR_VETO_HUMAN_CONFIRMATION_BYPASS)
        pending = [
            pt for pt in supervisor_input.required_patch_types
            if supervisor_input.patch_status.get(pt) not in {"valid", "repaired", "skipped"}
        ]
        if pending:
            vetoes.append(SUPERVISOR_VETO_REQUIRED_PATCH_MISSING)

    elif action == RunSupervisorAction.CONTINUE_PATCH_GENERATION:
        if supervisor_input.planning_mode != "incremental":
            vetoes.append(SUPERVISOR_VETO_INCREMENTAL_NOT_ACTIVE)

    elif action == RunSupervisorAction.RETRY_PATCH:
        if not decision.target_patch_type:
            vetoes.append(SUPERVISOR_VETO_NO_TARGET_PATCH_TYPE)
        elif decision.target_patch_type not in supervisor_input.allowed_retry_patch_types:
            vetoes.append(SUPERVISOR_VETO_INVALID_PATCH_TARGET)
        budget = supervisor_input.retry_budget_by_patch.get(decision.target_patch_type, 0)
        if budget <= 0:
            vetoes.append(SUPERVISOR_VETO_RETRY_BUDGET_EXHAUSTED)

    elif action == RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION:
        pass  # Always allowed when in allowed_actions.

    elif action == RunSupervisorAction.DOWNGRADE_TO_SKELETON:
        renderability = supervisor_input.renderability or "none"
        if renderability == "none" and supervisor_input.human_confirmation_required:
            vetoes.append(SUPERVISOR_VETO_SKELETON_NOT_AVAILABLE)

    elif action == RunSupervisorAction.STOP:
        pass  # Always allowed.

    else:
        vetoes.append(SUPERVISOR_VETO_UNSUPPORTED_ACTION)

    return vetoes


# ---------------------------------------------------------------------------
# State fingerprint
# ---------------------------------------------------------------------------

def compute_supervisor_state_fingerprint(
    supervisor_input: RunSupervisorInput,
) -> str:
    """Compute a stable fingerprint for loop detection.

    Based on stable fields only — no timestamps, UUIDs, or artifact paths.
    """
    stable_fields = {
        "stage": supervisor_input.current_stage,
        "schema_valid": supervisor_input.schema_valid,
        "blocking": sorted(supervisor_input.blocking_issue_codes),
        "warnings": sorted(supervisor_input.warning_issue_codes),
        "patch_status": dict(sorted(supervisor_input.patch_status.items())),
        "failed_patch": supervisor_input.failed_patch_type,
        "semantic_codes": sorted(
            f.get("finding_code", "") for f in supervisor_input.semantic_findings
        ),
        "repair_statuses": [
            d.get("status") for d in supervisor_input.repair_decisions
        ],
        "renderability": supervisor_input.renderability,
        "human_confirmation": supervisor_input.human_confirmation_required,
    }
    raw = json.dumps(stable_fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------

def detect_supervisor_loop(
    *,
    fingerprint: str,
    proposed_action: RunSupervisorAction,
    target_patch_type: str | None,
    history: Sequence[Mapping[str, Any]],
    max_same_action_per_fingerprint: int = 2,
) -> bool:
    """Detect if the same action is repeated on the same state fingerprint.

    Returns ``True`` if the action has been repeated too many times.
    """
    same_fingerprint_actions = [
        h for h in history
        if isinstance(h, Mapping) and h.get("state_fingerprint") == fingerprint
    ]
    same_action_count = 0
    for h in same_fingerprint_actions:
        h_action = h.get("proposed_action") or h.get("final_action")
        if h_action == proposed_action.value:
            # For retry_patch, also check target patch type matches.
            if proposed_action == RunSupervisorAction.RETRY_PATCH:
                h_target = h.get("target_patch_type")
                if h_target == target_patch_type:
                    same_action_count += 1
            else:
                same_action_count += 1
    return same_action_count >= max_same_action_per_fingerprint


# ---------------------------------------------------------------------------
# No-progress detection
# ---------------------------------------------------------------------------

def detect_no_progress(
    current_fingerprint: str,
    history: Sequence[Mapping[str, Any]],
) -> int:
    """Count consecutive decisions where state fingerprint didn't change."""
    if not history:
        return 0
    count = 0
    for h in reversed(history):
        if isinstance(h, Mapping) and h.get("state_fingerprint") == current_fingerprint:
            count += 1
        else:
            break
    return count


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

def determine_deterministic_supervisor_action(
    supervisor_input: RunSupervisorInput,
) -> RunSupervisorDecision:
    """Pick an action using deterministic priority rules.

    Priority:
    0. Structural (agent-fixable) blocker present → repair if budget/pending
       patches, else downgrade to a review-only skeleton. Material confirmations
       must never mask a structural blocker.
    1. Human confirmation needed → request_human_confirmation
    2. Failed patch retryable + budget → retry_patch
    3. Required patches pending → continue_patch_generation
    4. Schema valid + no blocker + renderer → continue_to_render
    5. Can skeleton → downgrade_to_skeleton
    6. Otherwise → stop
    """
    allowed = {a.value: a for a in supervisor_input.allowed_actions}
    decision_id = supervisor_input.decision_id

    def _decision(
        action: RunSupervisorAction,
        rationale: str,
        target: str | None = None,
        confidence: float = 0.85,
    ) -> RunSupervisorDecision:
        return RunSupervisorDecision(
            decision_id=decision_id,
            action=action,
            target_patch_type=target,
            rationale=rationale,
            evidence=[
                _evidence("workflow_history", rationale),
            ],
            confidence=confidence,
        )

    # 0. Structural (agent-fixable) blockers precede human confirmation.
    # Material assumptions must never drive request_human_confirmation when the
    # real execution blocker is a structural defect the agent owns.
    from openmc_agent.capability_blockers import is_structural_blocker_code

    structural_blockers = [
        code for code in supervisor_input.blocking_issue_codes
        if is_structural_blocker_code(code)
    ]
    if structural_blockers:
        pending_patches = [
            pt for pt in supervisor_input.required_patch_types
            if supervisor_input.patch_status.get(pt) not in {"valid", "repaired", "skipped"}
        ]
        if (
            pending_patches
            and RunSupervisorAction.CONTINUE_PATCH_GENERATION.value in allowed
        ):
            return _decision(
                RunSupervisorAction.CONTINUE_PATCH_GENERATION,
                "structural_blocker_precedes_human_confirmation: structural "
                f"blocker(s) {structural_blockers} present with pending repair "
                f"patches {pending_patches}; routing to repair, not to material "
                "confirmation.",
            )
        if RunSupervisorAction.DOWNGRADE_TO_SKELETON.value in allowed:
            return _decision(
                RunSupervisorAction.DOWNGRADE_TO_SKELETON,
                "structural_blocker_precedes_human_confirmation: structural "
                f"blocker(s) {structural_blockers} cannot be resolved by expert "
                "material facts; downgrading to review-only skeleton instead of "
                "requesting human confirmation for non-blocking assumptions.",
                confidence=0.8,
            )
        return _decision(
            RunSupervisorAction.STOP,
            "structural_blocker_precedes_human_confirmation: structural "
            f"blocker(s) {structural_blockers} present and no skeleton route "
            "available; stopping instead of requesting material confirmation.",
            confidence=0.7,
        )

    # 1. Human confirmation.
    if (
        supervisor_input.human_confirmation_required
        and RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION.value in allowed
    ):
        return _decision(
            RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION,
            "Human confirmation required — escalating before any further action.",
        )

    # 2. Retry patch.
    if (
        supervisor_input.failed_patch_type
        and supervisor_input.failed_patch_type in supervisor_input.allowed_retry_patch_types
        and RunSupervisorAction.RETRY_PATCH.value in allowed
    ):
        target = supervisor_input.failed_patch_type
        budget = supervisor_input.retry_budget_by_patch.get(target, 0)
        return _decision(
            RunSupervisorAction.RETRY_PATCH,
            f"Patch '{target}' failed but is retryable (budget={budget}). Retrying locally.",
            target=target,
        )

    # 3. Continue patch generation.
    pending = [
        pt for pt in supervisor_input.required_patch_types
        if supervisor_input.patch_status.get(pt) not in {"valid", "repaired", "skipped"}
    ]
    if pending and RunSupervisorAction.CONTINUE_PATCH_GENERATION.value in allowed:
        return _decision(
            RunSupervisorAction.CONTINUE_PATCH_GENERATION,
            f"Required patches still pending: {pending}. Continuing incremental generation.",
        )

    # 4. Continue to render.
    if RunSupervisorAction.CONTINUE_TO_RENDER.value in allowed:
        return _decision(
            RunSupervisorAction.CONTINUE_TO_RENDER,
            "Schema valid, no blockers, renderer available — proceeding to render.",
            confidence=0.9,
        )

    # 5. Downgrade to skeleton.
    if RunSupervisorAction.DOWNGRADE_TO_SKELETON.value in allowed:
        return _decision(
            RunSupervisorAction.DOWNGRADE_TO_SKELETON,
            f"Renderer unsupported (renderability={supervisor_input.renderability}) — "
            "downgrading to skeleton artifact.",
            confidence=0.7,
        )

    # 6. Stop.
    return _decision(
        RunSupervisorAction.STOP,
        "No safe action available — stopping workflow.",
        confidence=0.6,
    )


def _evidence(source_type: str, summary: str):
    from openmc_agent.run_supervisor import SupervisorEvidence

    return SupervisorEvidence(source_type=source_type, summary=summary)  # type: ignore[arg-type]
