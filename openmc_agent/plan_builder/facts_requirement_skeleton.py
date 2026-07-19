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


# ---------------------------------------------------------------------------
# Phase 8C Step 2: Evidence-claim → slot mining
# ---------------------------------------------------------------------------


# Map a slot's semantic_kind to the claim subjects and json_paths that
# populate it.  Reactor-neutral: only generic semantic kinds appear here.
_SEMANTIC_KIND_TO_CLAIM_SUBJECTS: dict[str, tuple[str, ...]] = {
    "model_scope": ("model_scope", "scope"),
    "assembly_count": ("assembly_count", "core_assembly_count"),
    "core_lattice_size": ("core_lattice_size", "core_layout_shape"),
    "assembly_type_counts": ("assembly_type_counts", "assembly_type_distribution"),
    "lattice_size": ("lattice_size", "pin_lattice_size"),
    "assembly_pitch_cm": ("assembly_pitch_cm", "assembly_pitch"),
    "pin_pitch_cm": ("pin_pitch_cm", "pin_pitch"),
    "has_axial_geometry": ("has_axial_geometry", "axial_geometry_presence"),
    "has_spacer_grids": ("has_spacer_grids", "spacer_grid_presence"),
    "has_special_pin_map": ("has_special_pin_map", "special_pin_map_presence"),
    "selected_variant": ("selected_variant", "benchmark_variant"),
    "active_fuel_region_cm": ("active_fuel_region_cm", "active_fuel_region"),
    "axial_domain_cm": ("axial_domain_cm", "axial_domain"),
    "expected_spacer_grid_count": ("expected_spacer_grid_count", "spacer_grid_count"),
}


# Status priority — higher wins.  Matches FactsRequirementSlot status values.
_CLAIM_STATUS_PRIORITY: dict[str, int] = {
    "unresolved": 0,
    "source_absent": 1,
    "deterministically_derived": 2,
    "source_backed": 3,
    "human_confirmed": 4,
}


def _claim_status_for_slot(claim: Any) -> str:
    """Translate an EvidenceClaim's status flags into a FactsRequirementSlot
    status.
    """
    if getattr(claim, "confirmed_by_human", False):
        return "human_confirmed"
    status = getattr(getattr(claim, "status", None), "value", str(getattr(claim, "status", "")))
    if status == "explicit":
        return "source_backed"
    if status == "deterministically_derived":
        return "deterministically_derived"
    if status in {"rejected", "withdrawn"}:
        return "source_absent"
    return "unresolved"


def _claim_source_span_ids(claim: Any) -> list[str]:
    spans: list[str] = []
    for ref in getattr(claim, "source_refs", ()) or ():
        sid = getattr(ref, "source_span_id", None) or getattr(ref, "span_id", None)
        if sid:
            spans.append(str(sid))
    return spans


def _claim_subject_matches(claim: Any, semantic_kind: str) -> bool:
    """Does this claim's subject or json_paths match the semantic kind?"""
    subjects = _SEMANTIC_KIND_TO_CLAIM_SUBJECTS.get(semantic_kind, ())
    if str(getattr(claim, "subject", "")).lower() in {s.lower() for s in subjects}:
        return True
    json_paths = tuple(getattr(claim, "required_by_json_paths", ()) or ())
    if json_paths:
        target_path = f"/{semantic_kind}"
        for jp in json_paths:
            if str(jp) == target_path or str(jp).startswith(target_path):
                return True
    return False


def _select_best_claim(
    claims: list[Any], semantic_kind: str
) -> tuple[Any | None, list[Any]]:
    """Return (best_claim, conflicting_claims) for the given semantic_kind.

    The best claim is the one with the highest status priority; ties break
    on confidence.  Conflicting claims have the same semantic_kind but a
    different value; they are returned so the compiler can mark the slot
    as ``conflict`` rather than silently picking one.
    """
    matching = [c for c in claims if _claim_subject_matches(c, semantic_kind)]
    if not matching:
        return None, []
    # Rank by status priority then by confidence.
    ranked = sorted(
        matching,
        key=lambda c: (
            _CLAIM_STATUS_PRIORITY.get(_claim_status_for_slot(c), 0),
            float(getattr(c, "confidence", 0.0)),
        ),
        reverse=True,
    )
    best = ranked[0]
    best_value = getattr(best, "value", None)
    conflicts = [
        c for c in ranked[1:]
        if getattr(c, "value", None) is not None
        and getattr(c, "value", None) != best_value
        and _claim_status_for_slot(c) in {"source_backed", "human_confirmed", "deterministically_derived"}
    ]
    return best, conflicts


