from __future__ import annotations

import copy
import hashlib
import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence
from uuid import uuid4

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel, SimulationPlan, ValidationReport
from openmc_agent.validator import validate_simulation_plan


class RepairProposalMode(str, Enum):
    OFF = "off"
    PROPOSAL_ONLY = "proposal_only"
    VALIDATE_ONLY = "validate_only"
    APPLY_IF_SAFE = "apply_if_safe"


class RepairRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FORBIDDEN = "forbidden"


class RepairPatchOperation(AgentBaseModel):
    op: Literal["add", "remove", "replace", "test"]
    path: str
    value: Any | None = None
    from_path: str | None = None


class LLMRepairProposal(AgentBaseModel):
    proposal_id: str
    source_issue_codes: list[str] = Field(default_factory=list)
    source_audit_finding_codes: list[str] = Field(default_factory=list)
    rationale: str
    expected_effect: str
    operations: list[RepairPatchOperation]
    suggested_patch_target: str | None = None
    requires_human_confirmation: bool = False
    model: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairOperationEvaluation(AgentBaseModel):
    index: int
    op: str
    path: str
    allowed: bool
    risk_level: RepairRiskLevel
    matched_allowlist_rule: str | None = None
    rejection_codes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RepairProposalResult(AgentBaseModel):
    proposal_id: str
    mode: RepairProposalMode
    status: Literal["proposed", "accepted", "rejected", "unsafe", "failed"]
    proposal: LLMRepairProposal | None = None
    operation_evaluations: list[RepairOperationEvaluation] = Field(default_factory=list)
    issues_before: list[str] = Field(default_factory=list)
    issues_after: list[str] = Field(default_factory=list)
    resolved_issue_codes: list[str] = Field(default_factory=list)
    remaining_issue_codes: list[str] = Field(default_factory=list)
    new_issue_codes: list[str] = Field(default_factory=list)
    schema_valid_before: bool | None = None
    schema_valid_after: bool | None = None
    applied_to_clone: bool = False
    applied_to_workflow_plan: bool = False
    deterministic_repair_attempted: bool = False
    deterministic_repair_succeeded: bool = False
    fallback_used: bool = False
    requires_human_confirmation: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    original_plan_hash: str | None = None
    patched_plan_hash: str | None = None
    duration_ms: float | None = None


class RepairProposalInput(AgentBaseModel):
    repair_id: str
    requirement_summary: str
    plan_summary: dict[str, Any]
    issue_codes: list[str]
    issue_summaries: list[dict[str, Any]]
    audit_findings: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_repair_result: dict[str, Any] | None = None
    allowed_operations: list[str]
    allowed_paths: list[str]
    protected_path_summary: list[str]
    validation_summary_before: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairValidationSnapshot(AgentBaseModel):
    schema_valid: bool
    issue_codes: list[str]
    blocking_issue_codes: list[str]
    warning_issue_codes: list[str]
    report_summary: dict[str, Any] = Field(default_factory=dict)


class RepairProposalLLMClient(Protocol):
    def propose(
        self,
        repair_input: RepairProposalInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]: ...


class PatchApplicationResult(AgentBaseModel):
    ok: bool
    plan: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None


class _CallableRepairProposalClient:
    def __init__(
        self,
        llm: Any,
        model_name: str | None = None,
        temperature: float = 0.0,
        output_mode: str = "auto",
    ) -> None:
        self.llm = llm
        self.model_name = model_name
        self.temperature = temperature
        self.output_mode = output_mode

    def propose(
        self,
        repair_input: RepairProposalInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]:
        if hasattr(self.llm, "propose"):
            return self.llm.propose(repair_input, prompt=prompt, json_schema=json_schema)
        if callable(self.llm):
            return self.llm(prompt)
        raise ConnectionError("repair proposal client is not callable")


def make_repair_proposal_client(
    *,
    llm: Any | None = None,
    model_name: str | None = None,
    temperature: float = 0.0,
    output_mode: Literal["auto", "json_object", "json_schema", "plain_prompt"] = "auto",
) -> RepairProposalLLMClient:
    if llm is None:
        raise ValueError("llm is required for real repair proposal client")
    return _CallableRepairProposalClient(llm, model_name, temperature, output_mode)


