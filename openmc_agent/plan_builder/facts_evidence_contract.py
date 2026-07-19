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
]


def run_facts_skeleton_preflight(
    skeleton: Any,
    candidate: dict[str, Any] | None,
) -> FactsSkeletonPreflightResult:
    """Check a candidate Facts patch against the compiled skeleton."""
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

    # Check scope
    if skeleton.model_scope is not None:
        candidate_scope = candidate.get("model_scope", "unknown")
        if skeleton.model_scope.status in ("human_confirmed", "source_backed"):
            if skeleton.model_scope.immutable and candidate_scope != skeleton.model_scope.value:
                issues.append({
                    "code": "facts_skeleton.immutable_field_modified",
                    "severity": "error",
                    "path": "/model_scope",
                    "expected": skeleton.model_scope.value,
                    "actual": candidate_scope,
                    "message": f"model_scope changed from '{skeleton.model_scope.value}' to '{candidate_scope}'",
                })
            elif candidate_scope != skeleton.model_scope.value:
                issues.append({
                    "code": "facts_skeleton.scope_contradiction",
                    "severity": "warning",
                    "path": "/model_scope",
                    "expected": skeleton.model_scope.value,
                    "actual": candidate_scope,
                    "message": f"model_scope '{candidate_scope}' differs from skeleton '{skeleton.model_scope.value}'",
                })

    # Check feature flags
    if skeleton.features is not None:
        for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
            expected_val = getattr(skeleton.features, flag, None)
            if expected_val is not None and skeleton.features.status in ("human_confirmed", "source_backed"):
                candidate_val = candidate.get(flag)
                if candidate_val != expected_val:
                    issues.append({
                        "code": "facts_skeleton.immutable_field_modified",
                        "severity": "error",
                        "path": f"/{flag}",
                        "expected": expected_val,
                        "actual": candidate_val,
                        "message": f"{flag} changed from {expected_val} to {candidate_val}",
                    })

    # Check assembly layout
    if skeleton.assembly_layout is not None and skeleton.assembly_layout.status in ("human_confirmed", "source_backed"):
        expected_count = skeleton.assembly_layout.assembly_count
        candidate_count = candidate.get("assembly_count")
        if expected_count is not None and expected_count != candidate_count:
            issues.append({
                "code": "facts_skeleton.count_mismatch",
                "severity": "error",
                "path": "/assembly_count",
                "expected": expected_count,
                "actual": candidate_count,
            })

    # Check fuel variants
    if skeleton.fuel_variant_slots:
        for slot in skeleton.fuel_variant_slots:
            if slot.status in ("human_confirmed", "source_backed") and slot.immutable:
                candidate_variants = candidate.get("fuel_variant_requirements", [])
                found = any(
                    v.get("variant_id") == slot.variant_id
                    for v in candidate_variants if isinstance(v, dict)
                )
                if not found:
                    issues.append({
                        "code": "facts_skeleton.fuel_variant_modified",
                        "severity": "error",
                        "path": "/fuel_variant_requirements",
                        "expected": slot.variant_id,
                        "message": f"required fuel variant '{slot.variant_id}' missing from candidate",
                    })

    return FactsSkeletonPreflightResult(ok=not any(i.get("severity") == "error" for i in issues), issues=issues)


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


def merge_facts_content_into_skeleton(
    skeleton: Any,
    proposal: FactsContentProposal,
    evidence_ledger: Any | None = None,
) -> FactsSkeletonMergeReport:
    """Merge a FactsContentProposal into the skeleton, producing a candidate.

    Only updates fields declared in proposal.resolved_fields.
    Leaves skeleton-immutable fields unchanged when proposal omits them.
    """
    warnings: list[str] = []
    errors: list[str] = []

    if skeleton is None:
        return FactsSkeletonMergeReport(ok=False, errors=["skeleton is None"])

    candidate: dict[str, Any] = {}

    if skeleton.model_scope is not None and skeleton.model_scope.status in ("human_confirmed", "source_backed"):
        candidate["model_scope"] = skeleton.model_scope.value
    elif "model_scope" in proposal.resolved_fields:
        candidate["model_scope"] = proposal.resolved_fields["model_scope"]

    if skeleton.features is not None:
        for flag in ("has_axial_geometry", "has_spacer_grids", "has_special_pin_map"):
            val = getattr(skeleton.features, flag, None)
            if val is not None:
                candidate[flag] = val

    if skeleton.assembly_layout is not None:
        if skeleton.assembly_layout.assembly_count is not None:
            candidate["assembly_count"] = skeleton.assembly_layout.assembly_count
        if skeleton.assembly_layout.core_lattice_size is not None:
            candidate["core_lattice_size"] = list(skeleton.assembly_layout.core_lattice_size)
        if skeleton.assembly_layout.assembly_type_counts:
            candidate["assembly_type_counts"] = skeleton.assembly_layout.assembly_type_counts

    fv_list: list[dict[str, Any]] = []
    for slot in skeleton.fuel_variant_slots:
        fv_list.append({
            "variant_id": slot.variant_id,
            "enrichment_wt_percent": slot.enrichment_wt_percent,
            "density_g_cm3": slot.density_g_cm3,
            "assembly_type_ids": slot.assembly_type_ids,
        })
    if fv_list:
        candidate["fuel_variant_requirements"] = fv_list

    li_list: list[dict[str, Any]] = []
    for slot in skeleton.localized_insert_slots:
        li_list.append({
            "requirement_id": slot.requirement_id,
            "insert_kind": slot.insert_kind,
            "assembly_type_ids": slot.assembly_type_ids,
        })
    if li_list:
        candidate["localized_insert_requirements"] = li_list

    # Apply proposal overrides
    for field_path, value in proposal.resolved_fields.items():
        if field_path not in candidate or field_path in (
            "model_scope", "has_axial_geometry", "has_spacer_grids",
            "has_special_pin_map", "assembly_count", "core_lattice_size",
            "assembly_type_counts",
        ):
            field_allowed = (
                field_path not in ("model_scope",)
                or skeleton.model_scope is None
                or skeleton.model_scope.status not in ("human_confirmed", "source_backed")
            )
            if field_allowed:
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