def compile_facts_requirement_skeleton(
    *,
    requirement_text: str,
    feature_contract: Any,
    evidence_ledger: Any | None = None,
    source_index: Any | None = None,
    confirmed_facts: dict[str, Any] | None = None,
) -> FactsSkeletonCompilationResult:
    """Compile a FactsRequirementSkeleton from available evidence.

    Phase 8C Step 2 contract:
    - Priority: human_confirmed > source_backed > deterministically_derived.
    - Mines ``evidence_ledger.claims`` for slot values via subject /
      ``required_by_json_paths`` matching.
    - Multiple conflicting claims for the same slot mark the slot as
      ``conflict`` rather than silently picking one.
    - Human-confirmed ``confirmed_facts`` still override source claims
      (this preserves backwards compatibility with the existing
      human-confirmation flow).
    - Source-absence records are only emitted when an EvidenceClaim with
      ``status='rejected'`` or ``'withdrawn'`` is present; absence of a
      claim never becomes a synthetic ``False``.
    """
    from openmc_agent.plan_builder.closed_loop.fingerprints import _digest
    from openmc_agent.plan_investigation.hashing import content_hash

    requirement_hash = content_hash(requirement_text) if hasattr(content_hash, '__call__') else _digest(requirement_text)
    source_index_hash = getattr(source_index, "index_hash", "") if source_index else ""
    ledger_hash = getattr(evidence_ledger, "ledger_hash", "") if evidence_ledger else ""
    contract_hash = getattr(feature_contract, "contract_hash", "") if feature_contract else ""

    warnings: list[str] = []
    errors: list[str] = []
    conflicts_list: list[str] = []

    confirmed = confirmed_facts or {}
    claims = list(getattr(evidence_ledger, "claims", [])) if evidence_ledger else []

    # ------------------------------------------------------------------
    # Model scope
    # ------------------------------------------------------------------
    scope_slot = FactsScopeRequirement()
    confirmed_scope = confirmed.get("model_scope") or (
        confirmed_facts.get("plan_closed_loop", {}).get("facts", {}).get("model_scope")
        if isinstance(confirmed_facts, dict) else None
    )
    if confirmed_scope and confirmed_scope in {"single_pin", "single_assembly", "multi_assembly_core", "full_core"}:
        scope_slot.value = confirmed_scope
        scope_slot.status = "human_confirmed"
        scope_slot.confidence = 1.0
        scope_slot.immutable = True
    else:
        best_claim, conflict_claims = _select_best_claim(claims, "model_scope")
        if best_claim is not None and best_claim.value in {
            "single_pin", "single_assembly", "multi_assembly_core", "full_core", "unknown",
        }:
            scope_slot.value = best_claim.value
            scope_slot.status = _claim_status_for_slot(best_claim)
            scope_slot.confidence = float(getattr(best_claim, "confidence", 0.0))
            scope_slot.source_claim_ids = [str(getattr(best_claim, "claim_id", ""))]
            scope_slot.source_span_ids = _claim_source_span_ids(best_claim)
            scope_slot.immutable = scope_slot.status in {"human_confirmed", "source_backed"}
            if conflict_claims:
                scope_slot.status = "conflict"
                scope_slot.unresolved_reason = "conflicting source-backed scope claims"
                conflicts_list.append(scope_slot.slot_id)
        # Feature contract can lock the scope when multi_assembly_core is detected.
        if (
            scope_slot.status == "unresolved"
            and feature_contract is not None
            and getattr(feature_contract, "multi_assembly_core", False)
        ):
            scope_slot.value = "multi_assembly_core"
            scope_slot.status = "deterministically_derived"
            scope_slot.derivation_codes = ["feature_contract.multi_assembly_core"]
            scope_slot.confidence = 0.9
            scope_slot.immutable = True

    # ------------------------------------------------------------------
    # Assembly layout
    # ------------------------------------------------------------------
    layout_slot = FactsAssemblyLayoutRequirement()
    layout_slot.assembly_count = confirmed.get("assembly_count")
    core_ls = confirmed.get("core_lattice_size")
    if isinstance(core_ls, (list, tuple)) and len(core_ls) == 2:
        layout_slot.core_lattice_size = (core_ls[0], core_ls[1])
    atc = confirmed.get("assembly_type_counts")
    if isinstance(atc, dict):
        layout_slot.assembly_type_counts = {str(k): int(v) for k, v in atc.items() if isinstance(v, (int, float))}

    # If confirmed_facts did not carry layout values, mine claims.
    if layout_slot.assembly_count is None:
        best, _ = _select_best_claim(claims, "assembly_count")
        if best is not None and isinstance(getattr(best, "value", None), int):
            layout_slot.assembly_count = int(best.value)
            layout_slot.status = _claim_status_for_slot(best)
            layout_slot.confidence = float(getattr(best, "confidence", 0.0))
            layout_slot.source_claim_ids = [str(getattr(best, "claim_id", ""))]
            layout_slot.source_span_ids = _claim_source_span_ids(best)
    if layout_slot.core_lattice_size is None:
        best, _ = _select_best_claim(claims, "core_lattice_size")
        if best is not None:
            value = getattr(best, "value", None)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                layout_slot.core_lattice_size = (int(value[0]), int(value[1]))
                if layout_slot.status == "unresolved":
                    layout_slot.status = _claim_status_for_slot(best)
                    layout_slot.source_claim_ids = [str(getattr(best, "claim_id", ""))]
                    layout_slot.source_span_ids = _claim_source_span_ids(best)
    if not layout_slot.assembly_type_counts:
        best, _ = _select_best_claim(claims, "assembly_type_counts")
        if best is not None and isinstance(getattr(best, "value", None), dict):
            layout_slot.assembly_type_counts = {
                str(k): int(v) for k, v in best.value.items()
                if isinstance(v, (int, float))
            }
            if layout_slot.status == "unresolved":
                layout_slot.status = _claim_status_for_slot(best)
                layout_slot.source_claim_ids = [str(getattr(best, "claim_id", ""))]
                layout_slot.source_span_ids = _claim_source_span_ids(best)
    # Deterministic derivation: assembly_count = rows × cols.
    if (
        layout_slot.assembly_count is None
        and layout_slot.core_lattice_size is not None
        and layout_slot.status != "conflict"
    ):
        rows, cols = layout_slot.core_lattice_size
        layout_slot.assembly_count = int(rows * cols)
        layout_slot.status = (
            layout_slot.status if layout_slot.status != "unresolved"
            else "deterministically_derived"
        )
        if "lattice_product" not in layout_slot.derivation_codes:
            layout_slot.derivation_codes = list(layout_slot.derivation_codes) + ["lattice_product"]

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------
    features_slot = FactsFeatureRequirement()
    for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
        val = confirmed.get(flag)
        if val is not None:
            setattr(features_slot, flag, val)
            if features_slot.status == "unresolved":
                features_slot.status = "human_confirmed"
                features_slot.immutable = True
        else:
            best, _ = _select_best_claim(claims, flag)
            if best is not None and isinstance(getattr(best, "value", None), bool):
                setattr(features_slot, flag, bool(best.value))
                if features_slot.status == "unresolved":
                    features_slot.status = _claim_status_for_slot(best)
                    features_slot.source_claim_ids = list(features_slot.source_claim_ids) + [str(getattr(best, "claim_id", ""))]
                    features_slot.source_span_ids = list(features_slot.source_span_ids) + _claim_source_span_ids(best)
    # Feature-contract fallback: feature detector presence locks the flag to True.
    if feature_contract is not None:
        fc_map = (
            ("has_spacer_grids", "has_spacer_grid"),
            ("has_axial_geometry", "has_axial_geometry"),
            ("has_special_pin_map", "has_special_pin_map"),
        )
        for facts_flag, contract_flag in fc_map:
            current = getattr(features_slot, facts_flag)
            if current is None and bool(getattr(feature_contract, contract_flag, False)):
                setattr(features_slot, facts_flag, True)
                if features_slot.status == "unresolved":
                    features_slot.status = "deterministically_derived"
                    features_slot.derivation_codes = list(features_slot.derivation_codes) + [
                        f"feature_contract.{contract_flag}"
                    ]

    # ------------------------------------------------------------------
    # Fuel variant slots
    # ------------------------------------------------------------------
    fuel_slots: list[FactsFuelVariantSlot] = []
    seen_fv_ids: set[str] = set()
    for req in confirmed.get("fuel_variant_requirements", []) if isinstance(confirmed, dict) else []:
        if isinstance(req, dict) and req.get("variant_id"):
            slot = FactsFuelVariantSlot(
                slot_id=f"fv_{req['variant_id']}",
                variant_id=req["variant_id"],
                enrichment_wt_percent=req.get("enrichment_wt_percent"),
                density_g_cm3=req.get("density_g_cm3"),
                assembly_type_ids=req.get("assembly_type_ids", []),
                status="human_confirmed",
                confidence=1.0,
                immutable=True,
            )
            fuel_slots.append(slot)
            seen_fv_ids.add(slot.variant_id)
    # Mine fuel-variant claims (subject="fuel_variant" or path matches).
    for claim in claims:
        if "fuel_variant" not in {
            str(getattr(claim, "subject", "")).lower(),
        }:
            continue
        value = getattr(claim, "value", None)
        if not isinstance(value, dict) or not value.get("variant_id"):
            continue
        vid = str(value["variant_id"])
        if vid in seen_fv_ids:
            continue
        seen_fv_ids.add(vid)
        fuel_slots.append(
            FactsFuelVariantSlot(
                slot_id=f"fv_{vid}",
                variant_id=vid,
                enrichment_wt_percent=value.get("enrichment_wt_percent"),
                density_g_cm3=value.get("density_g_cm3"),
                assembly_type_ids=list(value.get("assembly_type_ids", []) or []),
                status=_claim_status_for_slot(claim),
                confidence=float(getattr(claim, "confidence", 0.0)),
                source_claim_ids=[str(getattr(claim, "claim_id", ""))],
                source_span_ids=_claim_source_span_ids(claim),
                immutable=_claim_status_for_slot(claim) in {"human_confirmed", "source_backed"},
            )
        )

    # ------------------------------------------------------------------
    # Localized insert slots
    # ------------------------------------------------------------------
    insert_slots: list[FactsLocalizedInsertSlot] = []
    seen_li_ids: set[str] = set()
    for req in confirmed.get("localized_insert_requirements", []) if isinstance(confirmed, dict) else []:
        if isinstance(req, dict) and req.get("requirement_id"):
            slot = FactsLocalizedInsertSlot(
                slot_id=f"li_{req['requirement_id']}",
                requirement_id=req["requirement_id"],
                insert_kind=req.get("insert_kind", "custom"),
                assembly_type_ids=req.get("assembly_type_ids", []),
                expected_coordinate_count_per_assembly=req.get("expected_coordinate_count_per_assembly"),
                status="human_confirmed",
                confidence=1.0,
                immutable=True,
            )
            insert_slots.append(slot)
            seen_li_ids.add(slot.requirement_id)
    for claim in claims:
        if "localized_insert" not in {
            str(getattr(claim, "subject", "")).lower(),
        }:
            continue
        value = getattr(claim, "value", None)
        if not isinstance(value, dict) or not value.get("requirement_id"):
            continue
        rid = str(value["requirement_id"])
        if rid in seen_li_ids:
            continue
        seen_li_ids.add(rid)
        insert_slots.append(
            FactsLocalizedInsertSlot(
                slot_id=f"li_{rid}",
                requirement_id=rid,
                insert_kind=str(value.get("insert_kind", "custom")),
                assembly_type_ids=list(value.get("assembly_type_ids", []) or []),
                expected_coordinate_count_per_assembly=value.get("expected_coordinate_count_per_assembly"),
                status=_claim_status_for_slot(claim),
                confidence=float(getattr(claim, "confidence", 0.0)),
                source_claim_ids=[str(getattr(claim, "claim_id", ""))],
                source_span_ids=_claim_source_span_ids(claim),
                immutable=_claim_status_for_slot(claim) in {"human_confirmed", "source_backed"},
            )
        )
    # Feature-contract fallback: insert presence known but identity unknown.
    if (
        not insert_slots
        and feature_contract is not None
        and bool(getattr(feature_contract, "has_localized_insert", False))
    ):
        insert_slots.append(
            FactsLocalizedInsertSlot(
                slot_id="li_feature_contract_placeholder",
                requirement_id="feature_contract_localized_insert",
                insert_kind="custom",
                status="unresolved",
                unresolved_reason=(
                    "feature_contract.has_localized_insert is True but no source "
                    "claim identified the insert kind / assembly type binding"
                ),
            )
        )

    # ------------------------------------------------------------------
    # Scoped count slots
    # ------------------------------------------------------------------
    scoped_slots: list[FactsCountRequirement] = []
    for idx, sc in enumerate(confirmed.get("scoped_expected_counts", []) if isinstance(confirmed, dict) else []):
        if isinstance(sc, dict):
            slot = FactsCountRequirement(
                slot_id=f"count_{idx}",
                role=sc.get("role", ""),
                scope=sc.get("scope", ""),
                value=sc.get("value", 0),
                assembly_type_id=sc.get("assembly_type_id"),
                status="human_confirmed",
                confidence=1.0,
                immutable=True,
            )
            scoped_slots.append(slot)
    for claim in claims:
        if str(getattr(claim, "subject", "")).lower() != "scoped_count":
            continue
        value = getattr(claim, "value", None)
        if not isinstance(value, dict):
            continue
        scoped_slots.append(
            FactsCountRequirement(
                slot_id=f"count_{value.get('role', 'unknown')}_{value.get('scope', 'unknown')}",
                role=str(value.get("role", "")),
                scope=str(value.get("scope", "")),
                value=int(value.get("value", 0)),
                assembly_type_id=value.get("assembly_type_id"),
                status=_claim_status_for_slot(claim),
                confidence=float(getattr(claim, "confidence", 0.0)),
                source_claim_ids=[str(getattr(claim, "claim_id", ""))],
                source_span_ids=_claim_source_span_ids(claim),
            )
        )

    # ------------------------------------------------------------------
    # Aggregate unresolved / conflict slots
    # ------------------------------------------------------------------
    unresolved_slots: list[str] = []
    for slot_collection, label in (
        ([scope_slot] if scope_slot is not None else [], "scope"),
        ([layout_slot] if layout_slot is not None else [], "layout"),
        ([features_slot] if features_slot is not None else [], "features"),
    ):
        for s in slot_collection:
            status = getattr(s, "status", "unresolved")
            if status in {"unresolved", "source_absent"} and getattr(s, "required", False):
                unresolved_slots.append(getattr(s, "slot_id", label))
    for s in fuel_slots:
        if s.status in {"unresolved", "source_absent"}:
            unresolved_slots.append(s.slot_id)
    for s in insert_slots:
        if s.status in {"unresolved", "source_absent"}:
            unresolved_slots.append(s.slot_id)

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
        unresolved_slots=unresolved_slots,
        conflicting_slots=conflicts_list,
    )
    skeleton.skeleton_hash = _compute_skeleton_hash(skeleton)

    return FactsSkeletonCompilationResult(
        ok=not errors,
        skeleton=skeleton,
        warnings=warnings,
        errors=errors,
        compiler_hash=requirement_hash,
    )
