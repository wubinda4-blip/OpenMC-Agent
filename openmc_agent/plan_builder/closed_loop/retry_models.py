"""Typed, deterministic protocol for executable plan dependency retries.

The models in this module carry only Python-resolved ownership and stable
identifiers.  They are intentionally usable without a graph, OpenMC, or an
LLM provider so checkpoints remain safe to replay.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .fingerprints import canonical_json_dumps
from .models import PLAN_CLOSED_LOOP_CONTRACT_VERSION, PlanGateId


class _TextEnum(str, Enum):
    pass


class RetryTriggerOrigin(_TextEnum):
    FACTS_GATE = "facts_gate"
    MATERIAL_UNIVERSE_GATE = "material_universe_gate"
    PLACEMENT_GATE = "placement_gate"
    DETERMINISTIC_PREFLIGHT = "deterministic_preflight"
    MATERIAL_READINESS = "material_readiness"
    PATCH_VALIDATION = "patch_validation"
    PLAN_VALIDATION = "plan_validation"
    ASSEMBLY = "assembly"
    TASK_PLAN_RECONCILIATION = "task_plan_reconciliation"
    HUMAN_RESUME = "human_resume"
    LEGACY_RETRY_ROUTER = "legacy_retry_router"


class PlanRetryAction(_TextEnum):
    REVISE_OWNER_PATCH = "revise_owner_patch"
    REGENERATE_OWNER_PATCH = "regenerate_owner_patch"
    RECOMPUTE_TASK_PLAN = "recompute_task_plan"
    RETRY_DEPENDENCY = "retry_dependency"
    ASK_HUMAN = "ask_human"
    RESUME_DOWNSTREAM = "resume_downstream"
    FAIL_CLOSED = "fail_closed"


class RetryExecutionStatus(_TextEnum):
    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    AWAITING_HUMAN = "awaiting_human"
    BLOCKED = "blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    CYCLE_DETECTED = "cycle_detected"
    UNSUPPORTED_REQUEST = "unsupported_request"
    FAILED = "failed"
    RESUMED = "resumed"
    RETRY_PLAN_RECORDED = "retry_plan_recorded"


class RetryRequestLifecycle(_TextEnum):
    """Lifecycle of a single retry request inside the controller.

    Terminal states (``resolved``/``superseded``/``no_progress``/``blocked``/
    ``failed``) must be removed from the pending list so the next loop pass
    never re-selects a dead request.
    """

    PENDING = "pending"
    EXECUTING = "executing"
    AWAITING_HUMAN = "awaiting_human"
    OWNER_COMMITTED = "owner_committed"
    REBUILDING = "rebuilding"
    REPLAYING = "replaying"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    NO_PROGRESS = "no_progress"
    BLOCKED = "blocked"
    FAILED = "failed"


TERMINAL_RETRY_LIFECYCLE_STATES: frozenset[str] = frozenset(
    {
        RetryRequestLifecycle.RESOLVED.value,
        RetryRequestLifecycle.SUPERSEDED.value,
        RetryRequestLifecycle.NO_PROGRESS.value,
        RetryRequestLifecycle.BLOCKED.value,
        RetryRequestLifecycle.FAILED.value,
    }
)


class RetryTargetSpec(AgentBaseModel):
    patch_type: str
    patch_id: str | None = None
    current_patch_hash: str | None = None
    required_ids: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    protected_json_paths: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)
    source_finding_ids: list[str] = Field(default_factory=list)
    source_issue_codes: list[str] = Field(default_factory=list)
    dependency_depth: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutablePlanRetryRequest(AgentBaseModel):
    request_id: str
    protocol_version: str = PLAN_CLOSED_LOOP_CONTRACT_VERSION
    origin: RetryTriggerOrigin
    gate_id: PlanGateId | None = None
    action: PlanRetryAction
    owner_patch_types: list[str] = Field(default_factory=list)
    targets: list[RetryTargetSpec] = Field(default_factory=list)
    source_finding_ids: list[str] = Field(default_factory=list)
    source_issue_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason_code: str
    canonical_task_plan_hash: str | None = None
    planning_scope_hash: str | None = None
    gate_input_hash: str | None = None
    priority: int = 100
    requires_human: bool = False
    repairable: bool = False
    request_fingerprint: str = ""
    created_round: int = 0
    lifecycle: RetryRequestLifecycle = RetryRequestLifecycle.PENDING
    owner_patch_hashes: dict[str, str | None] = Field(default_factory=dict)
    consumer_ids: list[str] = Field(default_factory=list)
    source_requirement_ids: list[str] = Field(default_factory=list)
    human_ambiguity: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _stable_fingerprint(self) -> "ExecutablePlanRetryRequest":
        import hashlib

        payload = {
            "origin": self.origin.value,
            "gate_id": self.gate_id.value if self.gate_id else None,
            "owner_patch_types": sorted(self.owner_patch_types),
            "source_issue_codes": sorted(self.source_issue_codes),
            "targets": [
                {
                    "patch_type": target.patch_type,
                    "current_patch_hash": target.current_patch_hash,
                    "required_ids": sorted(target.required_ids),
                    "affected_json_paths": sorted(target.affected_json_paths),
                    "required_properties": sorted(target.required_properties),
                }
                for target in sorted(self.targets, key=lambda item: item.patch_type)
            ],
            "canonical_task_plan_hash": self.canonical_task_plan_hash,
            "planning_scope_hash": self.planning_scope_hash,
            "gate_input_hash": self.gate_input_hash,
            "reason_code": self.reason_code,
        }
        expected = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
        if self.request_fingerprint and self.request_fingerprint != expected:
            raise ValueError("request_fingerprint must be deterministic")
        self.request_fingerprint = expected
        return self


class RetryExecutionPlan(AgentBaseModel):
    execution_id: str
    request_id: str
    owner_patch_types: list[str] = Field(default_factory=list)
    invalidation_patch_types: list[str] = Field(default_factory=list)
    gates_to_invalidate: list[PlanGateId] = Field(default_factory=list)
    gates_to_replay: list[PlanGateId] = Field(default_factory=list)
    earliest_resume_patch_type: str | None = None
    candidate_strategy: PlanRetryAction
    validation_steps: list[str] = Field(default_factory=list)
    commit_strategy: str = "atomic_owner_commit"
    budget_snapshot: dict[str, int] = Field(default_factory=dict)
    execution_fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetryRoundRecord(AgentBaseModel):
    round_index: int
    request: ExecutablePlanRetryRequest
    execution_plan: RetryExecutionPlan | None = None
    state_hash_before: str = ""
    owner_hashes_before: dict[str, str] = Field(default_factory=dict)
    candidate_hashes: dict[str, str] = Field(default_factory=dict)
    owner_hashes_after: dict[str, str] = Field(default_factory=dict)
    invalidated_patch_types: list[str] = Field(default_factory=list)
    regenerated_patch_types: list[str] = Field(default_factory=list)
    gates_invalidated: list[PlanGateId] = Field(default_factory=list)
    gates_replayed: list[PlanGateId] = Field(default_factory=list)
    gate_replay_details: dict[str, dict[str, Any]] = Field(default_factory=dict)
    issue_fingerprint_before: str | None = None
    issue_fingerprint_after: str | None = None
    resolved_issue_codes: list[str] = Field(default_factory=list)
    remaining_issue_codes: list[str] = Field(default_factory=list)
    new_issue_codes: list[str] = Field(default_factory=list)
    checks_executed: list[str] = Field(default_factory=list)
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    outcome: RetryExecutionStatus
    reclassification: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetryExecutionOutcome(AgentBaseModel):
    status: RetryExecutionStatus
    detail: str = ""
    request_id: str | None = None
    execution_id: str | None = None
    unresolved_request_ids: list[str] = Field(default_factory=list)
    new_request_ids: list[str] = Field(default_factory=list)
    workflow_behavior_changed: bool = False
    reclassification: str = ""
    budget_snapshot: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
