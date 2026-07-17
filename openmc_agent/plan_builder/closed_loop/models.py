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

PLAN_CLOSED_LOOP_CONTRACT_VERSION = "0.6"


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
    REVIEWED = "reviewed"
    # A reviewer was invoked but no trustworthy, coverage-complete review was
    # produced.  This is deliberately distinct from REVIEWED.
    REVIEW_FAILED = "review_failed"
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
            source_evidence_hashes=(
                [item.evidence_hash for item in self.source_evidence]
                or [str(item) for item in self.metadata.get("evidence_hashes", [])]
            ),
        )
        if self.finding_id and self.finding_id != fingerprint:
            raise ValueError("finding_id must match the deterministic finding fingerprint")
        if not self.finding_id:
            self.finding_id = fingerprint
        return self


class FactsInterpretationOption(AgentBaseModel):
    option_id: str
    label: str
    value: Any
    consequence: str
    source_evidence_hashes: list[str] = Field(default_factory=list)


class FactsReviewFindingDraft(AgentBaseModel):
    """Untrusted critic output; Python binds it to the supplied evidence."""
    code: str
    severity: PlanFindingSeverity
    category: PlanFindingCategory
    message: str
    evidence_hashes: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    repairable_by_llm: bool = False
    requires_human: bool = False
    confidence: float
    expected_value: Any | None = None
    current_value: Any | None = None
    candidate_interpretations: list[FactsInterpretationOption] = Field(default_factory=list)
    downstream_impact: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _human_is_not_repairable(self) -> "FactsReviewFindingDraft":
        if self.requires_human and self.repairable_by_llm:
            raise ValueError("requires_human findings cannot be repairable_by_llm")
        return self


class FactsReviewCoverageSummary(AgentBaseModel):
    reviewed_source_excerpt_count: int = 0
    omitted_source_excerpt_count: int = 0
    facts_fields_reviewed: list[str] = Field(default_factory=list)
    high_risk_topics_reviewed: list[str] = Field(default_factory=list)


class FactsReviewModelOutput(AgentBaseModel):
    review_status: Literal["complete", "insufficient_evidence", "source_too_large", "malformed_input"]
    findings: list[FactsReviewFindingDraft] = Field(default_factory=list)
    reviewed_evidence_hashes: list[str] = Field(default_factory=list)
    coverage_summary: FactsReviewCoverageSummary = Field(default_factory=FactsReviewCoverageSummary)
    concise_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class FactsRevisionProposal(AgentBaseModel):
    proposal_id: str
    target_patch_type: Literal["facts"] = "facts"
    operations: list[Any] = Field(default_factory=list)
    resolved_finding_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _proposal_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value


class HumanPlanAnswer(AgentBaseModel):
    question_id: str
    selected_option_id: str | None = None
    custom_value: Any | None = None
    answer_text: str | None = None
    answered_by: Literal["user", "expert", "test"]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfirmedFactRecord(AgentBaseModel):
    fact_id: str
    json_path: str
    value: Any
    source: Literal["human_confirmation"] = "human_confirmation"
    question_id: str
    evidence_hashes: list[str] = Field(default_factory=list)
    affected_patch_types: list[str] = Field(default_factory=lambda: ["facts"])
    confirmed_round: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfirmedPlanFactRecord(AgentBaseModel):
    """Typed confirmation shared by all plan gates.

    ``ConfirmedFactRecord`` remains the backwards-compatible facts-only
    record.  Placement confirmations deliberately live in a separate,
    namespaced ledger so an answer cannot be mistaken for source evidence.
    """

    fact_id: str
    gate_id: PlanGateId
    patch_type: str
    json_path: str
    value: Any
    question_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    affected_patch_types: list[str] = Field(default_factory=list)
    confirmed_round: int = 0
    input_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class PlanEvidenceItem(AgentBaseModel):
    """A short stable reference used by cross-patch gates.

    The short ``ref_id`` is transport-only.  Semantic identity is always the
    canonical SHA-256 hash, so an LLM cannot create authority by inventing a
    convenient reference label.
    """

    ref_id: str
    evidence_kind: Literal[
        "source_excerpt", "accepted_fact_contract", "patch_fragment",
        "deterministic_issue", "contract_matrix_row",
    ]
    patch_type: str | None = None
    patch_id: str | None = None
    json_path: str | None = None
    label: str
    value: Any
    canonical_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlacementRequirementView(AgentBaseModel):
    requirement_id: str
    insert_kind: str
    assembly_type_ids: list[str] = Field(default_factory=list)
    expected_coordinate_count: int | None = None
    expected_assembly_instance_count: int | None = None
    host_kind: str = "guide_tube"
    required_profile_id: str | None = None
    required_segment_roles: list[str] = Field(default_factory=list)
    expected_universe_ids: list[str] = Field(default_factory=list)
    anchor_z_cm: float | None = None
    control_state_id: str | None = None
    required_in_detailed_domain: bool = True
    requires_human_confirmation: bool = False


