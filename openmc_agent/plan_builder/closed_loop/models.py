"""Reactor-neutral typed protocol models for the Phase-0 plan loop."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .fingerprints import (
    compute_evidence_pack_hash,
    compute_finding_fingerprint,
    compute_source_excerpt_hash,
)

PLAN_CLOSED_LOOP_CONTRACT_VERSION = "0.1"


class _TextEnum(str, Enum):
    pass


class PlanLoopMode(_TextEnum):
    OFF = "off"
    ADVISORY = "advisory"
    CONTROLLED = "controlled"


class PlanGateId(_TextEnum):
    FACTS = "facts"
    MATERIAL_UNIVERSE = "material_universe"
    PLACEMENT = "placement"
    AXIAL_GEOMETRY = "axial_geometry"
    ASSEMBLED_PLAN = "assembled_plan"


class PlanStageStatus(_TextEnum):
    PENDING = "pending"
    PROPOSING = "proposing"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    AWAITING_HUMAN = "awaiting_human"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PlanReviewAction(_TextEnum):
    APPROVE = "approve"
    REVISE_CURRENT_PATCH = "revise_current_patch"
    RETRY_DEPENDENCY = "retry_dependency"
    ASK_HUMAN = "ask_human"
    FAIL_CLOSED = "fail_closed"


class PlanFindingSeverity(_TextEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PlanFindingCategory(_TextEnum):
    SOURCE_COVERAGE = "source_coverage"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    CROSS_PATCH_MISMATCH = "cross_patch_mismatch"
    PLACEMENT_GAP = "placement_gap"
    REACHABILITY_GAP = "reachability_gap"
    PHYSICAL_AMBIGUITY = "physical_ambiguity"
    REPRESENTATION_ERROR = "representation_error"
    SCHEMA_OR_FORMAT = "schema_or_format"
    NO_PROGRESS = "no_progress"
    BUDGET_EXHAUSTED = "budget_exhausted"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class SourceExcerpt(AgentBaseModel):
    source_id: str
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    text: str = ""
    evidence_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_hash(self) -> "SourceExcerpt":
        expected = compute_source_excerpt_hash(self.source_path, self.line_start, self.line_end, self.text)
        if self.evidence_hash and self.evidence_hash != expected:
            raise ValueError("evidence_hash must match source path, line range, and text")
        if not self.evidence_hash:
            self.evidence_hash = expected
        return self


class PlanReviewFinding(AgentBaseModel):
    finding_id: str = ""
    gate_id: PlanGateId
    code: str
    severity: PlanFindingSeverity
    category: PlanFindingCategory
    message: str
    source_evidence: list[SourceExcerpt] = Field(default_factory=list)
    affected_patch_types: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    repairable_by_llm: bool = False
    requires_human: bool = False
    confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("affected_patch_types", "affected_json_paths")
    @classmethod
    def _dedupe_ordered(cls, value: list[str]) -> list[str]:
        return _dedupe(value)

    @model_validator(mode="after")
    def _validate_fingerprint(self) -> "PlanReviewFinding":
        fingerprint = compute_finding_fingerprint(
            gate_id=self.gate_id.value, code=self.code, category=self.category.value,
            affected_patch_types=self.affected_patch_types,
            affected_json_paths=self.affected_json_paths,
            source_evidence_hashes=[item.evidence_hash for item in self.source_evidence],
        )
        if self.finding_id and self.finding_id != fingerprint:
            raise ValueError("finding_id must match the deterministic finding fingerprint")
        if not self.finding_id:
            self.finding_id = fingerprint
        return self


class PlanReviewDecision(AgentBaseModel):
    decision_id: str
    gate_id: PlanGateId
    action: PlanReviewAction
    target_patch_types: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    allowed_actions_snapshot: list[PlanReviewAction]
    decided_by: Literal["deterministic", "reviewer", "supervisor", "human"]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_decision(self) -> "PlanReviewDecision":
        if self.action not in self.allowed_actions_snapshot:
            raise ValueError("action must be included in allowed_actions_snapshot")
        if self.action is PlanReviewAction.APPROVE and self.target_patch_types:
            raise ValueError("approve decisions cannot target patches")
        if self.action in {PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY} and not self.target_patch_types:
            raise ValueError("repair decisions require target_patch_types")
        if self.action is PlanReviewAction.ASK_HUMAN and not self.finding_ids:
            raise ValueError("ask_human decisions require finding_ids")
        return self


class HumanQuestionOption(AgentBaseModel):
    option_id: str
    label: str
    value: Any
    consequence: str
    recommended: bool = False


class HumanPlanQuestion(AgentBaseModel):
    question_id: str
    gate_id: PlanGateId
    finding_ids: list[str] = Field(default_factory=list)
    title: str
    question: str
    source_evidence: list[SourceExcerpt] = Field(default_factory=list)
    current_plan_summary: str = ""
    options: list[HumanQuestionOption] = Field(default_factory=list)
    affected_patch_types: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    default_option_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_question(self) -> "HumanPlanQuestion":
        if not self.question.strip():
            raise ValueError("question must not be blank")
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option_id values must be unique")
        if self.default_option_id is not None and self.default_option_id not in option_ids:
            raise ValueError("default_option_id must reference an option")
        if sum(option.recommended for option in self.options) > 1:
            raise ValueError("at most one option may be recommended")
        # This field is a reader-oriented summary, never a hidden transport
        # channel for a complete patch payload.
        try:
            summary_payload = json.loads(self.current_plan_summary)
        except (TypeError, ValueError):
            summary_payload = None
        if isinstance(summary_payload, dict) and (
            "patch_type" in summary_payload or "patches" in summary_payload
        ):
            raise ValueError("current_plan_summary must not contain complete patch JSON")
        return self


class PlanEvidencePack(AgentBaseModel):
    evidence_pack_id: str = ""
    gate_id: PlanGateId
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    relevant_patches: dict[str, dict[str, Any]] = Field(default_factory=dict)
    patch_summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deterministic_issues: list[dict[str, Any]] = Field(default_factory=list)
    dependency_edges: list[dict[str, Any]] = Field(default_factory=list)
    reachability_summary: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[PlanReviewAction] = Field(default_factory=list)
    input_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_hash(self) -> "PlanEvidencePack":
        expected = compute_evidence_pack_hash(self)
        if self.input_hash and self.input_hash != expected:
            raise ValueError("input_hash must match deterministic evidence-pack content")
        if not self.input_hash:
            self.input_hash = expected
        if not self.evidence_pack_id:
            self.evidence_pack_id = expected
        return self


class PlanStageState(AgentBaseModel):
    stage_id: str
    gate_id: PlanGateId
    status: PlanStageStatus = PlanStageStatus.PENDING
    patch_types: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    validation_count: int = 0
    review_count: int = 0
    repair_count: int = 0
    human_round_count: int = 0
    no_progress_count: int = 0
    issue_fingerprint: str | None = None
    latest_candidate_hash: str | None = None
    last_patch_hashes: dict[str, str] = Field(default_factory=dict)
    finding_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attempt_count", "validation_count", "review_count", "repair_count", "human_round_count", "no_progress_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("stage counters must be non-negative")
        return value


def _default_gate_enabled() -> dict[PlanGateId, bool]:
    return {gate: False for gate in PlanGateId}


class PlanClosedLoopPolicy(AgentBaseModel):
    contract_version: Literal["0.1"] = PLAN_CLOSED_LOOP_CONTRACT_VERSION
    mode: PlanLoopMode = PlanLoopMode.OFF
    max_review_rounds_per_gate: int = 2
    max_repair_rounds_per_gate: int = 2
    max_human_rounds_per_gate: int = 2
    max_attempts_per_issue_fingerprint: int = 2
    max_no_progress_rounds: int = 1
    max_total_additional_llm_calls: int = 20
    enable_human_gate: bool = False
    fail_closed_on_budget_exhaustion: bool = True
    artifact_subdir: str = "plan_closed_loop"
    gate_enabled: dict[PlanGateId, bool] = Field(default_factory=_default_gate_enabled)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_review_rounds_per_gate", "max_repair_rounds_per_gate", "max_human_rounds_per_gate", "max_attempts_per_issue_fingerprint", "max_no_progress_rounds", "max_total_additional_llm_calls")
    @classmethod
    def _budget_bounds(cls, value: int) -> int:
        if not 0 <= value <= 10000:
            raise ValueError("budget must be between 0 and 10000")
        return value


class PlanLoopOutcome(AgentBaseModel):
    status: Literal["progressed", "awaiting_human", "completed", "blocked", "disabled"]
    active_gate_id: PlanGateId | None = None
    active_stage_id: str | None = None
    findings: list[PlanReviewFinding] = Field(default_factory=list)
    decision: PlanReviewDecision | None = None
    pending_questions: list[HumanPlanQuestion] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    additional_llm_calls_used: int = 0
    state_changed: bool = False
    detail: str = ""