class FakeRepairProposalClient:
    def propose(
        self,
        repair_input: RepairProposalInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        issue_codes = list(repair_input.issue_codes)
        operations: list[dict[str, Any]] = []
        requires_human = False
        target = None
        if "audit.material.nominal_reported_as_confirmed" in issue_codes:
            path = _first_allowed(repair_input, "composition_status")
            if path:
                operations.append({"op": "replace", "path": path, "value": "nominal"})
                target = "materials"
        elif any(code.startswith("audit.capability") for code in issue_codes):
            path = _first_allowed(repair_input, "renderability") or _first_allowed(
                repair_input, "capability_notes"
            )
            if path:
                operations.append({"op": "replace", "path": path, "value": "skeleton"})
                target = "capability"
        elif any(code.startswith("audit.reference") for code in issue_codes):
            path = _first_allowed(repair_input, "reference_usage") or _first_allowed(
                repair_input, "reference_policy"
            )
            if path:
                operations.append({"op": "replace", "path": path, "value": {"used": False}})
                target = "metadata"
        elif "audit.fact_gap.unresolved_fact_hidden" in issue_codes:
            requires_human = True
            target = "facts"
        elif "assembly.missing_patch" in issue_codes:
            path = _first_allowed(repair_input, "repair_requests")
            if path:
                operations.append(
                    {
                        "op": "add",
                        "path": path,
                        "value": {"target": "pin_map", "reason": "missing structural patch"},
                    }
                )
                target = "pin_map"
        elif any("axial_overlay" in code or "spacer_grid" in code for code in issue_codes):
            path = _first_allowed(repair_input, "axial_overlays")
            if path:
                operations.append(
                    {
                        "op": "add",
                        "path": path,
                        "value": {"id": "repair_overlay_skeleton", "overlay_kind": "spacer_grid", "geometry_mode": "skeleton"},
                    }
                )
                target = "axial_overlays"
        return {
            "proposal_id": repair_input.repair_id,
            "source_issue_codes": issue_codes,
            "source_audit_finding_codes": list(_audit_codes(repair_input.audit_findings)),
            "rationale": "Deterministic fake repair proposal based on issue allowlist.",
            "expected_effect": "Address the targeted low-risk metadata/structural issue if validation confirms improvement.",
            "operations": operations,
            "suggested_patch_target": target,
            "requires_human_confirmation": requires_human,
            "confidence": 0.8 if operations else 0.6,
        }


def _first_allowed(repair_input: RepairProposalInput, token: str) -> str | None:
    for path in repair_input.allowed_paths:
        if token in path:
            if path.endswith("/**"):
                return path[:-3]
            if "*" in path:
                return path.replace("*", "0", 1)
            return path
    return None


def _audit_codes(findings: Sequence[dict[str, Any]]) -> list[str]:
    return [str(f.get("finding_code")) for f in findings if f.get("finding_code")]


def evaluate_repair_operation(
    operation: RepairPatchOperation,
    *,
    source_issue_codes: Sequence[str],
    source_audit_finding_codes: Sequence[str],
) -> RepairOperationEvaluation:
    from openmc_agent.repair_policy import (
        REPAIR_MISSING_SOURCE_ISSUE,
        REPAIR_OPERATION_NOT_ALLOWED,
        REPAIR_PATH_NOT_ALLOWED_FOR_ISSUE,
        REPAIR_PROTECTED_PATH,
        REPAIR_ROOT_REPLACEMENT_FORBIDDEN,
        REPAIR_UNKNOWN_ISSUE_CODE,
        REPAIR_PATH_RULES,
        is_protected_path,
        is_root_replacement,
        match_json_pointer_pattern,
    )

    codes = list(source_issue_codes or []) + list(source_audit_finding_codes or [])
    rejections: list[str] = []
    notes: list[str] = []
    matched_rule = None
    risk = RepairRiskLevel.LOW
    if not codes:
        rejections.append(REPAIR_MISSING_SOURCE_ISSUE)
    if is_root_replacement(operation.path):
        rejections.append(REPAIR_ROOT_REPLACEMENT_FORBIDDEN)
    if is_protected_path(operation.path):
        rejections.append(REPAIR_PROTECTED_PATH)
        risk = RepairRiskLevel.FORBIDDEN
    known = [REPAIR_PATH_RULES[c] for c in codes if c in REPAIR_PATH_RULES]
    if not known and codes:
        rejections.append(REPAIR_UNKNOWN_ISSUE_CODE)
    op_rule_match = False
    path_rule_match = False
    for rule in known:
        if operation.op in rule.allowed_operations:
            op_rule_match = True
        denied = any(match_json_pointer_pattern(operation.path, p) for p in rule.denied_path_patterns)
        allowed_path = any(match_json_pointer_pattern(operation.path, p) for p in rule.allowed_path_patterns)
        if operation.op in rule.allowed_operations and allowed_path and not denied:
            matched_rule = rule.issue_code
            risk = max_risk(risk, _coerce_risk(rule.risk_level))
            path_rule_match = True
            notes.extend(rule.notes)
            break
    if known and not op_rule_match:
        rejections.append(REPAIR_OPERATION_NOT_ALLOWED)
    if known and op_rule_match and not path_rule_match:
        rejections.append(REPAIR_PATH_NOT_ALLOWED_FOR_ISSUE)
    allowed = not rejections and matched_rule is not None
    return RepairOperationEvaluation(
        index=0,
        op=operation.op,
        path=operation.path,
        allowed=allowed,
        risk_level=risk,
        matched_allowlist_rule=matched_rule,
        rejection_codes=sorted(set(rejections)),
        notes=notes,
    )


def _coerce_risk(value: Any) -> RepairRiskLevel:
    if isinstance(value, RepairRiskLevel):
        return value
    raw = getattr(value, "value", value)
    try:
        return RepairRiskLevel(str(raw))
    except Exception:
        return RepairRiskLevel.LOW


def max_risk(a: RepairRiskLevel, b: RepairRiskLevel) -> RepairRiskLevel:
    order = {RepairRiskLevel.LOW: 0, RepairRiskLevel.MEDIUM: 1, RepairRiskLevel.HIGH: 2, RepairRiskLevel.FORBIDDEN: 3}
    return a if order[a] >= order[b] else b


def apply_repair_patch_to_clone(
    plan: Mapping[str, Any] | AgentBaseModel,
    operations: Sequence[RepairPatchOperation],
) -> PatchApplicationResult:
    from openmc_agent.repair_policy import REPAIR_PATCH_APPLICATION_FAILED, decode_json_pointer

    data = copy.deepcopy(_to_plain_dict(plan))
    try:
        for operation in operations:
            if operation.path == "":
                raise ValueError("root modification is forbidden")
            parts = decode_json_pointer(operation.path)
            parent, key = _resolve_parent(data, parts, create_missing=False)
            if operation.op == "test":
                current = _read_child(parent, key)
                if current != operation.value:
                    raise ValueError(f"test failed at {operation.path}")
            elif operation.op == "remove":
                _remove_child(parent, key)
            elif operation.op == "replace":
                _replace_child(parent, key, operation.value)
            elif operation.op == "add":
                if not parts:
                    raise ValueError("root add is forbidden")
                parent, key = _resolve_parent(data, parts, create_missing=False)
                _add_child(parent, key, operation.value)
            else:  # pragma: no cover; pydantic blocks this
                raise ValueError(f"unsupported op {operation.op}")
    except Exception as exc:
        return PatchApplicationResult(ok=False, error_code=REPAIR_PATCH_APPLICATION_FAILED, error=str(exc))
    return PatchApplicationResult(ok=True, plan=data)


def _resolve_parent(data: Any, parts: list[str], *, create_missing: bool) -> tuple[Any, str]:
    if not parts:
        raise ValueError("root modification is forbidden")
    current = data
    for part in parts[:-1]:
        if isinstance(current, list):
            idx = _array_index(part, current, allow_append=False)
            current = current[idx]
        elif isinstance(current, dict):
            if part not in current:
                if create_missing:
                    current[part] = {}
                else:
                    raise KeyError(part)
            current = current[part]
        else:
            raise TypeError("cannot traverse non-container")
    return current, parts[-1]


def _array_index(part: str, array: list[Any], *, allow_append: bool) -> int:
    if part == "-":
        if allow_append:
            return len(array)
        raise IndexError("'-' is only valid for add")
    if not part.isdigit():
        raise IndexError("array index must be a non-negative integer")
    idx = int(part)
    if idx < 0 or idx >= len(array):
        raise IndexError(idx)
    return idx


def _read_child(parent: Any, key: str) -> Any:
    if isinstance(parent, list):
        return parent[_array_index(key, parent, allow_append=False)]
    if isinstance(parent, dict):
        if key not in parent:
            raise KeyError(key)
        return parent[key]
    raise TypeError("parent is not a container")


def _replace_child(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, list):
        parent[_array_index(key, parent, allow_append=False)] = value
    elif isinstance(parent, dict):
        if key not in parent:
            raise KeyError(key)
        parent[key] = value
    else:
        raise TypeError("parent is not a container")


def _remove_child(parent: Any, key: str) -> None:
    if isinstance(parent, list):
        del parent[_array_index(key, parent, allow_append=False)]
    elif isinstance(parent, dict):
        if key not in parent:
            raise KeyError(key)
        del parent[key]
    else:
        raise TypeError("parent is not a container")


def _add_child(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, list):
        idx = _array_index(key, parent, allow_append=True)
        parent.insert(idx, value)
    elif isinstance(parent, dict):
        parent[key] = value
    else:
        raise TypeError("parent is not a container")


def validate_plan_for_repair(
    plan: Mapping[str, Any] | AgentBaseModel,
    *,
    context: Mapping[str, Any] | None = None,
) -> RepairValidationSnapshot:
    context = context or {}
    payload = _to_plain_dict(plan)
    extra_issues = [str(c) for c in context.get("extra_issue_codes", [])]
    try:
        sim_plan = SimulationPlan.model_validate(payload)
        report = validate_simulation_plan(sim_plan, requirement=str(context.get("requirement", "")))
        issue_codes = _issue_codes(report)
        blocking = _blocking_issue_codes(report)
        warnings = [issue.code for issue in report.issues if issue.severity == "warning"]
        schema_valid = True
    except Exception as exc:
        issue_codes = ["repair.plan_schema_invalid_after"]
        blocking = ["repair.plan_schema_invalid_after"]
        warnings = []
        schema_valid = False
        report = ValidationReport(is_valid=False, errors=[str(exc)])
    issue_codes = _unique([*issue_codes, *extra_issues])
    blocking = _unique([*blocking, *[c for c in extra_issues if "blocking" in c]])
    return RepairValidationSnapshot(
        schema_valid=schema_valid,
        issue_codes=issue_codes,
        blocking_issue_codes=blocking,
        warning_issue_codes=warnings,
        report_summary={"is_valid": getattr(report, "is_valid", False), "issue_codes": issue_codes},
    )


def decide_repair_acceptance(
    *,
    proposal: LLMRepairProposal,
    operation_evaluations: Sequence[RepairOperationEvaluation],
    before: RepairValidationSnapshot,
    after: RepairValidationSnapshot | None,
    mode: RepairProposalMode,
) -> tuple[str, list[str]]:
    from openmc_agent.repair_policy import (
        REPAIR_NEW_BLOCKING_ISSUE,
        REPAIR_REQUIRES_HUMAN_CONFIRMATION,
        REPAIR_TARGET_ISSUE_NOT_IMPROVED,
    )

    reasons: list[str] = []
    if mode == RepairProposalMode.OFF:
        return "failed", ["repair.mode_off"]
    if mode == RepairProposalMode.PROPOSAL_ONLY:
        return "proposed", []
    if not proposal.operations:
        reasons.append(REPAIR_TARGET_ISSUE_NOT_IMPROVED)
    if proposal.requires_human_confirmation:
        reasons.append(REPAIR_REQUIRES_HUMAN_CONFIRMATION)
    if any(not ev.allowed for ev in operation_evaluations):
        return "unsafe", _unique([r for ev in operation_evaluations for r in ev.rejection_codes])
    if any(ev.risk_level in {RepairRiskLevel.HIGH, RepairRiskLevel.FORBIDDEN} for ev in operation_evaluations):
        return "unsafe", ["repair.high_or_forbidden_risk"]
    if after is None or not after.schema_valid:
        reasons.append("repair.plan_schema_invalid_after")
    target = set(proposal.source_issue_codes) | set(proposal.source_audit_finding_codes)
    before_target = target & set(before.issue_codes)
    after_target = target & set(after.issue_codes if after else [])
    if before_target and len(after_target) >= len(before_target):
        reasons.append(REPAIR_TARGET_ISSUE_NOT_IMPROVED)
    new_blocking = set(after.blocking_issue_codes if after else []) - set(before.blocking_issue_codes)
    if new_blocking or (after and len(after.blocking_issue_codes) > len(before.blocking_issue_codes)):
        reasons.append(REPAIR_NEW_BLOCKING_ISSUE)
    if reasons:
        return "rejected", _unique(reasons)
    return "accepted", []


def run_repair_proposal_flow(
    *,
    plan: Mapping[str, Any] | AgentBaseModel,
    validation_result: Any,
    audit_result: Any | None = None,
    mode: RepairProposalMode = RepairProposalMode.PROPOSAL_ONLY,
    client: RepairProposalLLMClient | None = None,
    model_name: str | None = None,
    allow_fallback: bool = True,
    context: Mapping[str, Any] | None = None,
) -> RepairProposalResult:
    from openmc_agent.repair_policy import REPAIR_LLM_FALLBACK_USED, rules_for_issue_codes
    from openmc_agent.repair_prompts import build_repair_proposal_prompt

    started = time.perf_counter()
    context = dict(context or {})
    proposal_id = f"repair_{uuid4().hex[:12]}"
    if mode == RepairProposalMode.OFF:
        return RepairProposalResult(proposal_id=proposal_id, mode=mode, status="proposed")
    original = _to_plain_dict(plan)
    before = validate_plan_for_repair(original, context=context)
    validation_codes = _codes_from_validation(validation_result)
    audit_findings = _audit_findings(audit_result)
    audit_codes = _audit_codes(audit_findings)
    source_codes = _unique([*validation_codes, *audit_codes])
    deterministic_attempted = bool(context.get("deterministic_repair_attempted"))
    deterministic_succeeded = bool(context.get("deterministic_repair_succeeded"))
    if deterministic_succeeded:
        return RepairProposalResult(
            proposal_id=proposal_id,
            mode=mode,
            status="accepted",
            issues_before=before.issue_codes,
            schema_valid_before=before.schema_valid,
            deterministic_repair_attempted=deterministic_attempted,
            deterministic_repair_succeeded=True,
            original_plan_hash=_plan_hash(original),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    rules = rules_for_issue_codes(source_codes)
    repair_input = RepairProposalInput(
        repair_id=proposal_id,
        requirement_summary=_truncate(str(context.get("requirement", "")), 1200),
        plan_summary=_compact_plan_summary(original),
        issue_codes=source_codes,
        issue_summaries=_issue_summaries(validation_result),
        audit_findings=audit_findings,
        deterministic_repair_result=context.get("deterministic_repair_result"),
        allowed_operations=sorted({op for rule in rules for op in rule.allowed_operations}),
        allowed_paths=[path for rule in rules for path in rule.allowed_path_patterns],
        protected_path_summary=_protected_summary(),
        validation_summary_before=before.model_dump(mode="json"),
        metadata=_safe_metadata(dict(context.get("metadata") or {})),
    )
    warnings: list[str] = []
    fallback_used = False
    raw_response_chars: int | None = None
    if client is None:
        client = FakeRepairProposalClient()
        fallback_used = True
    prompt = build_repair_proposal_prompt(repair_input)
    proposal: LLMRepairProposal | None = None
    for attempt in range(2):
        try:
            raw = client.propose(repair_input, prompt=prompt, json_schema=LLMRepairProposal.model_json_schema())
            raw_response_chars = len(raw) if isinstance(raw, str) else len(json.dumps(raw, ensure_ascii=False))
            data = json.loads(raw) if isinstance(raw, str) else raw
            proposal = LLMRepairProposal.model_validate(data)
            if model_name and proposal.model is None:
                proposal.model = model_name
            break
        except Exception as exc:
            warnings.append(f"repair proposal attempt {attempt + 1} failed: {exc}")
    if proposal is None:
        if allow_fallback:
            proposal = build_deterministic_repair_suggestion(repair_input)
            fallback_used = True
            warnings.append(REPAIR_LLM_FALLBACK_USED)
        else:
            return RepairProposalResult(
                proposal_id=proposal_id,
                mode=mode,
                status="failed",
                issues_before=before.issue_codes,
                schema_valid_before=before.schema_valid,
                fallback_used=False,
                warnings=warnings,
                original_plan_hash=_plan_hash(original),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
    evals = []
    for index, operation in enumerate(proposal.operations):
        ev = evaluate_repair_operation(
            operation,
            source_issue_codes=proposal.source_issue_codes,
            source_audit_finding_codes=proposal.source_audit_finding_codes,
        )
        ev.index = index
        evals.append(ev)
    if any(not ev.allowed for ev in evals):
        status = "unsafe"
        reasons = _unique([r for ev in evals for r in ev.rejection_codes])
        after = None
        patched_hash = None
        applied_to_clone = False
    else:
        patch_result = apply_repair_patch_to_clone(original, proposal.operations)
        applied_to_clone = bool(patch_result.ok and proposal.operations and mode != RepairProposalMode.PROPOSAL_ONLY)
        if patch_result.ok and patch_result.plan is not None and mode != RepairProposalMode.PROPOSAL_ONLY:
            after_context = {**context, "extra_issue_codes": context.get("after_extra_issue_codes", [])}
            after = validate_plan_for_repair(patch_result.plan, context=after_context)
            status, reasons = decide_repair_acceptance(
                proposal=proposal, operation_evaluations=evals, before=before, after=after, mode=mode
            )
            patched_hash = _plan_hash(patch_result.plan)
        elif patch_result.ok and mode == RepairProposalMode.PROPOSAL_ONLY:
            after = None
            status, reasons = decide_repair_acceptance(
                proposal=proposal, operation_evaluations=evals, before=before, after=None, mode=mode
            )
            patched_hash = None
        else:
            after = None
            status = "rejected"
            reasons = [patch_result.error_code or "repair.patch_application_failed"]
            patched_hash = None
    issues_after = after.issue_codes if after else []
    resolved = sorted(set(before.issue_codes) - set(issues_after)) if after else []
    remaining = sorted(set(before.issue_codes) & set(issues_after)) if after else before.issue_codes
    new = sorted(set(issues_after) - set(before.issue_codes)) if after else []
    applied_to_workflow = status == "accepted" and mode == RepairProposalMode.APPLY_IF_SAFE
    result = RepairProposalResult(
        proposal_id=proposal.proposal_id,
        mode=mode,
        status=status,  # type: ignore[arg-type]
        proposal=proposal,
        operation_evaluations=evals,
        issues_before=before.issue_codes,
        issues_after=issues_after,
        resolved_issue_codes=resolved,
        remaining_issue_codes=remaining,
        new_issue_codes=new,
        schema_valid_before=before.schema_valid,
        schema_valid_after=after.schema_valid if after else None,
        applied_to_clone=applied_to_clone,
        applied_to_workflow_plan=applied_to_workflow,
        deterministic_repair_attempted=deterministic_attempted,
        deterministic_repair_succeeded=deterministic_succeeded,
        fallback_used=fallback_used,
        requires_human_confirmation=proposal.requires_human_confirmation,
        rejection_reasons=reasons,
        warnings=warnings + ([f"raw_response_chars={raw_response_chars}"] if raw_response_chars is not None else []),
        original_plan_hash=_plan_hash(original),
        patched_plan_hash=patched_hash,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    outdir = context.get("repair_artifact_dir")
    if outdir:
        write_repair_proposal_artifacts(Path(str(outdir)), repair_input, proposal, evals, before, after, result, prompt=prompt)
    return result


def build_deterministic_repair_suggestion(repair_input: RepairProposalInput) -> LLMRepairProposal:
    requires_human = any("fact_gap" in code for code in repair_input.issue_codes)
    return LLMRepairProposal(
        proposal_id=repair_input.repair_id,
        source_issue_codes=list(repair_input.issue_codes),
        source_audit_finding_codes=list(_audit_codes(repair_input.audit_findings)),
        rationale="No safe deterministic low-risk patch can be proposed without additional context.",
        expected_effect="Record a no-op repair suggestion; leave plan unchanged.",
        operations=[],
        requires_human_confirmation=requires_human,
        confidence=0.0,
    )


def write_repair_proposal_artifacts(
    root: Path,
    repair_input: RepairProposalInput,
    proposal: LLMRepairProposal,
    evaluations: Sequence[RepairOperationEvaluation],
    before: RepairValidationSnapshot,
    after: RepairValidationSnapshot | None,
    result: RepairProposalResult,
    *,
    prompt: str | None = None,
    raw_response: str | None = None,
) -> list[str]:
    path = root / proposal.proposal_id
    path.mkdir(parents=True, exist_ok=True)
    artifacts: list[str] = []
    def write(name: str, payload: Any) -> None:
        target = path / name
        if isinstance(payload, str):
            target.write_text(_redact(payload), encoding="utf-8")
        else:
            target.write_text(json.dumps(_redact_obj(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(str(target))
    write("input.json", repair_input.model_dump(mode="json"))
    write("proposal.json", proposal.model_dump(mode="json"))
    write("operation_evaluations.json", [ev.model_dump(mode="json") for ev in evaluations])
    write("validation_before.json", before.model_dump(mode="json"))
    write("validation_after.json", after.model_dump(mode="json") if after else {})
    write("result.json", result.model_dump(mode="json"))
    if prompt is not None:
        write("prompt.txt", prompt)
    if raw_response is not None:
        write("raw_response.txt", raw_response)
    return artifacts


def _to_plain_dict(plan: Mapping[str, Any] | AgentBaseModel) -> dict[str, Any]:
    if hasattr(plan, "model_dump"):
        return plan.model_dump(mode="json")
    return copy.deepcopy(dict(plan))


def _issue_codes(report: ValidationReport) -> list[str]:
    return [issue.code for issue in report.issues]


def _blocking_issue_codes(report: ValidationReport) -> list[str]:
    return [issue.code for issue in report.issues if issue.severity == "error"]


def _codes_from_validation(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "issues"):
        return [issue.code for issue in value.issues]
    if isinstance(value, dict):
        if isinstance(value.get("issue_codes"), list):
            return [str(c) for c in value["issue_codes"]]
        issues = value.get("issues") or []
        return [str(i.get("code")) for i in issues if isinstance(i, dict) and i.get("code")]
    return []


def _issue_summaries(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "issues"):
        return [issue.model_dump(mode="json") for issue in value.issues]
    if isinstance(value, dict):
        return [i for i in value.get("issues", []) if isinstance(i, dict)]
    return []


def _audit_findings(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "findings"):
        return [f.model_dump(mode="json") for f in value.findings]
    if isinstance(value, dict):
        return [f for f in value.get("findings", []) if isinstance(f, dict)]
    if isinstance(value, list):
        return [f for f in value if isinstance(f, dict)]
    return []


def _compact_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    cm = plan.get("complex_model") or {}
    core = cm.get("core") or {}
    materials = cm.get("materials") or plan.get("materials") or []
    return {
        "has_complex_model": bool(cm),
        "material_count": len(materials),
        "materials": [
            {
                "id": m.get("id"),
                "composition_status": m.get("composition_status"),
                "source": m.get("source"),
                "source_note": m.get("source_note"),
            }
            for m in materials[:20]
            if isinstance(m, dict)
        ],
        "axial_overlay_count": len(core.get("axial_overlays") or []),
        "axial_layer_count": len(core.get("axial_layers") or []),
        "capability": plan.get("capability_report") or plan.get("capability") or {},
    }


def _protected_summary() -> list[str]:
    from openmc_agent.repair_policy import PROTECTED_PATH_PATTERNS

    return list(PROTECTED_PATH_PATTERNS[:30])


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return _redact_obj(metadata) if isinstance(metadata, dict) else {}


def _redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {k: _redact_obj(v) for k, v in value.items() if not _secret_key(str(k))}
    if isinstance(value, list):
        return [_redact_obj(v) for v in value[:100]]
    return value


def _redact(text: str) -> str:
    return text.replace("ghp_", "<redacted_ghp_prefix>")


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in ("password", "secret", "token", "api_key"))


def _truncate(text: str, limit: int) -> str:
    return text[:limit]


def _unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