class PlacementAssemblyScopeView(AgentBaseModel):
    scope_id: str
    source_patch_type: str
    source_json_path: str
    assembly_type_id: str | None = None
    multiplicity: int | None = None
    lattice_size: tuple[int, int] | None = None
    coordinate_convention: dict[str, Any] = Field(default_factory=dict)
    guide_tube_coords: list[tuple[int, int]] = Field(default_factory=list)
    instrument_tube_coords: list[tuple[int, int]] = Field(default_factory=list)
    localized_insert_intents: list[dict[str, Any]] = Field(default_factory=list)


class PlacementProfileView(AgentBaseModel):
    profile_id: str
    anchor_kind: str = "absolute"
    anchor_z_cm: float | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)


class PlacementUniverseView(AgentBaseModel):
    universe_id: str
    kind: str


class PlacementCoreInstanceView(AgentBaseModel):
    assembly_type_id: str
    coordinate: tuple[int, int]


class PlacementBindingView(AgentBaseModel):
    scope_kind: Literal["single_assembly", "multi_assembly"]
    requirements: list[PlacementRequirementView] = Field(default_factory=list)
    assembly_scopes: list[PlacementAssemblyScopeView] = Field(default_factory=list)
    profiles: list[PlacementProfileView] = Field(default_factory=list)
    universes: list[PlacementUniverseView] = Field(default_factory=list)
    core_instances: list[PlacementCoreInstanceView] = Field(default_factory=list)
    coordinate_conventions: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlacementContractRow(AgentBaseModel):
    requirement_id: str
    insert_kind: str
    source_scope: str
    expected_assembly_type_ids: list[str] = Field(default_factory=list)
    actual_assembly_type_ids: list[str] = Field(default_factory=list)
    expected_instance_count: int | None = None
    actual_instance_count: int | None = None
    expected_coordinate_count: int | None = None
    actual_coordinate_counts: dict[str, int] = Field(default_factory=dict)
    host_kind: str = "guide_tube"
    host_coordinate_counts: dict[str, int] = Field(default_factory=dict)
    matching_intent_ids: list[str] = Field(default_factory=list)
    required_profile_id: str | None = None
    actual_profile_ids: list[str] = Field(default_factory=list)
    required_segment_roles: list[str] = Field(default_factory=list)
    actual_segment_roles: list[str] = Field(default_factory=list)
    expected_universe_ids: list[str] = Field(default_factory=list)
    referenced_universe_ids: list[str] = Field(default_factory=list)
    missing_universe_ids: list[str] = Field(default_factory=list)
    anchor_expected: float | None = None
    anchor_actual: dict[str, float | None] = Field(default_factory=dict)
    control_state_expected: str | None = None
    control_state_actual: dict[str, str | None] = Field(default_factory=dict)
    coordinate_convention_status: Literal["pass", "fail", "ambiguous", "not_applicable"] = "not_applicable"
    static_binding_status: Literal["pass", "fail", "ambiguous", "not_applicable"] = "not_applicable"
    issue_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class PlacementContractMatrix(AgentBaseModel):
    rows: list[PlacementContractRow] = Field(default_factory=list)
    input_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlacementEvidencePack(AgentBaseModel):
    gate_id: Literal[PlanGateId.PLACEMENT] = PlanGateId.PLACEMENT
    input_hash: str = ""
    placement_scope_kind: Literal["single_assembly", "multi_assembly"]
    evidence_items: list[PlanEvidenceItem] = Field(default_factory=list)
    contract_matrix: PlacementContractMatrix
    deterministic_issues: list[dict[str, Any]] = Field(default_factory=list)
    relevant_patch_hashes: dict[str, str] = Field(default_factory=dict)
    required_patch_types: list[str] = Field(default_factory=list)
    optional_patch_types: list[str] = Field(default_factory=list)
    accepted_facts_hash: str = ""
    coordinate_convention_summary: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[PlanReviewAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlacementReviewFindingDraft(AgentBaseModel):
    code: str
    severity: PlanFindingSeverity
    category: PlanFindingCategory
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    affected_contract_rows: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    repairable_by_llm: bool = False
    requires_human: bool = False
    confidence: float
    expected_value: Any | None = None
    current_value: Any | None = None
    candidate_interpretations: list[FactsInterpretationOption] = Field(default_factory=list)
    downstream_impact: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _placement_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _placement_human_or_repair(self) -> "PlacementReviewFindingDraft":
        if self.requires_human and self.repairable_by_llm:
            raise ValueError("requires_human findings cannot be repairable_by_llm")
        return self


class PlacementReviewCoverageSummary(AgentBaseModel):
    reviewed_contract_row_count: int = 0
    omitted_contract_row_count: int = 0
    reviewed_evidence_item_count: int = 0
    omitted_evidence_item_count: int = 0
    deterministic_issues_acknowledged: list[str] = Field(default_factory=list)


class PlacementReviewModelOutput(AgentBaseModel):
    review_status: Literal["complete", "insufficient_evidence", "malformed_input"]
    findings: list[PlacementReviewFindingDraft] = Field(default_factory=list)
    reviewed_contract_row_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_refs: list[str] = Field(default_factory=list)
    coverage_summary: PlacementReviewCoverageSummary = Field(default_factory=PlacementReviewCoverageSummary)
    concise_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlacementPatchEdit(AgentBaseModel):
    patch_type: Literal["localized_insert_profiles", "pin_map", "assembly_catalog", "core_layout"]
    patch_id: str
    expected_patch_hash: str
    operations: list[Any] = Field(default_factory=list)


class PlacementRevisionProposal(AgentBaseModel):
    proposal_id: str
    gate_id: Literal[PlanGateId.PLACEMENT] = PlanGateId.PLACEMENT
    edits: list[PlacementPatchEdit] = Field(default_factory=list)
    resolved_finding_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _placement_edits(self) -> "PlacementRevisionProposal":
        if not self.edits:
            raise ValueError("placement revision requires at least one edit")
        if len({edit.patch_type for edit in self.edits}) != len(self.edits):
            raise ValueError("placement revision has at most one edit block per patch type")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return self


class PlacementDependencyRetryRequest(AgentBaseModel):
    request_id: str
    gate_id: Literal[PlanGateId.PLACEMENT] = PlanGateId.PLACEMENT
    dependency_patch_type: Literal["facts", "universes"]
    issue_codes: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    required_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str
    downstream_patch_types: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase-4 Material-Universe Review Gate models
# ---------------------------------------------------------------------------


class MaterialRecord(AgentBaseModel):
    """Static view of one MaterialsPatch entry, augmented with resolver info."""

    material_id: str
    name: str = ""
    role: str = ""
    source_variant_id: str | None = None
    density_g_cm3: float | None = None
    density_status: str = "needs_confirmation"
    density_source: str | None = None
    temperature_K: float | None = None
    composition_status: str = "needs_confirmation"
    composition: dict[str, float] = Field(default_factory=dict)
    composition_basis: str = "unknown"
    compound_component_count: int = 0
    resolver_status: str = "unknown"
    resolver_normalized_species: dict[str, float] = Field(default_factory=dict)
    resolver_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_by_source: list[str] = Field(default_factory=list)
    static_consumers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UniverseRecord(AgentBaseModel):
    """Static view of one UniversesPatch entry."""

    universe_id: str
    kind: str = "custom"
    fuel_variant_id: str | None = None
    cell_count: int = 0
    material_ids: list[str] = Field(default_factory=list)
    cell_roles: list[str] = Field(default_factory=list)
    background_cell_id: str | None = None
    required_by_source: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CellMaterialBinding(AgentBaseModel):
    """One row per (universe, cell) describing the material reference."""

    binding_id: str
    universe_id: str
    universe_kind: str = "custom"
    cell_id: str
    cell_role: str = ""
    region_kind: str = "unknown"
    r_min_cm: float | None = None
    r_max_cm: float | None = None
    material_id: str | None = None
    material_role: str | None = None
    material_source_variant_id: str | None = None
    expected_roles: list[str] = Field(default_factory=list)
    expected_variant_id: str | None = None
    status: Literal["pass", "fail", "ambiguous", "unresolved"] = "pass"
    issue_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class FuelVariantBinding(AgentBaseModel):
    """One row per source fuel variant requirement."""

    variant_id: str
    source_enrichment_wt_percent: float | None = None
    material_id: str | None = None
    material_source_variant_id: str | None = None
    material_enrichment_wt_percent: float | None = None
    active_fuel_universe_ids: list[str] = Field(default_factory=list)
    active_fuel_cell_ids: list[str] = Field(default_factory=list)
    variant_count: int = 0
    collapsed_with_variants: list[str] = Field(default_factory=list)
    status: Literal["pass", "fail", "ambiguous"] = "pass"
    issue_codes: list[str] = Field(default_factory=list)


class MaterialUniverseBindingView(AgentBaseModel):
    """Static, deterministic view of the Materials → Universes edge."""

    planning_scope: str = "unknown"
    facts_patch_hash: str = ""
    materials_patch_hash: str = ""
    universes_patch_hash: str = ""
    feature_contract_hash: str = ""
    canonical_task_plan_hash: str = ""
    required_material_contracts: list[dict[str, Any]] = Field(default_factory=list)
    material_records: list[MaterialRecord] = Field(default_factory=list)
    universe_records: list[UniverseRecord] = Field(default_factory=list)
    cell_material_bindings: list[CellMaterialBinding] = Field(default_factory=list)
    fuel_variant_bindings: list[FuelVariantBinding] = Field(default_factory=list)
    unresolved_references: list[dict[str, Any]] = Field(default_factory=list)
    static_reachability_edges: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialUniverseContractRow(AgentBaseModel):
    """One row of the deterministic Material-Universe contract matrix.

    Four row kinds share this shape via ``row_kind``:
    - ``source_material_coverage``
    - ``material_to_cell_binding``
    - ``fuel_variant_identity``
    - ``required_universe_material_structure``
    """

    row_id: str
    row_kind: Literal[
        "source_material_coverage",
        "material_to_cell_binding",
        "fuel_variant_identity",
        "required_universe_material_structure",
    ]
    requirement_id: str = ""
    material_id: str | None = None
    material_role: str | None = None
    universe_id: str | None = None
    cell_id: str | None = None
    cell_role: str | None = None
    variant_id: str | None = None
    expected_roles: list[str] = Field(default_factory=list)
    actual_roles: list[str] = Field(default_factory=list)
    expected_material_roles: list[str] = Field(default_factory=list)
    actual_material_roles: list[str] = Field(default_factory=list)
    expected_variant_id: str | None = None
    actual_variant_ids: list[str] = Field(default_factory=list)
    coverage_status: Literal["pass", "fail", "ambiguous", "not_applicable"] = "not_applicable"
    issue_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialUniverseContractMatrix(AgentBaseModel):
    rows: list[MaterialUniverseContractRow] = Field(default_factory=list)
    input_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialUniverseEvidencePack(AgentBaseModel):
    gate_id: Literal[PlanGateId.MATERIAL_UNIVERSE] = PlanGateId.MATERIAL_UNIVERSE
    input_hash: str = ""
    evidence_pack_id: str = ""
    binding_view: MaterialUniverseBindingView
    contract_matrix: MaterialUniverseContractMatrix
    material_species_report: dict[str, Any] = Field(default_factory=dict)
    deterministic_issues: list[dict[str, Any]] = Field(default_factory=list)
    relevant_patch_hashes: dict[str, str] = Field(default_factory=dict)
    accepted_facts_hash: str = ""
    evidence_items: list[PlanEvidenceItem] = Field(default_factory=list)
    confirmed_records: list[dict[str, Any]] = Field(default_factory=list)
    allowed_actions: list[PlanReviewAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialUniverseReviewFindingDraft(AgentBaseModel):
    """Untrusted critic output; Python binds it to supplied evidence."""

    code: str
    severity: PlanFindingSeverity
    category: PlanFindingCategory
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    contract_row_ids: list[str] = Field(default_factory=list)
    affected_json_paths: list[str] = Field(default_factory=list)
    repairable_by_llm: bool = False
    requires_human: bool = False
    confidence: float
    expected_semantics: Any | None = None
    current_semantics: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _human_not_repairable(self) -> "MaterialUniverseReviewFindingDraft":
        if self.requires_human and self.repairable_by_llm:
            raise ValueError("requires_human findings cannot be repairable_by_llm")
        return self


class MaterialUniverseReviewCoverageSummary(AgentBaseModel):
    reviewed_source_requirement_ids: list[str] = Field(default_factory=list)
    reviewed_material_ids: list[str] = Field(default_factory=list)
    reviewed_universe_ids: list[str] = Field(default_factory=list)
    reviewed_contract_row_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_refs: list[str] = Field(default_factory=list)
    omitted_material_count: int = 0
    omitted_universe_count: int = 0
    omitted_contract_row_count: int = 0
    unresolved_evidence_count: int = 0


class MaterialUniverseReviewModelOutput(AgentBaseModel):
    review_status: Literal["complete", "insufficient_evidence", "source_too_large", "malformed_input"]
    findings: list[MaterialUniverseReviewFindingDraft] = Field(default_factory=list)
    reviewed_contract_row_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_refs: list[str] = Field(default_factory=list)
    coverage_summary: MaterialUniverseReviewCoverageSummary = Field(default_factory=MaterialUniverseReviewCoverageSummary)
    concise_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    contract_version: Literal["0.1", "0.2", "0.3", "0.4", "0.5", "0.6"] = PLAN_CLOSED_LOOP_CONTRACT_VERSION
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
    facts_review_chunk_chars: int = 12000
    max_facts_review_chunks: int = 8
    max_facts_review_source_chars: int = 96000
    enable_facts_review_synthesis: bool = True
    plan_human_mode: Literal["off", "ambiguity_only"] = "off"
    plan_gates: list[PlanGateId] = Field(default_factory=list)
    placement_review_mode: Literal["off", "advisory", "controlled"] = "off"
    # Phase-4 Material-Universe Review Gate mode.  Independent of placement
    # so users can enable one without the other.
    material_universe_review_mode: Literal["off", "advisory", "controlled"] = "off"
    # Phase-3 executable retry budget.  It remains independent from reviewer
    # calls so checkpoint restore cannot silently refresh retry authority.
    max_retry_rounds: int = 6
    max_attempts_per_retry_request: int = 2
    max_same_candidate_attempts: int = 1
    max_owner_regenerations_per_patch: int = 2
    max_gate_replays_per_gate: int = 3
    max_total_retry_llm_calls: int = 12
    gate_enabled: dict[PlanGateId, bool] = Field(default_factory=_default_gate_enabled)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_review_rounds_per_gate", "max_repair_rounds_per_gate", "max_human_rounds_per_gate", "max_attempts_per_issue_fingerprint", "max_no_progress_rounds", "max_total_additional_llm_calls", "max_facts_review_chunks", "max_retry_rounds", "max_attempts_per_retry_request", "max_same_candidate_attempts", "max_owner_regenerations_per_patch", "max_gate_replays_per_gate", "max_total_retry_llm_calls")
    @classmethod
    def _budget_bounds(cls, value: int) -> int:
        if not 0 <= value <= 10000:
            raise ValueError("budget must be between 0 and 10000")
        return value

    @field_validator("facts_review_chunk_chars", "max_facts_review_source_chars")
    @classmethod
    def _source_bounds(cls, value: int) -> int:
        if not 1 <= value <= 1_000_000:
            raise ValueError("facts review source limit must be between 1 and 1000000")
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
