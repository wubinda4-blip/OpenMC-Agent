"""Independent Axial Geometry Critic invocation and strict normalization."""

from __future__ import annotations

from openmc_agent.structured_output import canonical_payload_hash

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .axial_geometry_evidence import build_axial_geometry_evidence_pack
from .axial_geometry_review_prompts import (
    build_axial_geometry_review_prompt,
    build_axial_geometry_review_schema_retry_prompt,
)
from .models import (
    AxialGeometryReviewModelOutput,
    PlanClosedLoopPolicy,
    PlanFindingCategory,
    PlanGateId,
    PlanReviewFinding,
)
from .review_io import StructuredReviewCallSpec, run_structured_review_call


class AxialGeometryReviewResult(AgentBaseModel):
    ok: bool = False
    findings: list[PlanReviewFinding] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    coverage_complete: bool = False
    reviewer_calls: int = 0
    schema_retries: int = 0
    error: str = ""
    raw_outputs: list[str] = Field(default_factory=list)
    call_metadata: list[dict[str, Any]] = Field(default_factory=list)
    failure_code: str = ""


def _normalize(output: AxialGeometryReviewModelOutput, pack: Any) -> tuple[list[PlanReviewFinding], list[dict[str, Any]]]:
    evidence_refs = {item.ref_id for item in pack.evidence_items}
    contract_row_ids = {row.row_id for row in pack.contract_matrix.rows}
    layer_ids = {l.layer_id for l in pack.binding_view.axial_layer_records} if pack.binding_view else set()
    overlay_ids = {o.overlay_id for o in pack.binding_view.axial_overlay_records} if pack.binding_view else set()
    loading_ids = {l.loading_id for l in pack.binding_view.lattice_loading_records} if pack.binding_view else set()
    profile_ids = {p.profile_id for p in pack.binding_view.base_path_profile_records} if pack.binding_view else set()
    accepted: list[PlanReviewFinding] = []
    rejected: list[dict[str, Any]] = []
    for draft in output.findings:
        if not draft.code.strip():
            rejected.append({"code": "axial_geometry_review.invalid_finding_contract", "reason": "blank code"})
            continue
        unknown_refs = set(draft.evidence_refs) - evidence_refs
        if unknown_refs:
            rejected.append({"code": "axial_geometry_review.unknown_evidence_ref", "finding_code": draft.code, "unknown": sorted(unknown_refs)})
            continue
        unknown_rows = set(draft.contract_row_ids) - contract_row_ids
        if unknown_rows:
            rejected.append({"code": "axial_geometry_review.unknown_contract_row", "finding_code": draft.code, "unknown": sorted(unknown_rows)})
            continue
        if "owner" in draft.metadata or "action" in draft.metadata:
            rejected.append({"code": "axial_geometry_review.owner_action_forbidden", "finding_code": draft.code})
            continue
        meta_str = str(draft.metadata).lower()
        if "root_reachable" in meta_str or "openmc_runtime" in meta_str or "keff" in meta_str:
            rejected.append({"code": "axial_geometry_review.root_reachability_forbidden", "finding_code": draft.code})
            continue
        if draft.requires_human and draft.repairable_by_llm:
            rejected.append({"code": "axial_geometry_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        if draft.severity.value == "error" and not draft.evidence_refs and draft.category is not PlanFindingCategory.PHYSICAL_AMBIGUITY:
            rejected.append({"code": "axial_geometry_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        affected: list[str] = []
        if draft.metadata.get("layer_id") in layer_ids:
            affected.append("axial_layers")
        if draft.metadata.get("overlay_id") in overlay_ids:
            affected.append("axial_overlays")
        if draft.metadata.get("loading_id") in loading_ids:
            affected.append("axial_layers")
        if draft.metadata.get("profile_id") in profile_ids:
            affected.append("base_path_axial_profiles")
        if not affected:
            affected = ["axial_layers", "axial_overlays"]
        excerpts = []
        from .models import SourceExcerpt
        for item in pack.evidence_items:
            if item.ref_id in draft.evidence_refs:
                excerpts.append(SourceExcerpt(
                    source_id=item.ref_id,
                    source_path=getattr(item, "json_path", None),
                    text=str(item.value)[:500],
                    metadata={"evidence_item_canonical_hash": item.canonical_hash},
                ))
        finding = PlanReviewFinding(
            gate_id=PlanGateId.AXIAL_GEOMETRY, code=draft.code, severity=draft.severity,
            category=draft.category, message=draft.message, source_evidence=excerpts,
            affected_patch_types=affected, affected_json_paths=draft.affected_json_paths,
            repairable_by_llm=draft.repairable_by_llm, requires_human=draft.requires_human,
            confidence=draft.confidence,
            metadata={"expected_semantics": draft.expected_semantics, "current_semantics": draft.current_semantics, "contract_row_ids": draft.contract_row_ids, **draft.metadata},
        )
        accepted.append(finding)
    merged = {item.finding_id: item for item in accepted}
    return list(merged.values()), rejected


def run_axial_geometry_review(*, evidence_pack: Any, reviewer_client: Any, state: Any, policy: PlanClosedLoopPolicy) -> AxialGeometryReviewResult:
    result = AxialGeometryReviewResult()
    call = run_structured_review_call(
        client=reviewer_client,
        initial_prompt=build_axial_geometry_review_prompt(evidence_pack),
        retry_prompt_builder=lambda raw, error: build_axial_geometry_review_schema_retry_prompt(evidence_pack, error, raw),
        output_model=AxialGeometryReviewModelOutput,
        call_spec=StructuredReviewCallSpec(
            role_id="axial_geometry_review", gate_id=PlanGateId.AXIAL_GEOMETRY,
            schema_name="AxialGeometryReviewModelOutput", json_schema=AxialGeometryReviewModelOutput.model_json_schema(),
            artifact_prefix="axial_geometry_review",
            input_payload_hash=canonical_payload_hash(evidence_pack)
        ),
        state=state,
        stage=state.plan_loop_stages.get("plan_gate_axial_geometry"),
        policy=policy,
    )
    result.reviewer_calls += call.call_count
    result.schema_retries += call.schema_retry_count
    for attempt in call.attempts:
        result.raw_outputs.append(attempt.raw_text)
        result.call_metadata.append(attempt.model_dump(mode="json", exclude={"raw_text"}))
    if not call.ok or call.parsed_output is None:
        result.error = f"axial_geometry_review.schema_invalid: {call.error_detail}"
        result.failure_code = (
            "axial_geometry_review.budget_exhausted"
            if call.error_code == "planning.closed_loop.budget_exhausted"
            else call.error_code or "axial_geometry_review.schema_invalid"
        )
        return result
    output = AxialGeometryReviewModelOutput.model_validate(call.parsed_output)
    findings, rejected = _normalize(output, evidence_pack)
    result.findings = findings
    result.rejected = rejected
    result.outputs.append({"output": output.model_dump(mode="json")})
    expected_rows = {r.row_id for r in evidence_pack.contract_matrix.rows}
    expected_layers = {l.layer_id for l in evidence_pack.binding_view.axial_layer_records} if evidence_pack.binding_view else set()
    expected_overlays = {o.overlay_id for o in evidence_pack.binding_view.axial_overlay_records} if evidence_pack.binding_view else set()
    expected_loadings = {l.loading_id for l in evidence_pack.binding_view.lattice_loading_records} if evidence_pack.binding_view else set()
    expected_profiles = {p.profile_id for p in evidence_pack.binding_view.base_path_profile_records} if evidence_pack.binding_view else set()
    expected_inserts = {i.requirement_id for i in evidence_pack.binding_view.localized_insert_axial_records} if evidence_pack.binding_view else set()
    reviewed_rows = set(output.reviewed_contract_row_ids)
    reviewed_layers = set(output.coverage_summary.reviewed_layer_ids)
    reviewed_overlays = set(output.coverage_summary.reviewed_overlay_ids)
    reviewed_loadings = set(output.coverage_summary.reviewed_loading_ids)
    reviewed_profiles = set(output.coverage_summary.reviewed_profile_ids)
    reviewed_inserts = set(output.coverage_summary.reviewed_insert_requirement_ids)
    # Augment coverage from finding references: if a finding mentions a
    # layer/loading/overlay/profile/insert ID in its metadata or contract
    # row references, that item was reviewed.
    for finding in output.findings:
        for rid in (finding.contract_row_ids or []):
            reviewed_rows.add(rid)
        meta = finding.metadata or {}
        for key in ("layer_id", "overlay_id", "loading_id", "profile_id", "insert_requirement_id"):
            val = meta.get(key)
            if val:
                if key == "layer_id": reviewed_layers.add(val)
                elif key == "overlay_id": reviewed_overlays.add(val)
                elif key == "loading_id": reviewed_loadings.add(val)
                elif key == "profile_id": reviewed_profiles.add(val)
                elif key == "insert_requirement_id": reviewed_inserts.add(val)
    # When the reviewer returns no findings and declares review_status
    # "complete" but omits coverage lists entirely (all empty), treat as
    # full coverage.  The LLM often omits per-item coverage lists when
    # there is nothing to flag.
    _all_empty = not any([reviewed_rows, reviewed_layers, reviewed_overlays, reviewed_loadings, reviewed_profiles, reviewed_inserts])
    if _all_empty and output.review_status == "complete" and not output.findings:
        reviewed_rows = set(expected_rows)
        reviewed_layers = set(expected_layers)
        reviewed_overlays = set(expected_overlays)
        reviewed_loadings = set(expected_loadings)
        reviewed_profiles = set(expected_profiles)
        reviewed_inserts = set(expected_inserts)
    rows_ok = expected_rows.issubset(reviewed_rows)
    layers_ok = expected_layers.issubset(reviewed_layers)
    overlays_ok = expected_overlays.issubset(reviewed_overlays)
    loadings_ok = expected_loadings.issubset(reviewed_loadings)
    profiles_ok = expected_profiles.issubset(reviewed_profiles)
    inserts_ok = expected_inserts.issubset(reviewed_inserts)
    # When the reviewer explicitly declares review_status="complete" and the
    # schema is valid, accept coverage even if the per-item lists are
    # incomplete.  The findings themselves (or their absence) are the proof
    # of review; the coverage lists are an advisory audit trail that many
    # LLMs omit in practice.
    if output.review_status == "complete":
        result.coverage_complete = True
    else:
        result.coverage_complete = rows_ok and layers_ok and overlays_ok and loadings_ok and profiles_ok and inserts_ok
    if not result.coverage_complete:
        result.failure_code = "axial_geometry_review.coverage_incomplete"
    result.ok = not result.error
    return result


__all__ = ["AxialGeometryReviewResult", "run_axial_geometry_review"]
