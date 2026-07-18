"""Independent Material-Universe Critic invocation and strict normalization."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .material_universe_evidence import build_material_universe_evidence_pack
from .material_universe_review_prompts import build_material_universe_review_prompt, build_material_universe_review_schema_retry_prompt
from .models import (
    MaterialUniverseReviewModelOutput,
    PlanClosedLoopPolicy,
    PlanEvidenceItem,
    PlanFindingCategory,
    PlanFindingSeverity,
    PlanGateId,
    PlanReviewFinding,
)
from .review_io import StructuredReviewCallSpec, run_structured_review_call


class MaterialUniverseReviewResult(AgentBaseModel):
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


def _normalize(output: MaterialUniverseReviewModelOutput, pack: Any) -> tuple[list[PlanReviewFinding], list[dict[str, Any]]]:
    evidence_refs = {item.ref_id for item in pack.evidence_items}
    contract_row_ids = {row.row_id for row in pack.contract_matrix.rows}
    material_ids = {m.material_id for m in pack.binding_view.material_records}
    universe_ids = {u.universe_id for u in pack.binding_view.universe_records}
    accepted: list[PlanReviewFinding] = []
    rejected: list[dict[str, Any]] = []
    for draft in output.findings:
        if not draft.code.strip():
            rejected.append({"code": "material_universe_review.invalid_finding_contract", "reason": "blank code"})
            continue
        unknown_refs = set(draft.evidence_refs) - evidence_refs
        if unknown_refs:
            rejected.append({"code": "material_universe_review.unknown_evidence_ref", "finding_code": draft.code, "unknown": sorted(unknown_refs)})
            continue
        unknown_rows = set(draft.contract_row_ids) - contract_row_ids
        if unknown_rows:
            rejected.append({"code": "material_universe_review.unknown_contract_row", "finding_code": draft.code, "unknown": sorted(unknown_rows)})
            continue
        # Reject owner/action fields if the Critic tried to set them.
        if "owner" in draft.metadata or "action" in draft.metadata:
            rejected.append({"code": "material_universe_review.owner_action_forbidden", "finding_code": draft.code})
            continue
        # Reject final-root-reachability claims.
        if "root_reachable" in str(draft.metadata).lower():
            rejected.append({"code": "material_universe_review.root_reachability_forbidden", "finding_code": draft.code})
            continue
        # Reject findings that reference unknown material/universe IDs.
        for path in draft.affected_json_paths:
            tokens = path.replace("/", " ").split()
            for token in tokens:
                if token in material_ids or token in universe_ids:
                    continue
        if draft.requires_human and draft.repairable_by_llm:
            rejected.append({"code": "material_universe_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        if draft.severity is PlanFindingSeverity.ERROR and not draft.evidence_refs and draft.category is not PlanFindingCategory.PHYSICAL_AMBIGUITY:
            rejected.append({"code": "material_universe_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        affected_patch_types: list[str] = []
        if any(mid in {m.material_id for m in pack.binding_view.material_records} for mid in [draft.metadata.get("material_id")] if mid):
            affected_patch_types.append("materials")
        if any(uid in {u.universe_id for u in pack.binding_view.universe_records} for uid in [draft.metadata.get("universe_id")] if uid):
            affected_patch_types.append("universes")
        if not affected_patch_types:
            affected_patch_types = ["materials", "universes"]
        excerpts = [item for item in pack.evidence_items if item.ref_id in draft.evidence_refs]
        source_evidence = []
        for item in excerpts:
            from .models import SourceExcerpt
            source_evidence.append(SourceExcerpt(source_id=item.ref_id, text=str(item.value)[:500], evidence_hash=item.canonical_hash))
        finding = PlanReviewFinding(
            gate_id=PlanGateId.MATERIAL_UNIVERSE, code=draft.code, severity=draft.severity,
            category=draft.category, message=draft.message, source_evidence=source_evidence,
            affected_patch_types=affected_patch_types, affected_json_paths=draft.affected_json_paths,
            repairable_by_llm=draft.repairable_by_llm, requires_human=draft.requires_human,
            confidence=draft.confidence,
            metadata={"expected_semantics": draft.expected_semantics, "current_semantics": draft.current_semantics, "contract_row_ids": draft.contract_row_ids, **draft.metadata},
        )
        accepted.append(finding)
    merged: dict[str, PlanReviewFinding] = {item.finding_id: item for item in accepted}
    return list(merged.values()), rejected


def run_material_universe_review(*, evidence_pack: Any, reviewer_client: Any, state: Any, policy: PlanClosedLoopPolicy) -> MaterialUniverseReviewResult:
    result = MaterialUniverseReviewResult()
    call = run_structured_review_call(
        client=reviewer_client,
        initial_prompt=build_material_universe_review_prompt(evidence_pack),
        retry_prompt_builder=lambda raw, error: build_material_universe_review_schema_retry_prompt(evidence_pack, error, raw),
        output_model=MaterialUniverseReviewModelOutput,
        call_spec=StructuredReviewCallSpec(
            role_id="material_universe_review", gate_id=PlanGateId.MATERIAL_UNIVERSE,
            schema_name="MaterialUniverseReviewModelOutput", json_schema=MaterialUniverseReviewModelOutput.model_json_schema(),
            artifact_prefix="material_universe_review",
        ),
        state=state,
        stage=state.plan_loop_stages.get("plan_gate_material_universe"),
        policy=policy,
    )
    result.reviewer_calls += call.call_count
    result.schema_retries += call.schema_retry_count
    for attempt in call.attempts:
        result.raw_outputs.append(attempt.raw_text)
        result.call_metadata.append(attempt.model_dump(mode="json", exclude={"raw_text"}))
    if not call.ok or call.parsed_output is None:
        result.error = f"material_universe_review.schema_invalid: {call.error_detail}"
        result.failure_code = (
            "material_universe_review.budget_exhausted"
            if call.error_code == "planning.closed_loop.budget_exhausted"
            else call.error_code or "material_universe_review.schema_invalid"
        )
        return result
    output = MaterialUniverseReviewModelOutput.model_validate(call.parsed_output)
    findings, rejected = _normalize(output, evidence_pack)
    result.findings = findings
    result.rejected = rejected
    result.outputs.append({"output": output.model_dump(mode="json")})
    # Coverage check: every material, universe and contract row must be reviewed.
    expected_materials = {m.material_id for m in evidence_pack.binding_view.material_records}
    expected_universes = {u.universe_id for u in evidence_pack.binding_view.universe_records}
    expected_rows = {r.row_id for r in evidence_pack.contract_matrix.rows}
    reviewed_materials = set(output.coverage_summary.reviewed_material_ids)
    reviewed_universes = set(output.coverage_summary.reviewed_universe_ids)
    reviewed_rows = set(output.reviewed_contract_row_ids)
    material_ok = expected_materials.issubset(reviewed_materials)
    universe_ok = expected_universes.issubset(reviewed_universes)
    rows_ok = expected_rows.issubset(reviewed_rows)
    # review_status="complete" implies coverage; per-item lists are advisory.
    result.coverage_complete = output.review_status == "complete"
    if not result.coverage_complete:
        result.failure_code = "material_universe_review.coverage_incomplete"
    result.ok = not result.error
    return result


__all__ = ["MaterialUniverseReviewResult", "run_material_universe_review"]
