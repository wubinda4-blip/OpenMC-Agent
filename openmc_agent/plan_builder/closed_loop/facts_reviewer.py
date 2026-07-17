"""Independent Facts Critic invocation and strict Python normalization."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .facts_review_prompts import build_facts_review_prompt, build_facts_review_schema_retry_prompt
from .models import (
    FactsReviewModelOutput, PlanClosedLoopPolicy, PlanEvidencePack, PlanFindingCategory,
    PlanFindingSeverity, PlanGateId, PlanReviewFinding, SourceExcerpt,
)


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


def _call(client: Any, prompt: str) -> str | dict[str, Any]:
    if hasattr(client, "generate_patch_json"):
        return client.generate_patch_json(
            prompt=prompt, patch_type="facts_review",
            json_schema=FactsReviewModelOutput.model_json_schema(), temperature=0,
        )
    return client(prompt)


def _parse_json_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Recover a single JSON object from a provider which ignored JSON mode.

    We never treat prose as a finding.  The scan is intentionally conservative:
    it accepts a complete object only, preferring the final object because
    reasoning models commonly prepend an explanatory draft.
    """
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    if not candidates:
        raise ValueError("facts_review.output_not_json")
    # A top-level review contract is more specific than nested JSON objects
    # such as ``coverage_summary``.  Prefer it even when it is followed by
    # nested objects in the same response.
    return next((item for item in reversed(candidates) if "review_status" in item), candidates[-1])


def _output_mode_metadata(client: Any) -> dict[str, Any]:
    return {
        "requested_output_mode": getattr(client, "last_output_mode_requested", "protocol_or_plain_prompt"),
        "actual_output_mode": getattr(client, "last_output_mode_used", "protocol_or_plain_prompt"),
        "structured_fallback_used": bool(getattr(client, "last_output_fallback_used", False)),
        "fallback_reasons": list(getattr(client, "last_output_fallback_reasons", [])),
    }


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
        if draft.severity is PlanFindingSeverity.ERROR and not draft.evidence_hashes and draft.category is not PlanFindingCategory.PHYSICAL_AMBIGUITY:
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
        raw: Any = None
        for attempt in range(2):
            try:
                if state.plan_loop_additional_llm_calls >= policy.max_total_additional_llm_calls:
                    result.error = "facts_review.budget_exhausted"
                    result.failure_code = "facts_review.budget_exhausted"
                    return result
                prompt = (
                    build_facts_review_prompt(pack)
                    if attempt == 0
                    else build_facts_review_schema_retry_prompt(pack, "facts_review.schema_invalid", raw if isinstance(raw, str) else None)
                )
                raw = _call(reviewer_client, prompt)
                if isinstance(raw, str):
                    result.raw_outputs.append(raw)
                result.reviewer_calls += 1
                state.plan_loop_additional_llm_calls += 1
                result.call_metadata.append({"pack_id": pack.evidence_pack_id, "attempt": attempt + 1, **_output_mode_metadata(reviewer_client)})
                parsed = _parse_json_payload(raw)
                output = FactsReviewModelOutput.model_validate(parsed)
                findings, rejected = _normalize(output, pack)
                all_findings.extend(findings)
                all_rejected.extend(rejected)
                result.outputs.append({"pack_id": pack.evidence_pack_id, "output": output.model_dump(mode="json")})
                break
            except Exception as exc:
                if attempt == 0:
                    result.schema_retries += 1
                    continue
                result.error = f"facts_review.schema_invalid: {exc}"
                result.failure_code = "facts_review.schema_invalid"
                return result
    result.findings = list({finding.finding_id: finding for finding in all_findings}.values())
    result.rejected = all_rejected
    expected = {item.evidence_hash for pack in evidence_packs for item in pack.source_excerpts}
    reviewed = {key for output in result.outputs for key in output["output"].get("reviewed_evidence_hashes", [])}
    result.coverage_complete = bool(expected) and expected.issubset(reviewed)
    if not result.coverage_complete:
        result.failure_code = "facts_review.coverage_incomplete"
    result.ok = not result.error
    return result
