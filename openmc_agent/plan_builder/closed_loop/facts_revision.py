"""Facts-only RFC6902 proposal validation and clone evaluation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.validation_repair import PatchRepairOperation
from openmc_agent.plan_builder.validators import validate_patch
from openmc_agent.repair_proposal import apply_json_patch_to_clone
from openmc_agent.schemas import AgentBaseModel

from .fingerprints import compute_candidate_hash
from .models import FactsRevisionProposal, PlanReviewFinding


class FactsRevisionEvaluation(AgentBaseModel):
    accepted: bool = False
    candidate: dict[str, Any] | None = None
    candidate_hash: str | None = None
    reasons: list[str] = Field(default_factory=list)


def _pointer_value(value: Any, pointer: str) -> Any:
    """Resolve RFC6901 pointer without treating a nested confirmation as a
    top-level key.  Missing values remain distinct from JSON null."""
    current = value
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current


_MISSING = object()


def _confirmed_records(confirmed_facts: dict[str, Any]) -> list[tuple[str, Any]]:
    """Supports the stable typed record store and the legacy namespaced map."""
    records = confirmed_facts.get("plan_closed_loop_records", []) if isinstance(confirmed_facts, dict) else []
    result: list[tuple[str, Any]] = []
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict) and isinstance(record.get("json_path"), str):
            result.append((record["json_path"], record.get("value")))
    facts = confirmed_facts.get("plan_closed_loop", {}).get("facts", {}) if isinstance(confirmed_facts, dict) else {}
    if isinstance(facts, dict):
        for path, value in facts.items():
            result.append((path if str(path).startswith("/") else "/" + str(path), value))
    return result


def allowed_paths_for_findings(findings: list[PlanReviewFinding]) -> list[str]:
    paths = {"/missing_facts", "/assumptions", "/source_notes"}
    for finding in findings:
        if finding.repairable_by_llm and not finding.requires_human:
            for path in finding.affected_json_paths:
                paths.add(path)
    return sorted(paths)


def normalize_facts_revision(raw: str | dict[str, Any]) -> FactsRevisionProposal:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    proposal = FactsRevisionProposal.model_validate(payload)
    operations = [PatchRepairOperation.model_validate(item) for item in proposal.operations]
    if not operations:
        raise ValueError("facts revision requires operations")
    if any(operation.op not in {"add", "replace", "remove"} for operation in operations):
        raise ValueError("facts revision permits only add, replace, and remove")
    proposal.operations = operations
    return proposal


def evaluate_facts_revision(*, facts_patch: dict[str, Any], proposal: FactsRevisionProposal,
                            findings: list[PlanReviewFinding], confirmed_facts: dict[str, Any],
                            prior_candidate_hashes: list[str]) -> FactsRevisionEvaluation:
    allowed = allowed_paths_for_findings(findings)
    operations = [PatchRepairOperation.model_validate(item) for item in proposal.operations]
    for operation in operations:
        if operation.path == "" or operation.path == "/patch_type" or not any(operation.path == path or operation.path.startswith(path + "/") for path in allowed):
            return FactsRevisionEvaluation(reasons=["facts_revision.path_out_of_scope"])
    try:
        applied = apply_json_patch_to_clone(facts_patch, [operation.model_dump(mode="json", exclude_none=True) for operation in operations])
        if not applied.ok:
            return FactsRevisionEvaluation(reasons=[f"facts_revision.apply_failed: {applied.error}"])
        candidate = applied.plan
    except Exception as exc:
        return FactsRevisionEvaluation(reasons=[f"facts_revision.apply_failed: {exc}"])
    if not isinstance(candidate, dict):
        return FactsRevisionEvaluation(reasons=["facts_revision.root_replacement_forbidden"])
    candidate_hash = compute_candidate_hash(target_patch_type="facts", candidate_patch=candidate)
    if candidate_hash in prior_candidate_hashes:
        return FactsRevisionEvaluation(candidate_hash=candidate_hash, reasons=["facts_revision.duplicate_candidate"])
    try:
        parsed = parse_patch_content("facts", candidate)
        validation = validate_patch(parsed)
    except Exception as exc:
        return FactsRevisionEvaluation(candidate_hash=candidate_hash, reasons=[f"facts_revision.schema_invalid: {exc}"])
    if not validation.ok:
        return FactsRevisionEvaluation(candidate_hash=candidate_hash, reasons=["facts_revision.validator_failed"])
    for path, value in _confirmed_records(confirmed_facts):
        if _pointer_value(candidate, path) != value:
            return FactsRevisionEvaluation(candidate_hash=candidate_hash, reasons=["facts_revision.confirmed_fact_changed"])
    return FactsRevisionEvaluation(accepted=True, candidate=candidate, candidate_hash=candidate_hash)
