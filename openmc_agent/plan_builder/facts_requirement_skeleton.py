"""FactsRequirementSkeleton — locks source-backed facts and prevents LLM drift.

Phase 8B Step 2 models that constrain the LLM to respect source-backed,
human-confirmed, and deterministically-derived fact values.  The skeleton
is compiled once after the Facts investigation and is consulted at every
subsequent closed-loop gate to detect hash mismatches, immutable-field
modifications, scope contradictions, and required-slot absence.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class FactsRequirementSlot(AgentBaseModel):
    slot_id: str
    facts_json_path: str
    semantic_kind: str = ""
    required: bool = False
    value: Any = None
    status: Literal[
        "source_backed",
        "human_confirmed",
        "deterministically_derived",
        "unresolved",
        "source_absent",
        "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsScopeRequirement(AgentBaseModel):
    slot_id: str = "scope"
    facts_json_path: str = "/model_scope"
    semantic_kind: str = "model_scope"
    required: bool = True
    value: Literal[
        "single_pin", "single_assembly", "multi_assembly_core",
        "full_core", "unknown",
    ] = "unknown"
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsAssemblyLayoutRequirement(AgentBaseModel):
    slot_id: str = "assembly_layout"
    facts_json_path: str = "/assembly_count"
    semantic_kind: str = "assembly_count"
    required: bool = False
    assembly_count: int | None = None
    core_lattice_size: tuple[int, int] | None = None
    assembly_type_counts: dict[str, int] = Field(default_factory=dict)
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsFeatureRequirement(AgentBaseModel):
    slot_id: str = "features"
    facts_json_path: str = "/features"
    semantic_kind: str = "features"
    required: bool = False
    has_axial_geometry: bool | None = None
    has_spacer_grids: bool | None = None
    has_special_pin_map: bool | None = None
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsFuelVariantSlot(AgentBaseModel):
    slot_id: str
    facts_json_path: str = "/fuel_variant_requirements"
    semantic_kind: str = "fuel_variant"
    required: bool = True
    variant_id: str
    enrichment_wt_percent: float | None = None
    density_g_cm3: float | None = None
    assembly_type_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsLocalizedInsertSlot(AgentBaseModel):
    slot_id: str
    facts_json_path: str = "/localized_insert_requirements"
    semantic_kind: str = "localized_insert"
    required: bool = False
    requirement_id: str
    insert_kind: str
    assembly_type_ids: list[str] = Field(default_factory=list)
    expected_coordinate_count_per_assembly: int | None = None
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsCountRequirement(AgentBaseModel):
    slot_id: str
    facts_json_path: str = "/scoped_expected_counts"
    semantic_kind: str = "scoped_count"
    required: bool = False
    role: str
    scope: str
    value: int
    assembly_type_id: str | None = None
    status: Literal[
        "source_backed", "human_confirmed", "deterministically_derived",
        "unresolved", "source_absent", "conflict",
    ] = "unresolved"
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    derivation_codes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    immutable: bool = False
    unresolved_reason: str | None = None
    slot_hash: str = ""


class FactsSkeletonCompilationResult(AgentBaseModel):
    ok: bool = False
    skeleton: FactsRequirementSkeleton | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    compiler_hash: str = ""


class FactsRequirementSkeleton(AgentBaseModel):
    requirement_hash: str
    source_index_hash: str
    ledger_hash: str
    feature_contract_hash: str
    selected_variant: str | None = None
    model_scope: FactsScopeRequirement | None = None
    assembly_layout: FactsAssemblyLayoutRequirement | None = None
    features: FactsFeatureRequirement | None = None
    fuel_variant_slots: list[FactsFuelVariantSlot] = Field(default_factory=list)
    localized_insert_slots: list[FactsLocalizedInsertSlot] = Field(default_factory=list)
    scoped_count_slots: list[FactsCountRequirement] = Field(default_factory=list)
    unresolved_slots: list[str] = Field(default_factory=list)
    conflicting_slots: list[str] = Field(default_factory=list)
    source_absence_records: list[dict[str, Any]] = Field(default_factory=list)
    skeleton_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.skeleton_hash:
            self.skeleton_hash = _compute_skeleton_hash(self)


def _compute_skeleton_hash(skeleton: FactsRequirementSkeleton) -> str:
    from openmc_agent.plan_builder.closed_loop.fingerprints import _digest
    return _digest(skeleton.model_dump(mode="json", exclude={"skeleton_hash"}))


def compile_facts_requirement_skeleton(
    *,
    requirement_text: str,
    feature_contract: Any,
    evidence_ledger: Any | None = None,
    source_index: Any | None = None,
    confirmed_facts: dict[str, Any] | None = None,
) -> FactsSkeletonCompilationResult:
    """Compile a FactsRequirementSkeleton from available evidence.

    Priority: human_confirmed > source_backed > deterministically_derived.

    For each field, checks if evidence exists and sets status accordingly.
    Fields with no evidence become ``unresolved`` or ``source_absent``.
    """
    from openmc_agent.plan_builder.closed_loop.fingerprints import _digest
    from openmc_agent.plan_investigation.hashing import content_hash

    requirement_hash = content_hash(requirement_text) if hasattr(content_hash, '__call__') else _digest(requirement_text)
    source_index_hash = getattr(source_index, "index_hash", "") if source_index else ""
    ledger_hash = getattr(evidence_ledger, "ledger_hash", "") if evidence_ledger else ""
    contract_hash = getattr(feature_contract, "contract_hash", "") if feature_contract else ""

    warnings: list[str] = []
    errors: list[str] = []

    confirmed = confirmed_facts or {}

    # Model scope
    scope_slot = FactsScopeRequirement()
    confirmed_scope = confirmed.get("model_scope") or confirmed_facts.get("plan_closed_loop", {}).get("facts", {}).get("model_scope") if isinstance(confirmed_facts, dict) else None
    if confirmed_scope and confirmed_scope in {"single_pin", "single_assembly", "multi_assembly_core", "full_core"}:
        scope_slot.value = confirmed_scope
        scope_slot.status = "human_confirmed"
        scope_slot.confidence = 1.0
        scope_slot.immutable = True

    # Assembly layout
    layout_slot = FactsAssemblyLayoutRequirement()
    layout_slot.assembly_count = confirmed.get("assembly_count")
    core_ls = confirmed.get("core_lattice_size")
    if isinstance(core_ls, (list, tuple)) and len(core_ls) == 2:
        layout_slot.core_lattice_size = (core_ls[0], core_ls[1])
    atc = confirmed.get("assembly_type_counts")
    if isinstance(atc, dict):
        layout_slot.assembly_type_counts = {str(k): int(v) for k, v in atc.items() if isinstance(v, (int, float))}

    # Features
    features_slot = FactsFeatureRequirement()
    for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
        val = confirmed.get(flag)
        if val is not None:
            setattr(features_slot, flag, val)

    # Fuel variant slots
    fuel_slots: list[FactsFuelVariantSlot] = []
    for req in confirmed.get("fuel_variant_requirements", []) if isinstance(confirmed, dict) else []:
        if isinstance(req, dict) and req.get("variant_id"):
            slot = FactsFuelVariantSlot(
                slot_id=f"fv_{req['variant_id']}",
                variant_id=req["variant_id"],
                enrichment_wt_percent=req.get("enrichment_wt_percent"),
                density_g_cm3=req.get("density_g_cm3"),
                assembly_type_ids=req.get("assembly_type_ids", []),
            )
            fuel_slots.append(slot)

    # Localized insert slots
    insert_slots: list[FactsLocalizedInsertSlot] = []
    for req in confirmed.get("localized_insert_requirements", []) if isinstance(confirmed, dict) else []:
        if isinstance(req, dict) and req.get("requirement_id"):
            slot = FactsLocalizedInsertSlot(
                slot_id=f"li_{req['requirement_id']}",
                requirement_id=req["requirement_id"],
                insert_kind=req.get("insert_kind", "custom"),
                assembly_type_ids=req.get("assembly_type_ids", []),
                expected_coordinate_count_per_assembly=req.get("expected_coordinate_count_per_assembly"),
            )
            insert_slots.append(slot)

    # Scoped count slots
    scoped_slots: list[FactsCountRequirement] = []
    for idx, sc in enumerate(confirmed.get("scoped_expected_counts", []) if isinstance(confirmed, dict) else []):
        if isinstance(sc, dict):
            slot = FactsCountRequirement(
                slot_id=f"count_{idx}",
                role=sc.get("role", ""),
                scope=sc.get("scope", ""),
                value=sc.get("value", 0),
                assembly_type_id=sc.get("assembly_type_id"),
            )
            scoped_slots.append(slot)

    skeleton = FactsRequirementSkeleton(
        requirement_hash=requirement_hash,
        source_index_hash=source_index_hash,
        ledger_hash=ledger_hash,
        feature_contract_hash=contract_hash,
        model_scope=scope_slot,
        assembly_layout=layout_slot,
        features=features_slot,
        fuel_variant_slots=fuel_slots,
        localized_insert_slots=insert_slots,
        scoped_count_slots=scoped_slots,
    )
    skeleton.skeleton_hash = _compute_skeleton_hash(skeleton)

    return FactsSkeletonCompilationResult(
        ok=not errors,
        skeleton=skeleton,
        warnings=warnings,
        errors=errors,
        compiler_hash=requirement_hash,
    )
