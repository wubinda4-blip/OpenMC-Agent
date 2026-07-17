"""Deterministic, resumable owner-patch retry controller.

The controller never asks an LLM to choose an owner or an action.  Providers
may produce a candidate only after the typed request and execution plan have
already constrained its target.  Candidate generation is injected so the
kernel is safe to exercise with deterministic fake clients.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH, PlanPatchDependencyGraph
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.planning_scope import build_canonical_task_plan
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.plan_builder.validators import validate_patch

from .fingerprints import canonical_json_dumps, compute_candidate_hash
from .models import PLAN_CLOSED_LOOP_CONTRACT_VERSION, PlanClosedLoopPolicy, PlanGateId, PlanLoopMode, PlanStageStatus
from .retry_models import (
    ExecutablePlanRetryRequest,
    PlanRetryAction,
    RetryExecutionOutcome,
    RetryExecutionPlan,
    RetryExecutionStatus,
    RetryRoundRecord,
    RetryTargetSpec,
    RetryTriggerOrigin,
)
from .retry_owner_policy import retry_owner_policy


def _valid_envelope(state: PlanBuildState, patch_type: str) -> PlanPatchEnvelope | None:
    matches = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
    return matches[0] if len(matches) == 1 else None


def _patch_hash(state: PlanBuildState, patch_type: str) -> str | None:
    env = _valid_envelope(state, patch_type)
    return compute_candidate_hash(target_patch_type=patch_type, candidate_patch=env.content) if env else None


def _state_hash(state: PlanBuildState) -> str:
    payload = {key: {"type": value.patch_type, "status": value.status, "content": value.content} for key, value in sorted(state.patches.items())}
    return hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()


def normalize_retry_request(
    source: Any,
    *,
    state: PlanBuildState,
    origin: RetryTriggerOrigin = RetryTriggerOrigin.DETERMINISTIC_PREFLIGHT,
) -> ExecutablePlanRetryRequest | None:
    """Normalize legacy retries, root causes, and issue dictionaries.

    The registry is the sole authority for owner/action selection.  A supplied
    owner, LLM text, or legacy free-form reason is retained only as evidence.
    """
    if isinstance(source, ExecutablePlanRetryRequest):
        return source
    data = source.model_dump(mode="json") if hasattr(source, "model_dump") else dict(source or {})
    codes = list(data.get("source_issue_codes") or data.get("issue_codes") or data.get("original_issue_codes") or ([data["code"]] if data.get("code") else []))
    if not codes:
        return None
    code = next((item for item in codes if retry_owner_policy(str(item), data) is not None), None)
    if code is None:
        state.add_event("planning.retry_request_rejected", "retry request rejected: unregistered issue", {"issue_codes": codes})
        return None
    policy = retry_owner_policy(code, data)
    assert policy is not None
    owner_hashes = {owner: _patch_hash(state, owner) for owner in policy.owner_patch_types if owner != "planning_task_plan"}
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner),
            required_ids=[str(value) for value in data.get("required_ids", [])],
            affected_json_paths=[str(value) for value in data.get("affected_json_paths", [])],
            protected_json_paths=policy.protected_json_paths,
            required_properties=[str(value) for value in ([data.get("required_property")] if data.get("required_property") else data.get("required_properties", []))],
            source_finding_ids=[str(value) for value in data.get("finding_ids", data.get("source_finding_ids", []))],
            source_issue_codes=codes,
            metadata={"legacy_source_type": type(source).__name__},
        )
        for owner in policy.owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=f"retry_{uuid4().hex[:16]}", protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=origin, gate_id=(PlanGateId.PLACEMENT if origin is RetryTriggerOrigin.PLACEMENT_GATE else (PlanGateId.FACTS if origin is RetryTriggerOrigin.FACTS_GATE else None)),
        action=policy.preferred_action, owner_patch_types=policy.owner_patch_types, targets=targets,
        source_finding_ids=[str(value) for value in data.get("finding_ids", data.get("source_finding_ids", []))],
        source_issue_codes=codes, evidence_refs=[str(value) for value in data.get("evidence_refs", [])],
        reason_code=code, canonical_task_plan_hash=(state.canonical_task_plan.plan_hash if state.canonical_task_plan else None),
        planning_scope_hash=(state.resolved_planning_scope.canonical_hash if state.resolved_planning_scope else None),
        gate_input_hash=str(data.get("gate_input_hash") or "") or None,
        priority={"facts": 10, "planning_task_plan": 30, "materials": 40, "universes": 50}.get(policy.owner_patch_types[0], 60),
        requires_human=bool(data.get("requires_human", False)), repairable=policy.preferred_action is not PlanRetryAction.FAIL_CLOSED,
        created_round=len(state.plan_retry_rounds), metadata={"legacy_payload": {key: value for key, value in data.items() if key not in {"message", "reason"}}},
    )
    state.plan_retry_requests[request.request_id] = request
    if request.request_id not in state.plan_retry_pending_request_ids:
        state.plan_retry_pending_request_ids.append(request.request_id)
    state.add_event("planning.retry_request_normalized", "retry request normalized by deterministic owner policy", {"request_id": request.request_id, "fingerprint": request.request_fingerprint, "owner_patch_types": request.owner_patch_types})
    return request


def compile_retry_execution_plan(
    request: ExecutablePlanRetryRequest,
    state: PlanBuildState,
    policy: PlanClosedLoopPolicy,
    dependency_graph: PlanPatchDependencyGraph = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH,
) -> RetryExecutionPlan:
    owner_policy = retry_owner_policy(request.reason_code)
    if owner_policy is None:
        raise ValueError("planning.retry_request_unsupported")
    if request.canonical_task_plan_hash and state.canonical_task_plan and request.canonical_task_plan_hash != state.canonical_task_plan.plan_hash:
        raise ValueError("planning.retry_request_stale")
    if request.planning_scope_hash and state.resolved_planning_scope and request.planning_scope_hash != state.resolved_planning_scope.canonical_hash:
        raise ValueError("planning.retry_request_stale")
    for target in request.targets:
        if target.patch_type == "planning_task_plan":
            continue
        current = _patch_hash(state, target.patch_type)
        if target.current_patch_hash and current != target.current_patch_hash:
            raise ValueError("planning.retry_request_stale")
    attempts = state.plan_retry_attempts_by_fingerprint.get(request.request_fingerprint, 0)
    if attempts >= min(policy.max_attempts_per_retry_request, owner_policy.max_attempts):
        raise ValueError("planning.retry_budget_exhausted")
    owner_types = [item for item in request.owner_patch_types if item != "planning_task_plan"]
    invalidated = dependency_graph.transitive_dependents(owner_types) if owner_types else []
    gates = dependency_graph.gates_affected_by_patch_types(invalidated or owner_types)
    if request.action is PlanRetryAction.RECOMPUTE_TASK_PLAN:
        invalidated = list(state.canonical_task_plan.ordered_patch_types) if state.canonical_task_plan else []
        gates = [PlanGateId.PLACEMENT, PlanGateId.ASSEMBLED_PLAN]
    plan_payload = {"request": request.request_fingerprint, "owners": owner_types, "invalidated": invalidated, "gates": [gate.value for gate in gates], "action": request.action.value}
    fingerprint = hashlib.sha256(canonical_json_dumps(plan_payload).encode("utf-8")).hexdigest()
    plan = RetryExecutionPlan(
        execution_id=f"retry_exec_{uuid4().hex[:16]}", request_id=request.request_id,
        owner_patch_types=request.owner_patch_types, invalidation_patch_types=invalidated,
        gates_to_invalidate=gates, gates_to_replay=gates,
        earliest_resume_patch_type=dependency_graph.earliest_patch_type(invalidated), candidate_strategy=request.action,
        validation_steps=owner_policy.required_acceptance_checks, execution_fingerprint=fingerprint,
        budget_snapshot={"request_attempts": attempts, "retry_rounds": len(state.plan_retry_rounds), "retry_llm_calls": int(state.plan_retry_budget.get("llm_calls", 0))},
    )
    state.plan_retry_execution_plans[plan.execution_id] = plan
    state.add_event("planning.retry_execution_plan_compiled", "retry execution plan compiled", {"execution_id": plan.execution_id, "request_id": request.request_id, "invalidation_patch_types": invalidated})
    return plan


def invalidate_gates_for_patch_change(state: PlanBuildState, patch_types: list[str], graph: PlanPatchDependencyGraph = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH) -> list[PlanGateId]:
    gates = graph.gates_affected_by_patch_types(patch_types)
    for gate in gates:
        stage = state.plan_loop_stages.get(f"plan_gate_{gate.value}")
        if stage is not None:
            stage.status = PlanStageStatus.PENDING
            stage.completed_at = None
            stage.metadata["invalidated_by_patch_types"] = sorted(set(patch_types))
            state.plan_retry_gate_replay_counts[gate.value] = state.plan_retry_gate_replay_counts.get(gate.value, 0) + 1
            state.add_event("planning.retry_gate_invalidated", "gate invalidated after owner patch change", {"gate_id": gate.value, "patch_types": patch_types})
    return gates


def _atomic_owner_commit(state: PlanBuildState, clone: PlanBuildState, owner_patch_types: list[str], request: ExecutablePlanRetryRequest, candidate_hashes: dict[str, str]) -> None:
    original = state.model_dump(mode="json")
    try:
        for patch_type in owner_patch_types:
            old = _valid_envelope(state, patch_type)
            new = _valid_envelope(clone, patch_type)
            if old is None or new is None:
                raise ValueError(f"retry owner patch unavailable: {patch_type}")
            old.status = "superseded"
            replacement = new.model_copy(deep=True)
            replacement.patch_id = f"{old.patch_id}_retry_{request.request_fingerprint[:12]}"
            replacement.source = "retry"
            replacement.status = "valid"
            replacement.metadata = {**replacement.metadata, "retry_request_id": request.request_id, "retry_candidate_hash": candidate_hashes[patch_type]}
            state.add_patch(replacement)
        state.assembled_plan = None
        state.add_event("planning.retry_owner_commit_completed", "retry owner patch committed atomically", {"request_id": request.request_id, "owners": owner_patch_types})
    except Exception:
        restored = PlanBuildState.model_validate(original)
        state.__dict__.update(restored.__dict__)
        state.add_event("planning.retry_owner_commit_rolled_back", "retry owner commit rolled back", {"request_id": request.request_id})
        raise


CandidateProducer = Callable[[ExecutablePlanRetryRequest, RetryExecutionPlan, PlanBuildState], dict[str, dict[str, Any]]]
CandidateValidator = Callable[[ExecutablePlanRetryRequest, RetryExecutionPlan, PlanBuildState], list[dict[str, Any]]]
DownstreamResumer = Callable[[PlanBuildState, RetryExecutionPlan], list[str]]


def execute_plan_retry_loop(
    *,
    state: PlanBuildState,
    policy: PlanClosedLoopPolicy,
    candidate_producer: CandidateProducer | None = None,
    candidate_validator: CandidateValidator | None = None,
    downstream_resumer: DownstreamResumer | None = None,
    dependency_graph: PlanPatchDependencyGraph = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH,
) -> RetryExecutionOutcome:
    """Execute one highest-priority request at a time, never a full replanning.

    Callers inject generation/review functions.  This keeps the transactional
    core provider-neutral while allowing the incremental executor to reuse its
    current target-patch generator and gate runners.
    """
    pending = [state.plan_retry_requests[item] for item in state.plan_retry_pending_request_ids if item in state.plan_retry_requests]
    if not pending:
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.RESOLVED, detail="no pending retry requests")
        state.plan_retry_outcome = outcome
        return outcome
    request = sorted(pending, key=lambda item: (item.priority, item.request_fingerprint))[0]
    if policy.mode is PlanLoopMode.OFF:
        return RetryExecutionOutcome(status=RetryExecutionStatus.BLOCKED, request_id=request.request_id, detail="retry loop disabled in off mode")
    try:
        plan = compile_retry_execution_plan(request, state, policy, dependency_graph)
    except ValueError as exc:
        code = str(exc)
        status = RetryExecutionStatus.BUDGET_EXHAUSTED if "budget" in code else (RetryExecutionStatus.UNSUPPORTED_REQUEST if "unsupported" in code else RetryExecutionStatus.BLOCKED)
        outcome = RetryExecutionOutcome(status=status, request_id=request.request_id, detail=code)
        state.plan_retry_outcome = outcome
        return outcome
    if policy.mode is PlanLoopMode.ADVISORY:
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.RETRY_PLAN_RECORDED, request_id=request.request_id, execution_id=plan.execution_id, detail="advisory retry plan recorded", workflow_behavior_changed=False)
        state.plan_retry_outcome = outcome
        return outcome
    if request.requires_human:
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.AWAITING_HUMAN, request_id=request.request_id, execution_id=plan.execution_id, detail="retry request requires human confirmation")
        state.plan_retry_outcome = outcome
        state.add_event("planning.retry_awaiting_human", outcome.detail, {"request_id": request.request_id})
        return outcome
    if request.action is PlanRetryAction.RECOMPUTE_TASK_PLAN:
        if state.resolved_planning_scope is None:
            outcome = RetryExecutionOutcome(status=RetryExecutionStatus.BLOCKED, request_id=request.request_id, detail="cannot recompute task plan without resolved scope")
            state.plan_retry_outcome = outcome
            return outcome
        contract = state.planning_feature_contract
        if contract is None:
            outcome = RetryExecutionOutcome(status=RetryExecutionStatus.BLOCKED, request_id=request.request_id, detail="cannot recompute task plan without feature contract")
            state.plan_retry_outcome = outcome
            return outcome
        facts_env = _valid_envelope(state, "facts")
        state.canonical_task_plan = build_canonical_task_plan(
            scope=state.resolved_planning_scope,
            contract=contract,
            facts_patch=facts_env.content if facts_env else {},
            feature_order=list(dependency_graph._ORDER),
        )
        invalidated = state.invalidate_patch_types(plan.invalidation_patch_types, reason="canonical task-plan recomputed", issues=[{"code": request.reason_code}])
        gates = invalidate_gates_for_patch_change(state, plan.invalidation_patch_types, dependency_graph)
        record = RetryRoundRecord(round_index=len(state.plan_retry_rounds), request=request, execution_plan=plan, state_hash_before=_state_hash(state), invalidated_patch_types=plan.invalidation_patch_types, regenerated_patch_types=[], gates_replayed=gates, outcome=RetryExecutionStatus.RESUMED)
        state.plan_retry_rounds.append(record)
        state.plan_retry_pending_request_ids.remove(request.request_id)
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.RESUMED, request_id=request.request_id, execution_id=plan.execution_id, detail="canonical task plan recomputed", workflow_behavior_changed=True, metadata={"invalidated_patch_ids": invalidated})
        state.plan_retry_outcome = outcome
        return outcome
    if candidate_producer is None:
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, execution_id=plan.execution_id, detail="no owner candidate producer registered")
        state.plan_retry_outcome = outcome
        return outcome
    before = _state_hash(state)
    clone = state.model_copy(deep=True)
    try:
        candidates = candidate_producer(request, plan, clone)
    except Exception as exc:
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, execution_id=plan.execution_id, detail=f"candidate generation failed: {exc}")
        state.plan_retry_outcome = outcome
        return outcome
    hashes: dict[str, str] = {}
    for patch_type, content in candidates.items():
        if patch_type not in request.owner_patch_types:
            outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, detail=f"candidate targets non-owner patch {patch_type}")
            state.plan_retry_outcome = outcome
            return outcome
        env = _valid_envelope(clone, patch_type)
        if env is None:
            outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, detail=f"candidate owner patch missing: {patch_type}")
            state.plan_retry_outcome = outcome
            return outcome
        parsed = parse_patch_content(patch_type, content)
        validation = validate_patch(parsed)
        if not validation.ok:
            outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, detail=f"candidate validation failed: {patch_type}")
            state.plan_retry_outcome = outcome
            return outcome
        env.content = content
        hashes[patch_type] = compute_candidate_hash(target_patch_type=patch_type, candidate_patch=content)
    candidate_group_hash = hashlib.sha256(canonical_json_dumps(hashes).encode("utf-8")).hexdigest()
    prior = state.plan_retry_candidate_hashes_by_fingerprint.setdefault(request.request_fingerprint, [])
    duplicate = candidate_group_hash in prior
    prior.append(candidate_group_hash)
    state.plan_retry_attempts_by_fingerprint[request.request_fingerprint] = state.plan_retry_attempts_by_fingerprint.get(request.request_fingerprint, 0) + 1
    if duplicate:
        record = RetryRoundRecord(round_index=len(state.plan_retry_rounds), request=request, execution_plan=plan, state_hash_before=before, candidate_hashes=hashes, outcome=RetryExecutionStatus.NO_PROGRESS, error="duplicate candidate")
        state.plan_retry_rounds.append(record)
        state.plan_retry_cycle_trace.append({"request_fingerprint": request.request_fingerprint, "candidate_hash": candidate_group_hash, "reason": "duplicate_candidate"})
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.NO_PROGRESS, request_id=request.request_id, execution_id=plan.execution_id, detail="duplicate owner candidate; retry stopped")
        state.plan_retry_outcome = outcome
        state.add_event("planning.retry_no_progress", outcome.detail, {"request_id": request.request_id})
        return outcome
    validation_issues = candidate_validator(request, plan, clone) if candidate_validator else []
    if any(item.get("severity", "error") == "error" for item in validation_issues):
        record = RetryRoundRecord(round_index=len(state.plan_retry_rounds), request=request, execution_plan=plan, state_hash_before=before, candidate_hashes=hashes, outcome=RetryExecutionStatus.FAILED, error="owner-specific acceptance failed", remaining_issue_codes=[str(item.get("code")) for item in validation_issues])
        state.plan_retry_rounds.append(record)
        outcome = RetryExecutionOutcome(status=RetryExecutionStatus.FAILED, request_id=request.request_id, execution_id=plan.execution_id, detail="owner-specific acceptance failed")
        state.plan_retry_outcome = outcome
        return outcome
    _atomic_owner_commit(state, clone, [item for item in request.owner_patch_types if item != "planning_task_plan"], request, hashes)
    invalidated = dependency_graph.transitive_dependents([item for item in request.owner_patch_types if item != "planning_task_plan"])
    # The committed owner is valid; only true dependents are invalidated.
    dependent_only = [item for item in invalidated if item not in request.owner_patch_types]
    state.invalidate_patch_types(dependent_only, reason=f"retry owner commit {request.request_id}", issues=[{"code": request.reason_code}])
    gates = invalidate_gates_for_patch_change(state, invalidated, dependency_graph)
    regenerated = downstream_resumer(state, plan) if downstream_resumer else []
    state.plan_retry_owner_regenerations.update({patch: state.plan_retry_owner_regenerations.get(patch, 0) + 1 for patch in hashes})
    state.plan_retry_pending_request_ids.remove(request.request_id)
    record = RetryRoundRecord(round_index=len(state.plan_retry_rounds), request=request, execution_plan=plan, state_hash_before=before, owner_hashes_before={target.patch_type: target.current_patch_hash or "" for target in request.targets}, candidate_hashes=hashes, owner_hashes_after={patch: _patch_hash(state, patch) or "" for patch in hashes}, invalidated_patch_types=dependent_only, regenerated_patch_types=regenerated, gates_replayed=gates, resolved_issue_codes=[request.reason_code], outcome=RetryExecutionStatus.RESUMED)
    state.plan_retry_rounds.append(record)
    outcome = RetryExecutionOutcome(status=RetryExecutionStatus.RESUMED, request_id=request.request_id, execution_id=plan.execution_id, detail="owner committed; downstream rebuild is resumable", workflow_behavior_changed=True)
    state.plan_retry_outcome = outcome
    state.add_event("planning.retry_downstream_resume_completed", outcome.detail, {"request_id": request.request_id, "invalidated_patch_types": dependent_only, "regenerated_patch_types": regenerated})
    return outcome
