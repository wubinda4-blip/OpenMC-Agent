"""Independent Facts Critic invocation and strict Python normalization."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .facts_review_prompts import build_facts_review_prompt, build_facts_review_schema_retry_prompt
from .models import (
    FactsReviewModelOutput, PlanClosedLoopPolicy, PlanEvidencePack, PlanFindingCategory,
    PlanFindingSeverity, PlanGateId, PlanReviewFinding, SourceExcerpt,
)
from .review_io import StructuredReviewCallSpec, run_structured_review_call


class FactsReviewResult(AgentBaseModel):
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


def _normalize(output: FactsReviewModelOutput, pack: PlanEvidencePack) -> tuple[list[PlanReviewFinding], list[dict[str, Any]]]:
    evidence = {item.evidence_hash: item for item in pack.source_excerpts}
    facts = pack.relevant_patches.get("facts", {})
    accepted: list[PlanReviewFinding] = []
    rejected: list[dict[str, Any]] = []
    for draft in output.findings:
        if not draft.code.strip():
            rejected.append({"code": "facts_review.invalid_finding_contract", "reason": "blank code"})
            continue
        unknown = set(draft.evidence_hashes) - set(evidence)
        if unknown:
            rejected.append({"code": "facts_review.unknown_evidence_hash", "finding_code": draft.code, "unknown": sorted(unknown)})
            continue
        if any(not path.startswith("/") or path.startswith("/materials") or path.startswith("/universes") for path in draft.affected_json_paths):
            rejected.append({"code": "facts_review.path_out_of_scope", "finding_code": draft.code})
            continue
        if draft.requires_human and draft.repairable_by_llm:
            rejected.append({"code": "facts_review.invalid_finding_contract", "finding_code": draft.code})
            continue
        if draft.severity is PlanFindingSeverity.ERROR and draft.category is not PlanFindingCategory.PHYSICAL_AMBIGUITY:
            # An error must be both evidence-grounded and actionable against
            # a specific FactsPatch field.  This rejects self-contradictory
            # critic output such as "this is consistent; no issue" labelled
            # as an error with no affected path; accepting such a finding
            # would fail-close a valid plan without a repairable contract.
            if not draft.evidence_hashes or not draft.affected_json_paths:
                rejected.append({"code": "facts_review.invalid_finding_contract", "finding_code": draft.code})
                continue
        excerpts = [evidence[key] for key in draft.evidence_hashes]
        finding = PlanReviewFinding(
            gate_id=PlanGateId.FACTS, code=draft.code, severity=draft.severity,
            category=draft.category, message=draft.message, source_evidence=excerpts,
            affected_patch_types=["facts"], affected_json_paths=draft.affected_json_paths,
            repairable_by_llm=draft.repairable_by_llm, requires_human=draft.requires_human,
            confidence=draft.confidence,
            metadata={"expected_value": draft.expected_value, "current_value": draft.current_value,
                      "candidate_interpretations": [item.model_dump(mode="json") for item in draft.candidate_interpretations],
                      "downstream_impact": draft.downstream_impact},
        )
        accepted.append(finding)
    # Finding identity excludes wording and unions evidence under the same semantic fingerprint.
    merged: dict[str, PlanReviewFinding] = {item.finding_id: item for item in accepted}
    return list(merged.values()), rejected


def run_facts_review(*, evidence_packs: list[PlanEvidencePack], reviewer_client: Any, state: Any, policy: PlanClosedLoopPolicy) -> FactsReviewResult:
    result = FactsReviewResult()
    all_findings: list[PlanReviewFinding] = []
    all_rejected: list[dict[str, Any]] = []
    for pack in evidence_packs:
        call = run_structured_review_call(
            client=reviewer_client, initial_prompt=build_facts_review_prompt(pack),
            retry_prompt_builder=lambda raw, error: build_facts_review_schema_retry_prompt(pack, error, raw),
            output_model=FactsReviewModelOutput,
            call_spec=StructuredReviewCallSpec(
                role_id="facts_review", gate_id=PlanGateId.FACTS,
                schema_name="FactsReviewModelOutput", json_schema=FactsReviewModelOutput.model_json_schema(),
                artifact_prefix="facts_review",
            ), state=state, stage=state.plan_loop_stages.get("plan_gate_facts"), policy=policy,
        )
        result.reviewer_calls += call.call_count
        result.schema_retries += call.schema_retry_count
        for attempt in call.attempts:
            result.raw_outputs.append(attempt.raw_text)
            result.call_metadata.append({"pack_id": pack.evidence_pack_id, **attempt.model_dump(mode="json", exclude={"raw_text"})})
        if not call.ok or call.parsed_output is None:
            result.error = f"facts_review.schema_invalid: {call.error_detail}"
            result.failure_code = (
                "facts_review.budget_exhausted"
                if call.error_code == "planning.closed_loop.budget_exhausted"
                else call.error_code or "facts_review.schema_invalid"
            )
            return result
        output = FactsReviewModelOutput.model_validate(call.parsed_output)
        findings, rejected = _normalize(output, pack)
        all_findings.extend(findings)
        all_rejected.extend(rejected)
        result.outputs.append({"pack_id": pack.evidence_pack_id, "output": output.model_dump(mode="json")})
    result.findings = list({finding.finding_id: finding for finding in all_findings}.values())
    result.rejected = all_rejected
    expected = {item.evidence_hash for pack in evidence_packs for item in pack.source_excerpts}
    reviewed = {key for output in result.outputs for key in output["output"].get("reviewed_evidence_hashes", [])}
    result.coverage_complete = bool(expected) and expected.issubset(reviewed)
    if not result.coverage_complete:
        result.failure_code = "facts_review.coverage_incomplete"
    result.ok = not result.error
    return result
