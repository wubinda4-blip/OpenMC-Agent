"""Typed, lossless builders for :class:`ExecutablePlanRetryRequest`.

Phase 3A only had a single ``normalize_retry_request`` entry point that
accepted free-form dicts and silently dropped structured fields (required
IDs, affected JSON paths, gate input hashes, evidence refs).  These builders
preserve every deterministic field so owner producers and acceptance checks
have the information they need to actually repair a patch.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH
from openmc_agent.plan_builder.state import PlanBuildState

from .fingerprints import canonical_json_dumps, compute_candidate_hash
from .models import PLAN_CLOSED_LOOP_CONTRACT_VERSION, PlanGateId
from .retry_models import (
    ExecutablePlanRetryRequest,
    PlanRetryAction,
    RetryTargetSpec,
    RetryTriggerOrigin,
    SpecialRetryRoute,
)
from .retry_owner_policy import RetryOwnerPolicy, retry_owner_policy


def _owner_hashes(state: PlanBuildState, owner_patch_types: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for patch_type in owner_patch_types:
        if patch_type == "planning_task_plan":
            continue
        envelopes = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
        if len(envelopes) == 1:
            hashes[patch_type] = compute_candidate_hash(target_patch_type=patch_type, candidate_patch=envelopes[0].content)
    return hashes


def _scope_hash(state: PlanBuildState) -> str | None:
    return state.resolved_planning_scope.canonical_hash if state.resolved_planning_scope else None


def _task_plan_hash(state: PlanBuildState) -> str | None:
    return state.canonical_task_plan.plan_hash if state.canonical_task_plan else None


def _register(request: ExecutablePlanRetryRequest, state: PlanBuildState) -> ExecutablePlanRetryRequest:
    """Idempotently register a typed request.

    A request with the same fingerprint that is still active is returned as-is
    instead of creating a duplicate.  Terminal requests with the same
    fingerprint are superseded so a fresh attempt can be scheduled.
    """
    for existing in state.plan_retry_requests.values():
        if existing.request_fingerprint == request.request_fingerprint and existing.lifecycle.value not in TERMINAL_RETRY_LIFECYCLE_STATES:
            state.add_event(
                "planning.retry_request_deduplicated",
                "retry request with matching fingerprint already active",
                {"request_id": existing.request_id, "fingerprint": request.request_fingerprint},
            )
            return existing
    state.plan_retry_requests[request.request_id] = request
    if request.request_id not in state.plan_retry_pending_request_ids:
        state.plan_retry_pending_request_ids.append(request.request_id)
    state.add_event(
        "planning.retry_request_registered",
        "typed retry request registered",
        {
            "request_id": request.request_id,
            "fingerprint": request.request_fingerprint,
            "owner_patch_types": request.owner_patch_types,
            "reason_code": request.reason_code,
            "origin": request.origin.value,
        },
    )
    return request


def _new_request_id() -> str:
    return f"retry_{uuid4().hex[:16]}"


def build_retry_request_from_placement_dependency(
    *,
    dependency_patch_type: str,
    issue_codes: list[str],
    finding_ids: list[str],
    required_ids: list[str],
    reason: str,
    state: PlanBuildState,
    downstream_patch_types: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    gate_input_hash: str | None = None,
    consumer_ids: list[str] | None = None,
    affected_json_paths: list[str] | None = None,
) -> ExecutablePlanRetryRequest | None:
    """Placement Gate dependency on a prior gate (Facts or Universes).

    Preserves the dependency patch type, the exact required IDs and the gate
    input hash so the Universes producer can be told *which* IDs to generate.
    """
    code = next((item for item in issue_codes if retry_owner_policy(str(item), {"code": item}) is not None), None)
    if code is None:
        state.add_event("planning.retry_request_rejected", "placement dependency has no registered owner", {"issue_codes": issue_codes})
        return None
    policy = retry_owner_policy(code, {"code": code})
    assert policy is not None
    if _reject_special_route(policy, issue_codes, state):
        return None
    owner_patch_types = [item for item in policy.owner_patch_types if item != "planning_task_plan"]
    owner_hashes = _owner_hashes(state, owner_patch_types)
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner),
            required_ids=list(required_ids),
            affected_json_paths=list(affected_json_paths or []),
            protected_json_paths=policy.protected_json_paths,
            required_properties=list(policy.required_acceptance_checks),
            source_finding_ids=list(finding_ids),
            source_issue_codes=list(issue_codes),
            dependency_depth=1,
            metadata={"dependency_patch_type": dependency_patch_type, "downstream_patch_types": list(downstream_patch_types or [])},
        )
        for owner in owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=_new_request_id(),
        protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=RetryTriggerOrigin.PLACEMENT_GATE,
        gate_id=PlanGateId.PLACEMENT,
        action=policy.preferred_action,
        owner_patch_types=owner_patch_types,
        targets=targets,
        source_finding_ids=list(finding_ids),
        source_issue_codes=list(issue_codes),
        evidence_refs=list(evidence_refs or []),
        reason_code=code,
        canonical_task_plan_hash=_task_plan_hash(state),
        planning_scope_hash=_scope_hash(state),
        gate_input_hash=gate_input_hash,
        priority={"facts": 10, "materials": 20, "universes": 30}.get(owner_patch_types[0], 60) if owner_patch_types else 60,
        requires_human=False,
        repairable=policy.preferred_action is not PlanRetryAction.FAIL_CLOSED,
        created_round=len(state.plan_retry_rounds),
        owner_patch_hashes=owner_hashes,
        consumer_ids=list(consumer_ids or []),
        metadata={"reason": reason, "dependency_patch_type": dependency_patch_type},
    )
    return _register(request, state)


def build_retry_request_from_facts_issue(
    *,
    issue_code: str,
    affected_json_paths: list[str],
    finding_ids: list[str],
    state: PlanBuildState,
    expected_value: Any | None = None,
    current_value: Any | None = None,
    evidence_refs: list[str] | None = None,
    confirmed_records: list[dict[str, Any]] | None = None,
    requires_human: bool = False,
    gate_input_hash: str | None = None,
) -> ExecutablePlanRetryRequest | None:
    """Facts owner request from a Facts Gate critic finding."""
    policy = retry_owner_policy(issue_code)
    if policy is None or _reject_special_route(policy, [issue_code], state):
        return None
    owner_patch_types = [item for item in policy.owner_patch_types if item != "planning_task_plan"]
    owner_hashes = _owner_hashes(state, owner_patch_types)
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner),
            affected_json_paths=list(affected_json_paths),
            protected_json_paths=policy.protected_json_paths,
            required_properties=list(policy.required_acceptance_checks),
            source_finding_ids=list(finding_ids),
            source_issue_codes=[issue_code],
            metadata={"expected_value": expected_value, "current_value": current_value},
        )
        for owner in owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=_new_request_id(),
        protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=RetryTriggerOrigin.FACTS_GATE,
        gate_id=PlanGateId.FACTS,
        action=policy.preferred_action,
        owner_patch_types=owner_patch_types,
        targets=targets,
        source_finding_ids=list(finding_ids),
        source_issue_codes=[issue_code],
        evidence_refs=list(evidence_refs or []),
        reason_code=issue_code,
        canonical_task_plan_hash=_task_plan_hash(state),
        planning_scope_hash=_scope_hash(state),
        gate_input_hash=gate_input_hash,
        priority=10,
        requires_human=requires_human,
        repairable=policy.preferred_action is not PlanRetryAction.FAIL_CLOSED,
        created_round=len(state.plan_retry_rounds),
        owner_patch_hashes=owner_hashes,
        human_ambiguity=requires_human,
        metadata={"confirmed_records": list(confirmed_records or [])},
    )
    return _register(request, state)


def build_retry_request_from_material_readiness(
    *,
    material_id: str,
    consumer_ids: list[str],
    required_property: str,
    state: PlanBuildState,
    source_paths: list[str] | None = None,
    issue_code: str = "materials.execution_density_required",
) -> ExecutablePlanRetryRequest | None:
    """Materials owner request from execution-readiness preflight.

    Aggregates all consumers of the same material into a single request so the
    controller never schedules eight independent overlay retries.
    """
    policy = retry_owner_policy(issue_code)
    if policy is None or _reject_special_route(policy, [issue_code], state):
        return None
    owner_patch_types = [item for item in policy.owner_patch_types if item != "planning_task_plan"]
    owner_hashes = _owner_hashes(state, owner_patch_types)
    affected_paths = sorted({f"/materials/{material_id}"}.union(source_paths or set()))
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner),
            required_ids=[material_id],
            affected_json_paths=affected_paths,
            protected_json_paths=policy.protected_json_paths,
            required_properties=[required_property],
            source_issue_codes=[issue_code],
            metadata={"material_id": material_id, "consumer_ids": list(consumer_ids)},
        )
        for owner in owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=_new_request_id(),
        protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=RetryTriggerOrigin.MATERIAL_READINESS,
        action=policy.preferred_action,
        owner_patch_types=owner_patch_types,
        targets=targets,
        source_issue_codes=[issue_code],
        reason_code=issue_code,
        canonical_task_plan_hash=_task_plan_hash(state),
        planning_scope_hash=_scope_hash(state),
        priority=20,
        requires_human=False,
        repairable=True,
        created_round=len(state.plan_retry_rounds),
        owner_patch_hashes=owner_hashes,
        consumer_ids=list(consumer_ids),
        metadata={"material_id": material_id, "required_property": required_property},
    )
    return _register(request, state)


def build_retry_request_from_root_cause(
    *,
    root_cause: Any,
    state: PlanBuildState,
) -> ExecutablePlanRetryRequest | None:
    """Convert a :class:`PlanningRootCause` into a typed retry request."""
    policy = retry_owner_policy(root_cause.code)
    if policy is None or _reject_special_route(policy, [root_cause.code], state):
        return None
    owner_patch_types = [item for item in policy.owner_patch_types if item != "planning_task_plan"]
    owner_hashes = {owner: root_cause.canonical_owner_patch_hashes.get(owner, "") for owner in owner_patch_types}
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner) or None,
            required_ids=list(root_cause.affected_ids),
            protected_json_paths=policy.protected_json_paths,
            required_properties=list(root_cause.metadata.get("required_property", "") and [root_cause.metadata["required_property"]] or []),
            source_issue_codes=list(root_cause.original_issue_codes),
            metadata={"root_cause_id": root_cause.root_cause_id},
        )
        for owner in owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=_new_request_id(),
        protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=RetryTriggerOrigin.ASSEMBLY,
        action=policy.preferred_action,
        owner_patch_types=owner_patch_types,
        targets=targets,
        source_issue_codes=list(root_cause.original_issue_codes),
        reason_code=root_cause.code,
        canonical_task_plan_hash=_task_plan_hash(state),
        planning_scope_hash=_scope_hash(state),
        priority={"facts": 10, "materials": 20, "universes": 30}.get(owner_patch_types[0], 40) if owner_patch_types else 40,
        requires_human=False,
        repairable=True,
        created_round=len(state.plan_retry_rounds),
        owner_patch_hashes=owner_hashes,
        consumer_ids=list(root_cause.affected_ids),
        metadata={"root_cause_id": root_cause.root_cause_id},
    )
    return _register(request, state)


def build_retry_request_from_patch_validation(
    *,
    issue_code: str,
    patch_type: str,
    state: PlanBuildState,
    schema_path: str | None = None,
    message: str = "",
    required_ids: list[str] | None = None,
) -> ExecutablePlanRetryRequest | None:
    """Patch-validation issue that the per-patch validator could not resolve."""
    policy = retry_owner_policy(issue_code, {"patch_type": patch_type})
    if policy is None or _reject_special_route(policy, [issue_code], state):
        return None
    owner_patch_types = [item for item in policy.owner_patch_types if item != "planning_task_plan"]
    owner_hashes = _owner_hashes(state, owner_patch_types)
    targets = [
        RetryTargetSpec(
            patch_type=owner,
            current_patch_hash=owner_hashes.get(owner),
            required_ids=list(required_ids or []),
            affected_json_paths=[schema_path] if schema_path else [],
            protected_json_paths=policy.protected_json_paths,
            source_issue_codes=[issue_code],
            metadata={"patch_type": patch_type, "message": message},
        )
        for owner in owner_patch_types
    ]
    request = ExecutablePlanRetryRequest(
        request_id=_new_request_id(),
        protocol_version=PLAN_CLOSED_LOOP_CONTRACT_VERSION,
        origin=RetryTriggerOrigin.PATCH_VALIDATION,
        action=policy.preferred_action,
        owner_patch_types=owner_patch_types,
        targets=targets,
        source_issue_codes=[issue_code],
        reason_code=issue_code,
        canonical_task_plan_hash=_task_plan_hash(state),
        planning_scope_hash=_scope_hash(state),
        priority=50,
        requires_human=False,
        repairable=True,
        created_round=len(state.plan_retry_rounds),
        owner_patch_hashes=owner_hashes,
        metadata={"patch_type": patch_type},
    )
    return _register(request, state)


from .retry_models import TERMINAL_RETRY_LIFECYCLE_STATES  # noqa: E402  (after defs to avoid import cycle at module load)


def _reject_special_route(policy: Any, issue_codes: list[str], state: PlanBuildState) -> bool:
    """Return True if the policy is a ``SpecialRetryRoute`` (rejected).

    Special routes are not patch retries and cannot be handled by these
    builders.  The caller must route them through the controller's
    ``normalize_retry_request`` instead.
    """
    if isinstance(policy, SpecialRetryRoute):
        state.add_event(
            "planning.retry_request_rejected",
            f"special route {policy.action.value} not supported by patch retry builder",
            {"issue_codes": issue_codes, "route": policy.action.value},
        )
        return True
    return False


__all__ = [
    "build_retry_request_from_placement_dependency",
    "build_retry_request_from_facts_issue",
    "build_retry_request_from_material_readiness",
    "build_retry_request_from_root_cause",
    "build_retry_request_from_patch_validation",
]
