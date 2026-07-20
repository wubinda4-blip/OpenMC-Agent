"""Independent Assembled Plan Critic invocation and strict normalization."""

from __future__ import annotations

from openmc_agent.structured_output import canonical_payload_hash

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .assembled_plan_evidence import build_assembled_plan_evidence_pack
from .assembled_plan_review_prompts import (
    build_assembled_plan_review_prompt,
    build_assembled_plan_review_schema_retry_prompt,
)
from .models import (
    AssembledPlanReviewModelOutput,
    PlanClosedLoopPolicy,
    PlanFindingCategory,
    PlanGateId,
    PlanReviewFinding,
)
from .review_io import StructuredReviewCallSpec, run_structured_review_call


class AssembledPlanReviewResult(AgentBaseModel):
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


def _normalize(output: AssembledPlanReviewModelOutput, pack: Any) -> tuple[list[PlanReviewFinding], list[dict[str, Any]]]:
    evidence_refs = {item.ref_id for item in pack.evidence_items}
    contract_row_ids = {row.row_id for row in pack.contract_matrix.rows}
    accepted: list[PlanReviewFinding] = []
    rejected: list[dict[str, Any]] = []
    for draft in output.findings:
        if not draft.code.strip():
            rejected.append({"code": "assembled_plan_review.invalid_finding_contract", "reason": "blank code"})
            continue
        unknown_refs = set(draft.evidence_refs) - evidence_refs
        if unknown_refs:
            rejected.append({"code": "assembled_plan_review.unknown_evidence_ref", "finding_code": draft.code, "unknown": sorted(unknown_refs)})
            continue
        unknown_rows = set(draft.contract_row_ids) - contract_row_ids
        if unknown_rows:
            rejected.append({"code": "assembled_plan_review.unknown_contract_row", "finding_code": draft.code, "unknown": sorted(unknown_rows)})
            continue
        if "owner" in draft.metadata or "action" in draft.metadata:
            rejected.append({"code": "assembled_plan_review.owner_action_forbidden", "finding_code": draft.code})
            continue
        meta_str = str(draft.metadata).lower()
        if any(kw in meta_str for kw in ("openmc_runtime", "keff", "source_rejection", "lost_particle")):
            rejected.append({"code": "assembled_plan_review.runtime_claim_forbidden", "finding_code": draft.code})
            continue
        if draft.requires_human and draft.repairable_by_llm:
            rejected.append({"code": "assembled_plan_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        if draft.severity.value == "error" and not draft.evidence_refs and draft.category is not PlanFindingCategory.PHYSICAL_AMBIGUITY:
            rejected.append({"code": "assembled_plan_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        excerpts = []
        from .models import SourceExcerpt
        for item in pack.evidence_items:
            if item.ref_id in draft.evidence_refs:
                excerpts.append(SourceExcerpt(source_id=item.ref_id, text=str(item.value)[:500], evidence_hash=item.canonical_hash))
        finding = PlanReviewFinding(
            gate_id=PlanGateId.ASSEMBLED_PLAN, code=draft.code, severity=draft.severity,
            category=draft.category, message=draft.message, source_evidence=excerpts,
            affected_patch_types=["facts", "materials", "universes", "axial_layers", "axial_overlays"],
            affected_json_paths=draft.affected_json_paths,
            repairable_by_llm=draft.repairable_by_llm, requires_human=draft.requires_human,
            confidence=draft.confidence,
            metadata={"expected_semantics": draft.expected_semantics, "current_semantics": draft.current_semantics, "contract_row_ids": draft.contract_row_ids, **draft.metadata},
        )
        accepted.append(finding)
    merged = {item.finding_id: item for item in accepted}
    return list(merged.values()), rejected


def run_assembled_plan_review(*, evidence_pack: Any, reviewer_client: Any, state: Any, policy: PlanClosedLoopPolicy) -> AssembledPlanReviewResult:
    result = AssembledPlanReviewResult()
    call = run_structured_review_call(
        client=reviewer_client,
        initial_prompt=build_assembled_plan_review_prompt(evidence_pack),
        retry_prompt_builder=lambda raw, error: build_assembled_plan_review_schema_retry_prompt(evidence_pack, error, raw),
        output_model=AssembledPlanReviewModelOutput,
        call_spec=StructuredReviewCallSpec(
            role_id="assembled_plan_review", gate_id=PlanGateId.ASSEMBLED_PLAN,
            schema_name="AssembledPlanReviewModelOutput", json_schema=AssembledPlanReviewModelOutput.model_json_schema(),
            artifact_prefix="assembled_plan_review",
            input_payload_hash=canonical_payload_hash(evidence_pack)
        ),
        state=state,
        stage=state.plan_loop_stages.get("plan_gate_assembled_plan"),
        policy=policy,
    )
    result.reviewer_calls += call.call_count
    result.schema_retries += call.schema_retry_count
    for attempt in call.attempts:
        result.raw_outputs.append(attempt.raw_text)
        result.call_metadata.append(attempt.model_dump(mode="json", exclude={"raw_text"}))
    if not call.ok or call.parsed_output is None:
        result.error = f"assembled_plan_review.schema_invalid: {call.error_detail}"
        result.failure_code = (
            "assembled_plan_review.budget_exhausted"
            if call.error_code == "planning.closed_loop.budget_exhausted"
            else call.error_code or "assembled_plan_review.schema_invalid"
        )
        return result
    output = AssembledPlanReviewModelOutput.model_validate(call.parsed_output)
    findings, rejected = _normalize(output, evidence_pack)
    result.findings = findings
    result.rejected = rejected
    result.outputs.append({"output": output.model_dump(mode="json")})
    expected_rows = {r.row_id for r in evidence_pack.contract_matrix.rows}
    reviewed_rows = set(output.reviewed_contract_row_ids)
    # review_status="complete" implies coverage; per-item lists are advisory.
    result.coverage_complete = output.review_status == "complete"
    if not result.coverage_complete:
        result.failure_code = "assembled_plan_review.coverage_incomplete"
    result.ok = not result.error
    return result


__all__ = ["AssembledPlanReviewResult", "run_assembled_plan_review"]
