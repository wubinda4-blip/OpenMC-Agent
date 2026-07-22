"""Split Material-Universe review into bounded independent reviewers."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import canonical_payload_hash

from .material_universe_reviewer import MaterialUniverseReviewResult, _normalize
from .models import (
    MaterialUniverseReviewCoverageSummary,
    MaterialUniverseReviewFindingDraft,
    MaterialUniverseReviewModelOutput,
    PlanGateId,
)
from .review_io import StructuredReviewCallSpec, run_structured_review_call


class _SplitReviewOutput(AgentBaseModel):
    review_status: Literal["complete", "insufficient_evidence", "malformed_input"]
    findings: list[dict[str, Any]] = Field(default_factory=list)
    reviewed_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_refs: list[str] = Field(default_factory=list)
    concise_summary: str = ""


class MaterialReviewOutput(_SplitReviewOutput):
    """Schema restricted to material coverage."""


class UniverseReviewOutput(_SplitReviewOutput):
    """Schema restricted to universe structure."""


class BindingReviewOutput(_SplitReviewOutput):
    """Schema restricted to material-to-universe-to-cell edges."""


def _scope_payload(pack: Any, scope: str) -> dict[str, Any]:
    view = pack.binding_view
    matrix = pack.contract_matrix
    if scope == "materials":
        records = [item.model_dump(mode="json") for item in view.material_records]
        rows = [row.model_dump(mode="json") for row in matrix.rows if row.row_kind in {"source_material_coverage", "fuel_variant_identity"}]
        instruction = "Review only material existence, role, density, composition, isotope resolution, and fuel variant identity."
        issue_kinds = {"source_material_coverage", "fuel_variant_identity"}
    elif scope == "universes":
        records = [item.model_dump(mode="json") for item in view.universe_records]
        rows = [row.model_dump(mode="json") for row in matrix.rows if row.row_kind == "required_universe_material_structure"]
        instruction = (
            "Review only Universe existence, geometry profile, required cells, and required material roles. "
            "UniverseRecord.material_ids is a de-duplicated set, not a cell-aligned material vector; "
            "cell-to-material mapping belongs to the binding scope."
        )
        issue_kinds = {"required_universe_material_structure"}
    else:
        records = [item.model_dump(mode="json") for item in view.cell_material_bindings]
        rows = [row.model_dump(mode="json") for row in matrix.rows if row.row_kind == "material_to_cell_binding"]
        instruction = "Review only material-to-universe-to-cell bindings and protected static edges."
        issue_kinds = {"material_to_cell_binding"}
    return {
        "gate_id": "material_universe", "review_scope": scope, "input_hash": pack.input_hash,
        "records": records, "contract_rows": rows,
        "deterministic_issues": [
            issue for issue in pack.deterministic_issues
            if issue.get("row_kind", "source_material_coverage") in issue_kinds
        ],
        "evidence_refs": [item.ref_id for item in pack.evidence_items],
        "instructions": [
            instruction,
            "Do not choose an owner or retry action.",
            "Do not repeat deterministic issues.",
            "Return findings=[] when no semantic issue remains.",
            "If findings is non-empty, every finding must include all required fields: code, severity, category, message, evidence_refs, contract_row_ids, affected_json_paths, repairable_by_llm, requires_human, confidence, expected_semantics, current_semantics, metadata.",
        ],
        "required_output_shape": {"review_status": "complete", "findings": [], "reviewed_ids": [], "reviewed_evidence_refs": [], "concise_summary": ""},
    }


def _prompt(pack: Any, scope: str) -> str:
    return "Review one bounded Material-Universe scope.\nINPUT:\n" + json.dumps(_scope_payload(pack, scope), ensure_ascii=False, indent=2)


def _retry_prompt(pack: Any, scope: str, error: str, raw: str) -> str:
    return f"Schema error for {scope}: {error}. Return one JSON object only. Previous output: {raw[:500]}\n" + _prompt(pack, scope)


_REQUIRED_FINDING_KEYS = {"code", "severity", "category", "message", "confidence"}


def _filter_raw_findings(raw_findings: list[dict[str, Any]]) -> tuple[list[MaterialUniverseReviewFindingDraft], list[dict[str, Any]]]:
    """Filter incomplete finding dicts; return (valid drafts, rejected)."""
    valid: list[MaterialUniverseReviewFindingDraft] = []
    rejected: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        missing = _REQUIRED_FINDING_KEYS - set(raw.keys())
        if missing:
            rejected.append({"code": "material_universe_review.invalid_finding_contract", "reason": f"missing fields: {sorted(missing)}", "raw_code": raw.get("code", "")})
            continue
        try:
            valid.append(MaterialUniverseReviewFindingDraft.model_validate(raw))
        except Exception as exc:
            rejected.append({"code": "material_universe_review.invalid_finding_contract", "reason": str(exc)[:200], "raw_code": raw.get("code", "")})
    return valid, rejected


def _as_combined_output(parsed: _SplitReviewOutput, scope: str) -> tuple[MaterialUniverseReviewModelOutput, list[dict[str, Any]]]:
    """Convert split output + filter incomplete findings."""
    valid_findings, rejected = _filter_raw_findings(parsed.findings)
    for finding in valid_findings:
        finding.metadata = {**finding.metadata, "review_scope": scope}
    coverage = MaterialUniverseReviewCoverageSummary(reviewed_evidence_refs=list(parsed.reviewed_evidence_refs))
    if scope == "materials":
        coverage.reviewed_material_ids = list(parsed.reviewed_ids)
    elif scope == "universes":
        coverage.reviewed_universe_ids = list(parsed.reviewed_ids)
    else:
        coverage.reviewed_contract_row_ids = list(parsed.reviewed_ids)
    combined = MaterialUniverseReviewModelOutput(
        review_status=parsed.review_status, findings=valid_findings,
        reviewed_contract_row_ids=list(parsed.reviewed_ids) if scope == "binding" else [],
        reviewed_evidence_refs=list(parsed.reviewed_evidence_refs), coverage_summary=coverage,
        concise_summary=parsed.concise_summary,
    )
    return combined, rejected


def run_material_universe_review_split(*, evidence_pack: Any, reviewer_client: Any, state: Any, policy: Any) -> MaterialUniverseReviewResult:
    result = MaterialUniverseReviewResult()
    scopes = (("materials", MaterialReviewOutput), ("universes", UniverseReviewOutput), ("binding", BindingReviewOutput))
    all_findings = []
    all_rejected: list[dict[str, Any]] = []
    all_outputs: list[dict[str, Any]] = []
    all_raw: list[str] = []
    all_meta: list[dict[str, Any]] = []
    failures: list[str] = []
    coverage = True
    for scope, output_model in scopes:
        spec = StructuredReviewCallSpec(
            role_id=f"material_universe_{scope}_review", gate_id=PlanGateId.MATERIAL_UNIVERSE,
            schema_name=output_model.__name__, json_schema=output_model.model_json_schema(),
            artifact_prefix=f"material_universe_{scope}_review", input_payload_hash=canonical_payload_hash({"scope": scope, "pack": evidence_pack}),
        )
        call = run_structured_review_call(
            client=reviewer_client, initial_prompt=_prompt(evidence_pack, scope),
            retry_prompt_builder=lambda raw, error, s=scope: _retry_prompt(evidence_pack, s, error, raw),
            output_model=output_model, call_spec=spec, state=state,
            stage=state.plan_loop_stages.get("plan_gate_material_universe"), policy=policy,
        )
        result.reviewer_calls += call.call_count
        result.schema_retries += call.schema_retry_count
        for attempt in call.attempts:
            all_raw.append(attempt.raw_text)
            all_meta.append(attempt.model_dump(mode="json", exclude={"raw_text"}))
        if not call.ok or call.parsed_output is None:
            failures.append(call.error_code or "material_universe_review.schema_invalid")
            continue
        parsed = output_model.model_validate(call.parsed_output)
        combined, scope_rejected = _as_combined_output(parsed, scope)
        findings, rejected = _normalize(combined, evidence_pack)
        all_findings.extend(findings)
        all_rejected.extend(scope_rejected)
        all_rejected.extend(rejected)
        all_outputs.append({"scope": scope, "output": parsed.model_dump(mode="json")})
        coverage = coverage and parsed.review_status == "complete"
    result.findings = list({item.finding_id: item for item in all_findings}.values())
    result.rejected = all_rejected
    result.outputs = all_outputs
    result.raw_outputs = all_raw
    result.call_metadata = all_meta
    result.coverage_complete = coverage and len(all_outputs) == len(scopes)
    result.failure_code = failures[0] if failures else ("material_universe_review.coverage_incomplete" if not result.coverage_complete else "")
    result.ok = result.coverage_complete and not failures
    result.error = result.failure_code
    return result


__all__ = ["MaterialReviewOutput", "UniverseReviewOutput", "BindingReviewOutput", "run_material_universe_review_split"]
