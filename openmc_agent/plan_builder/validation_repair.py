"""Validation-driven, clone-only RFC6902 repair of an incremental patch."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from openmc_agent.repair_policy import is_protected_path, match_json_pointer_pattern
from openmc_agent.repair_proposal import apply_json_patch_to_clone
from openmc_agent.schemas import AgentBaseModel, ValidationReport
from openmc_agent.validator import validate_simulation_plan

from .assembler import assemble_simulation_plan_from_patches
from .patches import PatchParseError, parse_patch_content
from .state import PlanBuildState, PlanPatchEnvelope
from .validators import validate_patch


class PatchRepairOperation(AgentBaseModel):
    op: Literal["test", "add", "replace", "remove"]
    path: str
    value: Any | None = None

    @model_validator(mode="after")
    def _require_value_for_value_operations(self) -> "PatchRepairOperation":
        """Keep RFC6902 value-bearing operations locally well-formed."""
        if self.op in {"add", "replace", "test"} and "value" not in self.model_fields_set:
            raise ValueError(f"RFC6902 {self.op!r} operation requires a value")
        return self


class PatchRepairRequest(AgentBaseModel):
    repair_id: str
    issue_fingerprint: str
    target_patch_type: str
    issues: list[dict[str, Any]]
    previous_patch_content: dict[str, Any]
    previous_patch_hash: str
    relevant_plan_fragment: dict[str, Any]
    valid_upstream_patch_summaries: dict[str, Any]
    allowed_path_patterns: list[str]
    forbidden_path_patterns: list[str]
    prior_candidate_hashes: list[str] = Field(default_factory=list)
    attempt_index: int = 0


class PatchRepairProposal(AgentBaseModel):
    repair_id: str
    target_patch_type: str
    operations: list[PatchRepairOperation]
    rationale: str
    confidence: float


class PatchRepairModelOutput(AgentBaseModel):
    """The compatibility envelope accepted from a repair model.

    The model only owns the RFC6902 operations.  Identity fields remain in
    this shape for backwards compatibility, but are verified against the
    request and never become authoritative.
    """

    operations: list[PatchRepairOperation]
    rationale: str | None = None
    confidence: float | None = None
    repair_id: str | None = None
    target_patch_type: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_advisory_confidence(cls, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("confidence must be a number between 0.0 and 1.0")
        numeric = float(value)
        if not 0.0 <= numeric <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return numeric


class PatchRepairNormalizationResult(AgentBaseModel):
    ok: bool
    proposal: PatchRepairProposal | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    raw_output: dict[str, Any] | None = None
    normalized_output: dict[str, Any] | None = None


class PatchRepairEvaluation(AgentBaseModel):
    accepted: bool
    status: Literal[
        "accepted", "rejected_schema", "rejected_unsafe_path", "rejected_patch_invalid",
        "rejected_no_improvement", "rejected_new_blocker", "rejected_duplicate_candidate",
        "requires_human_confirmation", "failed",
    ]
    issues_before: list[str]
    issues_after: list[str]
    resolved_issue_codes: list[str]
    introduced_issue_codes: list[str]
    candidate_hash: str | None = None
    issue_fingerprint_before: str
    issue_fingerprint_after: str | None = None
    reasons: list[str] = Field(default_factory=list)
    repaired_plan: dict[str, Any] | None = None
    repaired_patch: dict[str, Any] | None = None
    validation_report_after: dict[str, Any] | None = None


class PatchRepairLLMClient(Protocol):
    def propose_patch_repair(
        self, request: PatchRepairRequest, *, prompt: str, json_schema: dict[str, Any]
    ) -> str | dict[str, Any]: ...


GLOBAL_FORBIDDEN_PATH_PATTERNS = [
    "/density*", "/materials/**", "/benchmark*", "/benchmark_constants*", "/nuclear_data*",
    "/cross_sections*", "/environment*", "/api_key*", "/secret*", "/token*",
]


def stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_patch_repair_model_output(
    raw: str | dict[str, Any], *, request: PatchRepairRequest,
) -> PatchRepairNormalizationResult:
    """Bind a model response to its request without weakening JSON Patch safety.

    Missing advisory/system envelope fields are deterministic compatibility
    defaults.  Operations remain the model-owned, strict boundary: this
    function never invents, repairs, or broadens an operation.
    """
    parsed: Any
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            return PatchRepairNormalizationResult(
                ok=False,
                errors=[f"repair response is not valid JSON: {exc}"],
            )
    else:
        parsed = copy.deepcopy(raw)
    if not isinstance(parsed, dict):
        return PatchRepairNormalizationResult(
            ok=False,
            errors=["repair response must be a JSON object"],
        )

    raw_output = copy.deepcopy(parsed)
    try:
        model_output = PatchRepairModelOutput.model_validate(parsed)
    except Exception as exc:
        return PatchRepairNormalizationResult(
            ok=False,
            errors=[f"PatchRepairModelOutput schema validation failed: {exc}"],
            raw_output=raw_output,
        )
    if model_output.repair_id is not None and model_output.repair_id != request.repair_id:
        return PatchRepairNormalizationResult(
            ok=False,
            errors=["model repair_id conflicts with the system repair request"],
            raw_output=raw_output,
        )
    if (
        model_output.target_patch_type is not None
        and model_output.target_patch_type != request.target_patch_type
    ):
        return PatchRepairNormalizationResult(
            ok=False,
            errors=["model target_patch_type conflicts with the system repair request"],
            raw_output=raw_output,
        )

    warnings: list[str] = []
    rationale = model_output.rationale
    if rationale is None:
        rationale = "Model did not provide a rationale."
        warnings.append("repair_proposal.rationale_defaulted")
    confidence = model_output.confidence
    if confidence is None:
        confidence = 0.0
        warnings.append("repair_proposal.confidence_defaulted")
    proposal = PatchRepairProposal(
        repair_id=request.repair_id,
        target_patch_type=request.target_patch_type,
        operations=model_output.operations,
        rationale=rationale,
        confidence=confidence,
    )
    normalized = proposal.model_dump(mode="json")
    return PatchRepairNormalizationResult(
        ok=True,
        proposal=proposal,
        warnings=warnings,
        raw_output=raw_output,
        normalized_output=normalized,
    )


def _normal_path(path: str | None) -> str:
    if not path:
        return ""
    return ".".join(part for part in path.replace("[", ".").replace("]", "").split(".") if part and not part.isdigit())


def compute_validation_issue_fingerprint(
    report: ValidationReport, *, target_patch_type: str,
) -> str:
    stable = sorted(
        {
            (issue.code, _normal_path(issue.schema_path), issue.severity)
            for issue in report.issues
        }
    )
    return stable_json_hash({"target_patch_type": target_patch_type, "issues": stable})


def _fragment_for_path(plan: dict[str, Any], schema_path: str | None) -> dict[str, Any]:
    if not schema_path:
        return {"plan_summary": {"top_level_keys": sorted(plan)[:12]}}
    current: Any = plan
    for part in schema_path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            break
    return {"schema_path": schema_path, "value": current}


def build_patch_repair_request(
    *, state: PlanBuildState, report: ValidationReport, target_patch_type: str,
    allowed_path_patterns: list[str], forbidden_path_patterns: list[str], attempt_index: int = 0,
) -> PatchRepairRequest | None:
    target = next((p for p in state.patches.values() if p.patch_type == target_patch_type and p.status == "valid"), None)
    if target is None:
        return None
    relevant = [i for i in report.issues if i.severity == "error"]
    fingerprint = compute_validation_issue_fingerprint(report, target_patch_type=target_patch_type)
    prior = list(state.validation_repair_candidate_hashes.get(fingerprint, []))
    assembled = state.assembled_plan or {}
    fragment = _fragment_for_path(assembled, relevant[0].schema_path if relevant else None)
    upstream = {
        p.patch_type: {"patch_id": p.patch_id, "sha256": stable_json_hash(p.content), "status": p.status}
        for p in state.patches.values() if p.status == "valid" and p.patch_type != target_patch_type
    }
    return PatchRepairRequest(
        repair_id=f"ppr_{uuid4().hex[:16]}", issue_fingerprint=fingerprint,
        target_patch_type=target_patch_type, issues=[i.model_dump(mode="json") for i in relevant],
        previous_patch_content=copy.deepcopy(target.content), previous_patch_hash=stable_json_hash(target.content),
        relevant_plan_fragment=fragment, valid_upstream_patch_summaries=upstream,
        allowed_path_patterns=allowed_path_patterns,
        forbidden_path_patterns=forbidden_path_patterns, prior_candidate_hashes=prior,
        attempt_index=attempt_index,
    )


def _issue_codes(report: ValidationReport) -> list[str]:
    return sorted({issue.code for issue in report.issues})


def _operation_is_safe(operation: PatchRepairOperation, request: PatchRepairRequest) -> bool:
    if operation.path == "" or is_protected_path(operation.path):
        return False
    if any(match_json_pointer_pattern(operation.path, pattern) for pattern in GLOBAL_FORBIDDEN_PATH_PATTERNS):
        return False
    if any(match_json_pointer_pattern(operation.path, pattern) for pattern in request.forbidden_path_patterns):
        return False
    return any(match_json_pointer_pattern(operation.path, pattern) for pattern in request.allowed_path_patterns)


def _parsed_valid_patches(state: PlanBuildState) -> list[Any]:
    parsed: list[Any] = []
    for envelope in state.patches.values():
        if envelope.status == "valid":
            parsed.append(parse_patch_content(envelope.patch_type, envelope.content))
    return parsed


def evaluate_patch_repair_proposal(
    *, state: PlanBuildState, request: PatchRepairRequest, proposal: PatchRepairProposal,
    requirement: str,
) -> PatchRepairEvaluation:
    before_codes = sorted({str(issue.get("code")) for issue in request.issues if issue.get("code")})
    base = dict(
        accepted=False, issues_before=before_codes, issues_after=before_codes,
        resolved_issue_codes=[], introduced_issue_codes=[],
        issue_fingerprint_before=request.issue_fingerprint,
    )
    if proposal.repair_id != request.repair_id or proposal.target_patch_type != request.target_patch_type:
        return PatchRepairEvaluation(status="rejected_schema", reasons=["proposal target or repair_id mismatch"], **base)
    if not proposal.operations:
        return PatchRepairEvaluation(status="rejected_no_improvement", reasons=["proposal has no operations"], **base)
    if any(not _operation_is_safe(operation, request) for operation in proposal.operations):
        return PatchRepairEvaluation(status="rejected_unsafe_path", reasons=["operation violates patch repair path policy"], **base)
    applied = apply_json_patch_to_clone(request.previous_patch_content, proposal.operations)
    if not applied.ok or not isinstance(applied.plan, dict):
        return PatchRepairEvaluation(status="rejected_patch_invalid", reasons=[applied.error or "atomic JSON patch apply failed"], **base)
    candidate = applied.plan
    candidate_hash = stable_json_hash(candidate)
    if candidate_hash in request.prior_candidate_hashes:
        return PatchRepairEvaluation(status="rejected_duplicate_candidate", candidate_hash=candidate_hash, reasons=["candidate hash was evaluated for this fingerprint"], **base)
    try:
        parsed_candidate = parse_patch_content(request.target_patch_type, candidate)
    except PatchParseError as exc:
        return PatchRepairEvaluation(status="rejected_patch_invalid", candidate_hash=candidate_hash, reasons=[str(exc)], **base)
    patch_result = validate_patch(parsed_candidate)
    if not patch_result.ok:
        return PatchRepairEvaluation(status="rejected_patch_invalid", candidate_hash=candidate_hash, reasons=[i.code for i in patch_result.issues if i.severity == "error"], **base)
    clone = state.model_copy(deep=True)
    target = next((p for p in clone.patches.values() if p.patch_type == request.target_patch_type and p.status == "valid"), None)
    if target is None:
        return PatchRepairEvaluation(status="failed", candidate_hash=candidate_hash, reasons=["valid target patch disappeared"], **base)
    target.content = candidate
    target.source = "repair"
    try:
        assembly = assemble_simulation_plan_from_patches(_parsed_valid_patches(clone), strict=True)
    except Exception as exc:
        return PatchRepairEvaluation(status="rejected_patch_invalid", candidate_hash=candidate_hash, reasons=[f"assembly failed: {exc}"], **base)
    if not assembly.ok or assembly.plan is None:
        return PatchRepairEvaluation(status="rejected_patch_invalid", candidate_hash=candidate_hash, reasons=[i.code for i in assembly.issues if i.severity == "error"], **base)
    after = validate_simulation_plan(assembly.plan, requirement=requirement)
    after_codes = _issue_codes(after)
    after_fingerprint = compute_validation_issue_fingerprint(after, target_patch_type=request.target_patch_type)
    resolved = sorted(set(before_codes) - set(after_codes))
    introduced = sorted(set(after_codes) - set(before_codes))
    blocking = [i.code for i in after.issues if i.severity == "error" and i.code in introduced]
    common = {
        **base,
        "issues_after": after_codes,
        "resolved_issue_codes": resolved,
        "introduced_issue_codes": introduced,
        "candidate_hash": candidate_hash,
        "issue_fingerprint_after": after_fingerprint,
        "repaired_plan": assembly.plan.model_dump(mode="json"),
        "repaired_patch": candidate,
        "validation_report_after": after.model_dump(mode="json"),
    }
    if blocking:
        return PatchRepairEvaluation(status="rejected_new_blocker", reasons=[f"new blocking issues: {blocking}"], **common)
    if not resolved or after_codes == before_codes:
        return PatchRepairEvaluation(status="rejected_no_improvement", reasons=["target issue was not eliminated"], **common)
    return PatchRepairEvaluation(
        status="accepted",
        reasons=["target validation issue resolved without new blocker"],
        **{**common, "accepted": True},
    )


def commit_accepted_patch_repair(state: PlanBuildState, evaluation: PatchRepairEvaluation, request: PatchRepairRequest) -> None:
    if not evaluation.accepted or evaluation.repaired_patch is None or evaluation.repaired_plan is None:
        raise ValueError("only accepted repair evaluations may be committed")
    target = next(p for p in state.patches.values() if p.patch_type == request.target_patch_type and p.status == "valid")
    target.content = copy.deepcopy(evaluation.repaired_patch)
    target.source = "repair"
    target.status = "valid"
    state.patch_status[target.patch_id] = "valid"
    state.assembled_plan = copy.deepcopy(evaluation.repaired_plan)
    state.validation_issues = []
    candidates = state.validation_repair_candidate_hashes.setdefault(request.issue_fingerprint, [])
    if evaluation.candidate_hash and evaluation.candidate_hash not in candidates:
        candidates.append(evaluation.candidate_hash)
    state.validation_repair_history.append({
        "repair_id": request.repair_id,
        "target_patch_type": request.target_patch_type,
        "evaluation": evaluation.model_dump(mode="json"),
    })
