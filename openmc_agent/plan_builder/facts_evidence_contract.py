"""FactsEvidenceContract — constrains LLM output from the requirement skeleton.

Phase 8B Step 2: compiled from FactsRequirementSkeleton to produce a
deterministic contract that the Facts patch LLM must respect.  The
contract is consulted by the preflight checker and merge logic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class FactsEvidenceContract(AgentBaseModel):
    resolved_scope: Literal[
        "single_pin", "single_assembly", "multi_assembly_core",
        "full_core", "unknown",
    ] = "unknown"
    required_feature_flags: list[str] = Field(default_factory=list)
    required_assembly_layout: bool = False
    required_fuel_variants: list[dict[str, Any]] = Field(default_factory=list)
    required_localized_inserts: list[dict[str, Any]] = Field(default_factory=list)
    required_scoped_counts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_source_critical_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence_contract_hash: str = ""


def _compute_contract_hash(contract: FactsEvidenceContract) -> str:
    from openmc_agent.plan_builder.closed_loop.fingerprints import _digest
    return _digest(contract.model_dump(mode="json", exclude={"evidence_contract_hash"}))


def compile_facts_evidence_contract(
    skeleton: Any,
) -> FactsEvidenceContract:
    """Compile a FactsEvidenceContract from a FactsRequirementSkeleton."""
    scope = "unknown"
    if skeleton is not None and skeleton.model_scope is not None:
        scope = skeleton.model_scope.value

    flags: list[str] = []
    if skeleton is not None and skeleton.features is not None:
        if skeleton.features.has_axial_geometry:
            flags.append("has_axial_geometry")
        if skeleton.features.has_spacer_grids:
            flags.append("has_spacer_grids")
        if skeleton.features.has_special_pin_map:
            flags.append("has_special_pin_map")

    variants: list[dict[str, Any]] = []
    if skeleton is not None:
        for slot in skeleton.fuel_variant_slots:
            variants.append({
                "variant_id": slot.variant_id,
                "enrichment_wt_percent": slot.enrichment_wt_percent,
                "density_g_cm3": slot.density_g_cm3,
                "assembly_type_ids": slot.assembly_type_ids,
            })

    inserts: list[dict[str, Any]] = []
    if skeleton is not None:
        for slot in skeleton.localized_insert_slots:
            inserts.append({
                "requirement_id": slot.requirement_id,
                "insert_kind": slot.insert_kind,
                "assembly_type_ids": slot.assembly_type_ids,
                "expected_coordinate_count_per_assembly": slot.expected_coordinate_count_per_assembly,
            })

    scoped_counts: list[dict[str, Any]] = []
    if skeleton is not None:
        for slot in skeleton.scoped_count_slots:
            scoped_counts.append({
                "role": slot.role,
                "scope": slot.scope,
                "value": slot.value,
                "assembly_type_id": slot.assembly_type_id,
            })

    unresolved = list(skeleton.unresolved_slots) if skeleton is not None else []
    conflicts = list(skeleton.conflicting_slots) if skeleton is not None else []

    contract = FactsEvidenceContract(
        resolved_scope=scope,
        required_feature_flags=flags,
        required_assembly_layout=skeleton is not None and skeleton.assembly_layout is not None,
        required_fuel_variants=variants,
        required_localized_inserts=inserts,
        required_scoped_counts=scoped_counts,
        unresolved_source_critical_fields=unresolved,
        conflicts=conflicts,
    )
    contract.evidence_contract_hash = _compute_contract_hash(contract)
    return contract


class FactsSkeletonPreflightResult(AgentBaseModel):
    ok: bool = False
    issues: list[dict[str, Any]] = Field(default_factory=list)


FACTS_SKELETON_ISSUE_CODES: list[str] = [
    "facts_skeleton.missing",
    "facts_skeleton.hash_mismatch",
    "facts_skeleton.required_slot_missing",
    "facts_skeleton.immutable_field_modified",
    "facts_skeleton.scope_contradiction",
    "facts_skeleton.count_mismatch",
    "facts_skeleton.fuel_variant_modified",
    "facts_skeleton.localized_insert_modified",
    "facts_skeleton.unresolved_must_be_resolved",
    # Phase 8C Step 2 — contract preflight codes.
    "facts_contract.missing",
    "facts_contract.hash_mismatch",
    "facts_contract.required_slot_missing",
    "facts_contract.unexpected_slot_added",
    "facts_contract.locked_field_modified",
    "facts_contract.scope_contradiction",
    "facts_contract.core_layout_missing",
    "facts_contract.assembly_count_inconsistent",
    "facts_contract.feature_flag_contradiction",
    "facts_contract.fuel_variant_missing",
    "facts_contract.localized_insert_missing",
    "facts_contract.source_critical_unresolved",
    "facts_contract.conflict_unresolved",
    "facts_contract.unsupported_value",
    "facts_contract.evidence_reference_invalid",
]


def run_facts_skeleton_preflight(
    skeleton: Any,
    candidate: dict[str, Any] | None,
) -> FactsSkeletonPreflightResult:
    """Check a candidate Facts patch against the compiled skeleton.

    Phase 8C Step 2: the preflight now emits both the legacy
    ``facts_skeleton.*`` codes and the new ``facts_contract.*`` codes
    that the retry-owner policy maps onto targeted repair.  The two
    code families alias each other for backward compatibility —
    existing tests and owner-policy entries that match ``facts_skeleton.*``
    keep working.
    """
    issues: list[dict[str, Any]] = []

    if skeleton is None:
        issues.append({
            "code": "facts_skeleton.missing",
            "severity": "error",
            "message": "FactsRequirementSkeleton is None; cannot validate",
        })
        return FactsSkeletonPreflightResult(ok=False, issues=issues)

    if not candidate:
        issues.append({
            "code": "facts_skeleton.missing",
            "severity": "error",
            "message": "candidate facts patch is None or empty",
        })
        return FactsSkeletonPreflightResult(ok=False, issues=issues)

    LOCK_STATUSES = {"human_confirmed", "source_backed", "deterministically_derived"}

    # ---- Scope -------------------------------------------------------
    if skeleton.model_scope is not None:
        candidate_scope = candidate.get("model_scope", "unknown")
        skel_scope = skeleton.model_scope
        if skel_scope.status in LOCK_STATUSES and skel_scope.immutable:
            if candidate_scope != skel_scope.value:
                issues.append({
                    "code": "facts_skeleton.immutable_field_modified",
                    "severity": "error",
                    "path": "/model_scope",
                    "expected": skel_scope.value,
                    "actual": candidate_scope,
                    "slot_ids": ["scope"],
                    "source_claim_ids": list(skel_scope.source_claim_ids),
                    "repair_kind": "locked_value_restore",
                    "message": f"model_scope changed from '{skel_scope.value}' to '{candidate_scope}'",
                })
                # Phase 8C alias
                issues.append({
                    "code": "facts_contract.locked_field_modified",
                    "severity": "error",
                    "path": "/model_scope",
                    "expected": skel_scope.value,
                    "actual": candidate_scope,
                    "slot_ids": ["scope"],
                    "source_claim_ids": list(skel_scope.source_claim_ids),
                    "repair_kind": "locked_value_restore",
                })
        elif skel_scope.status == "conflict":
            issues.append({
                "code": "facts_contract.conflict_unresolved",
                "severity": "warning",
                "path": "/model_scope",
                "slot_ids": ["scope"],
                "message": "model_scope has conflicting source claims; candidate may be wrong",
            })
        elif skel_scope.status in {"unresolved", "source_absent"} and candidate_scope not in {"unknown", None}:
            # Skeleton has no source backing but the LLM emitted a concrete
            # value.  Not necessarily wrong, but worth flagging.
            pass

    # ---- Feature flags ----------------------------------------------
    if skeleton.features is not None:
        for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
            expected_val = getattr(skeleton.features, flag, None)
            if expected_val is not None and skeleton.features.status in LOCK_STATUSES:
                candidate_val = candidate.get(flag)
                if candidate_val is not None and candidate_val != expected_val:
                    issues.append({
                        "code": "facts_skeleton.immutable_field_modified",
                        "severity": "error",
                        "path": f"/{flag}",
                        "expected": expected_val,
                        "actual": candidate_val,
                        "slot_ids": ["features"],
                        "repair_kind": "locked_value_restore",
                        "message": f"{flag} changed from {expected_val} to {candidate_val}",
                    })
                    issues.append({
                        "code": "facts_contract.feature_flag_contradiction",
                        "severity": "error",
                        "path": f"/{flag}",
                        "expected": expected_val,
                        "actual": candidate_val,
                        "slot_ids": ["features"],
                        "repair_kind": "locked_value_restore",
                    })

    # ---- Assembly layout --------------------------------------------
    if skeleton.assembly_layout is not None and skeleton.assembly_layout.status in LOCK_STATUSES:
        expected_count = skeleton.assembly_layout.assembly_count
        candidate_count = candidate.get("assembly_count")
        if expected_count is not None and expected_count != candidate_count:
            issues.append({
                "code": "facts_skeleton.count_mismatch",
                "severity": "error",
                "path": "/assembly_count",
                "expected": expected_count,
                "actual": candidate_count,
                "slot_ids": ["assembly_layout"],
                "source_claim_ids": list(skeleton.assembly_layout.source_claim_ids),
                "derivation_codes": list(skeleton.assembly_layout.derivation_codes),
                "repair_kind": "locked_value_restore",
            })
            issues.append({
                "code": "facts_contract.assembly_count_inconsistent",
                "severity": "error",
                "path": "/assembly_count",
                "expected": expected_count,
                "actual": candidate_count,
                "slot_ids": ["assembly_layout"],
                "source_claim_ids": list(skeleton.assembly_layout.source_claim_ids),
                "derivation_codes": list(skeleton.assembly_layout.derivation_codes),
                "repair_kind": "locked_value_restore",
            })

    # ---- Fuel variants ----------------------------------------------
    if skeleton.fuel_variant_slots:
        candidate_variants = candidate.get("fuel_variant_requirements", []) or []
        candidate_vids = {
            v.get("variant_id") for v in candidate_variants
            if isinstance(v, dict) and v.get("variant_id")
        }
        for slot in skeleton.fuel_variant_slots:
            if slot.status in LOCK_STATUSES and slot.immutable and slot.variant_id not in candidate_vids:
                issues.append({
                    "code": "facts_skeleton.fuel_variant_modified",
                    "severity": "error",
                    "path": "/fuel_variant_requirements",
                    "expected": slot.variant_id,
                    "slot_ids": [slot.slot_id],
                    "source_claim_ids": list(slot.source_claim_ids),
                    "repair_kind": "locked_value_restore",
                    "message": f"required fuel variant '{slot.variant_id}' missing from candidate",
                })
                issues.append({
                    "code": "facts_contract.fuel_variant_missing",
                    "severity": "error",
                    "path": "/fuel_variant_requirements",
                    "expected": slot.variant_id,
                    "slot_ids": [slot.slot_id],
                    "source_claim_ids": list(slot.source_claim_ids),
                    "repair_kind": "locked_value_restore",
                })

    # ---- Localized inserts ------------------------------------------
    if skeleton.localized_insert_slots:
        candidate_inserts = candidate.get("localized_insert_requirements", []) or []
        candidate_rids = {
            i.get("requirement_id") for i in candidate_inserts
            if isinstance(i, dict) and i.get("requirement_id")
        }
        for slot in skeleton.localized_insert_slots:
            if slot.status in LOCK_STATUSES and slot.immutable and slot.requirement_id not in candidate_rids:
                issues.append({
                    "code": "facts_contract.localized_insert_missing",
                    "severity": "error",
                    "path": "/localized_insert_requirements",
                    "expected": slot.requirement_id,
                    "slot_ids": [slot.slot_id],
                    "source_claim_ids": list(slot.source_claim_ids),
                    "repair_kind": "locked_value_restore",
                    "message": f"required localized insert '{slot.requirement_id}' missing from candidate",
                })

    # ---- Unresolved / conflict summary ------------------------------
    if skeleton.unresolved_slots:
        # Only flag the source-critical ones as errors.
        for slot_id in skeleton.unresolved_slots:
            issues.append({
                "code": "facts_contract.source_critical_unresolved",
                "severity": "warning",
                "slot_ids": [slot_id],
                "message": f"slot '{slot_id}' is unresolved",
            })
    for slot_id in skeleton.conflicting_slots:
        if not any(i.get("code") == "facts_contract.conflict_unresolved" for i in issues):
            issues.append({
                "code": "facts_contract.conflict_unresolved",
                "severity": "warning",
                "slot_ids": [slot_id],
                "message": f"slot '{slot_id}' has conflicting evidence",
            })

    return FactsSkeletonPreflightResult(
        ok=not any(i.get("severity") == "error" for i in issues),
        issues=issues,
    )


class FactsContentProposal(AgentBaseModel):
    proposal_id: str = ""
    resolved_fields: dict[str, Any] = Field(default_factory=dict)
    unresolved_fields: list[str] = Field(default_factory=list)
    source_claim_ids: list[str] = Field(default_factory=list)
    proposal_hash: str = ""


class FactsPatchCandidate(AgentBaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    source_claim_ids: list[str] = Field(default_factory=list)
    patch_hash: str = ""


class FactsSkeletonMergeReport(AgentBaseModel):
    ok: bool = False
    merged: FactsPatchCandidate | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _li_entry(slot: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "requirement_id": slot.requirement_id,
        "insert_kind": slot.insert_kind,
        "assembly_type_ids": list(slot.assembly_type_ids),
    }
    if slot.expected_coordinate_count_per_assembly is not None:
        entry["expected_coordinate_count_per_assembly"] = slot.expected_coordinate_count_per_assembly
    return entry


def merge_facts_content_into_skeleton(
    skeleton: Any,
    proposal: FactsContentProposal,
    evidence_ledger: Any | None = None,
) -> FactsSkeletonMergeReport:
    """Merge a FactsContentProposal into the skeleton, producing a candidate.

    Only updates fields declared in proposal.resolved_fields.
    Leaves skeleton-immutable fields unchanged when proposal omits them.

    Phase 8C Step 2: ``deterministically_derived`` slots now also lock
    (the value was Python-derived from source evidence — the LLM cannot
    override it).  ``conflict`` slots do NOT lock; the LLM's proposal is
    used and the slot surfaces as a preflight warning.
    """
    warnings: list[str] = []
    errors: list[str] = []

    if skeleton is None:
        return FactsSkeletonMergeReport(ok=False, errors=["skeleton is None"])

    # Statuses that lock the slot — the LLM cannot override.
    LOCK_STATUSES = {"human_confirmed", "source_backed", "deterministically_derived"}

    candidate: dict[str, Any] = {}

    # Scope.
    if skeleton.model_scope is not None and skeleton.model_scope.status in LOCK_STATUSES:
        candidate["model_scope"] = skeleton.model_scope.value
    elif skeleton.model_scope is not None and skeleton.model_scope.status == "conflict":
        warnings.append("model_scope has conflicting source claims; using LLM proposal")
        if "model_scope" in proposal.resolved_fields:
            candidate["model_scope"] = proposal.resolved_fields["model_scope"]
    elif "model_scope" in proposal.resolved_fields:
        candidate["model_scope"] = proposal.resolved_fields["model_scope"]

    # Features.
    if skeleton.features is not None:
        for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
            val = getattr(skeleton.features, flag, None)
            if val is not None and skeleton.features.status in LOCK_STATUSES:
                candidate[flag] = val
            elif val is not None and skeleton.features.status != "conflict":
                # Unresolved but present — prefer skeleton over proposal to
                # avoid the LLM silently downgrading a feature flag.
                candidate[flag] = val

    # Assembly layout.
    if skeleton.assembly_layout is not None:
        if skeleton.assembly_layout.assembly_count is not None and (
            skeleton.assembly_layout.status in LOCK_STATUSES
            or skeleton.assembly_layout.assembly_count is not None
        ):
            candidate["assembly_count"] = skeleton.assembly_layout.assembly_count
        if skeleton.assembly_layout.core_lattice_size is not None:
            candidate["core_lattice_size"] = list(skeleton.assembly_layout.core_lattice_size)
        if skeleton.assembly_layout.assembly_type_counts:
            candidate["assembly_type_counts"] = dict(skeleton.assembly_layout.assembly_type_counts)

    # Fuel variants.
    fv_list: list[dict[str, Any]] = []
    for slot in skeleton.fuel_variant_slots:
        fv_list.append({
            "variant_id": slot.variant_id,
            "enrichment_wt_percent": slot.enrichment_wt_percent,
            "density_g_cm3": slot.density_g_cm3,
            "assembly_type_ids": list(slot.assembly_type_ids),
        })
    if fv_list:
        candidate["fuel_variant_requirements"] = fv_list

    # Localized inserts.
    li_list: list[dict[str, Any]] = []
    for slot in skeleton.localized_insert_slots:
        li_list.append(_li_entry(slot))
    if li_list:
        candidate["localized_insert_requirements"] = li_list

    # Apply proposal overrides for fields NOT locked by the skeleton.
    # For list-of-dict slots (fuel_variant_requirements,
    # localized_insert_requirements), the locked elements from the skeleton
    # must be preserved even if the proposal supplies a shorter list.
    for field_path, value in proposal.resolved_fields.items():
        if field_path in candidate:
            # Check if the slot is locked.
            is_locked = False
            if field_path == "model_scope" and skeleton.model_scope is not None:
                is_locked = skeleton.model_scope.status in LOCK_STATUSES
            elif field_path in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
                if skeleton.features is not None and skeleton.features.status in LOCK_STATUSES:
                    is_locked = getattr(skeleton.features, field_path, None) is not None
            elif field_path == "assembly_count" and skeleton.assembly_layout is not None:
                is_locked = (
                    skeleton.assembly_layout.status in LOCK_STATUSES
                    and skeleton.assembly_layout.assembly_count is not None
                )
            elif field_path == "fuel_variant_requirements":
                # Locked fuel variants must be preserved even when the
                # proposal supplies a different list.  Merge by variant_id.
                locked_by_id = {
                    slot.variant_id: {
                        "variant_id": slot.variant_id,
                        "enrichment_wt_percent": slot.enrichment_wt_percent,
                        "density_g_cm3": slot.density_g_cm3,
                        "assembly_type_ids": list(slot.assembly_type_ids),
                    }
                    for slot in skeleton.fuel_variant_slots
                    if slot.status in LOCK_STATUSES and slot.immutable
                }
                if locked_by_id:
                    proposal_items = value if isinstance(value, list) else []
                    proposal_ids = {
                        v.get("variant_id") for v in proposal_items
                        if isinstance(v, dict) and v.get("variant_id")
                    }
                    # Drop locked ids from proposal_items (they will be
                    # re-injected from the skeleton) so we don't double-add.
                    cleaned_proposal = [
                        v for v in proposal_items
                        if isinstance(v, dict)
                        and v.get("variant_id") not in locked_by_id
                    ]
                    merged_list = list(locked_by_id.values()) + cleaned_proposal
                    candidate[field_path] = merged_list
                    continue
            elif field_path == "localized_insert_requirements":
                # Same protection for localized inserts.
                locked_by_id = {
                    slot.requirement_id: _li_entry(slot)
                    for slot in skeleton.localized_insert_slots
                    if slot.status in LOCK_STATUSES and slot.immutable
                }
                if locked_by_id:
                    proposal_items = value if isinstance(value, list) else []
                    cleaned_proposal = [
                        v for v in proposal_items
                        if isinstance(v, dict)
                        and v.get("requirement_id") not in locked_by_id
                    ]
                    merged_list = list(locked_by_id.values()) + cleaned_proposal
                    candidate[field_path] = merged_list
                    continue
            if is_locked:
                # Locked scalar — proposal cannot override.
                continue
        candidate[field_path] = value

    from openmc_agent.plan_builder.closed_loop.fingerprints import _digest
    patch_hash = _digest(candidate)
    merged = FactsPatchCandidate(
        patch=candidate,
        source_claim_ids=list(proposal.source_claim_ids),
        patch_hash=patch_hash,
    )
    return FactsSkeletonMergeReport(
        ok=not errors,
        merged=merged,
        warnings=warnings,
        errors=errors,
    )
