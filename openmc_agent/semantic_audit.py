from __future__ import annotations

import json
import re
import time
from enum import Enum
from typing import Any, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class SemanticAuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SemanticAuditMode(str, Enum):
    OFF = "off"
    WARNING_ONLY = "warning_only"
    STRICT_EVALUATION = "strict_evaluation"


class SemanticAuditEvidence(AgentBaseModel):
    source_type: Literal[
        "requirement",
        "retrieval",
        "patch",
        "assembled_plan",
        "validation_report",
        "capability_report",
        "material_report",
        "workflow_metadata",
    ]
    source_id: str | None = None
    path: str | None = None
    excerpt: str | None = None
    summary: str | None = None


class SemanticAuditFinding(AgentBaseModel):
    finding_code: str
    title: str
    severity: SemanticAuditSeverity
    summary: str
    evidence: list[SemanticAuditEvidence] = Field(default_factory=list)
    suggested_patch_target: Literal[
        "facts",
        "materials",
        "universes",
        "pin_map",
        "axial_layers",
        "axial_overlays",
        "settings",
        "capability",
        "renderer",
        "requirement",
        "none",
    ] = "none"
    requires_human_confirmation: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    repair_hint: str | None = None


class SemanticAuditInput(AgentBaseModel):
    audit_id: str
    case_id: str | None = None
    original_requirement: str
    resolved_requirement_summary: str
    referenced_files: list[str] = Field(default_factory=list)
    retrieval_evidence: list[dict[str, Any]] = Field(default_factory=list)
    patch_summaries: dict[str, Any] = Field(default_factory=dict)
    assembled_plan_summary: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    capability_summary: dict[str, Any] = Field(default_factory=dict)
    material_summary: dict[str, Any] = Field(default_factory=dict)
    planning_mode: str | None = None
    reference_patch_policy: str | None = None
    reference_patch_usage: dict[str, Any] = Field(default_factory=dict)
    renderer: str | None = None
    renderability: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticAuditResult(AgentBaseModel):
    audit_id: str
    ok: bool = True
    mode: SemanticAuditMode = SemanticAuditMode.WARNING_ONLY
    findings: list[SemanticAuditFinding] = Field(default_factory=list)
    auditor: str = "deterministic"
    model: str | None = None
    fallback_used: bool = False
    input_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    raw_response_chars: int | None = None
    duration_ms: float | None = None


SEMANTIC_AUDIT_FINDING_CODES = {
    "audit.axial.partial_insert_in_base_lattice",
    "audit.axial.loading_missing_for_finite_insert",
    "audit.axial.base_lattice_loading_conflict",
    "audit.axial.through_path_semantics_conflict",
    "audit.geometry.dimension_mismatch",
    "audit.geometry.spacer_overlay_conflict",
    "audit.geometry.source_bounds_conflict",
    "audit.geometry.boundary_condition_conflict",
    "audit.material.nominal_reported_as_confirmed",
    "audit.material.missing_composition_confirmation",
    "audit.material.policy_report_conflict",
    "audit.material.silent_fact_fill",
    "audit.reference.unexpected_reference_usage",
    "audit.reference.reference_policy_conflict",
    "audit.capability.renderer_claim_conflict",
    "audit.capability.renderability_conflict",
    "audit.fact_gap.unresolved_fact_hidden",
}
UNKNOWN_FINDING_CODE = "audit.unknown_finding_code"
ALL_FINDING_CODES = SEMANTIC_AUDIT_FINDING_CODES | {UNKNOWN_FINDING_CODE}


class SemanticAuditLLMClient(Protocol):
    def audit(
        self,
        audit_input: SemanticAuditInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]: ...


def build_semantic_audit_input(
    *,
    requirement: str,
    resolved_requirement: str | None,
    workflow_state: Mapping[str, Any],
    case_id: str | None = None,
    max_requirement_chars: int = 12000,
    max_evidence_items: int = 20,
    max_excerpt_chars: int = 1200,
) -> SemanticAuditInput:
    plan = _dump(workflow_state.get("simulation_plan"))
    if not plan and isinstance(
        workflow_state.get("assembled_plan"), (dict, AgentBaseModel)
    ):
        plan = _dump(workflow_state.get("assembled_plan"))
    cm = plan.get("complex_model") or {}
    cap = plan.get("capability_report") or _dump(
        workflow_state.get("capability_report")
    )
    req_res = workflow_state.get("requirement_resolution") or {}
    inc = workflow_state.get("incremental_execution_result") or {}
    pbs = workflow_state.get("plan_build_state") or {}
    material_report = (
        workflow_state.get("material_composition_report")
        or workflow_state.get("material_report")
        or {}
    )
    retrieval: list[dict[str, Any]] = []
    for key in ("retrieval_evidence", "rag_evidence", "grep_evidence"):
        vals = workflow_state.get(key) or []
        if isinstance(vals, list):
            remaining = max(0, max_evidence_items - len(retrieval))
            for item in vals[:remaining]:
                retrieval.append(_truncate_obj(item, max_excerpt_chars))
            if len(retrieval) >= max_evidence_items:
                break
    ref_used = (
        inc.get("reference_patches_used") or pbs.get("reference_patches_used") or []
    )
    ref_usage = {
        "policy": workflow_state.get("reference_patch_policy")
        or inc.get("reference_patch_policy")
        or "off",
        "used": bool(ref_used),
        "patch_types": list(ref_used) if isinstance(ref_used, list) else [],
        "source_paths": _reference_source_paths(pbs),
    }
    assembled = _summarize_plan(plan)
    return SemanticAuditInput(
        audit_id=f"audit_{uuid4().hex[:12]}",
        case_id=case_id,
        original_requirement=_clean_text(requirement)[:max_requirement_chars],
        resolved_requirement_summary=_clean_text(
            resolved_requirement or req_res.get("summary") or ""
        )[:max_requirement_chars],
        referenced_files=list(
            req_res.get("referenced_files")
            or workflow_state.get("referenced_files")
            or []
        ),
        retrieval_evidence=retrieval,
        patch_summaries=_summarize_patches(pbs, inc),
        assembled_plan_summary=assembled,
        validation_summary=_summarize_validation(
            _dump(workflow_state.get("validation_report"))
        ),
        capability_summary=_summarize_capability(cap),
        material_summary=_truncate_obj(material_report, max_excerpt_chars),
        planning_mode=_planning_mode(workflow_state),
        reference_patch_policy=ref_usage["policy"],
        reference_patch_usage=ref_usage,
        renderer=assembled.get("renderer") or cap.get("supported_renderer"),
        renderability=assembled.get("renderability") or cap.get("renderability"),
        metadata={
            "artifact_keys": _artifact_keys(workflow_state.get("plan_artifacts")),
            **_safe_metadata(workflow_state.get("metadata") or {}),
        },
    )


def run_deterministic_semantic_audit(
    audit_input: SemanticAuditInput,
) -> SemanticAuditResult:
    findings: list[SemanticAuditFinding] = []
    s = audit_input.assembled_plan_summary
    text = (
        audit_input.original_requirement
        + " "
        + audit_input.resolved_requirement_summary
    ).lower()

    def add(
        code: str,
        title: str,
        summary: str,
        target: str = "none",
        human: bool = False,
        sev: SemanticAuditSeverity = SemanticAuditSeverity.WARNING,
    ):
        findings.append(
            SemanticAuditFinding(
                finding_code=code,
                title=title,
                severity=sev,
                summary=summary,
                evidence=[
                    SemanticAuditEvidence(source_type="assembled_plan", summary=summary)
                ],
                suggested_patch_target=target,
                requires_human_confirmation=human,
                confidence=0.8,
            )
        )

    if _mentions_3d(text) and not s.get("axial_layers") and not s.get("axial_overlays"):
        add(
            "audit.geometry.dimension_mismatch",
            "3D requirement assembled without axial structure",
            "Requirement indicates axial/3D geometry but assembled plan has no axial layers or overlays.",
            "axial_layers",
            sev=SemanticAuditSeverity.ERROR,
        )
    if "spacer" in text and not any(
        (o.get("kind") == "spacer_grid") for o in s.get("axial_overlays", [])
    ):
        add(
            "audit.geometry.spacer_overlay_conflict",
            "Spacer grid missing overlay",
            "Requirement mentions spacer grids but assembled plan has no spacer_grid axial overlay.",
            "axial_overlays",
        )
    if _finite_insert_conflict(s):
        add(
            "audit.axial.partial_insert_in_base_lattice",
            "Finite insert appears in base lattice",
            "Special finite insert roles appear in the base pin map without corresponding lattice loading overrides.",
            "pin_map",
            sev=SemanticAuditSeverity.ERROR,
        )
    if _material_nominal_confirmed(audit_input.material_summary, s):
        add(
            "audit.material.nominal_reported_as_confirmed",
            "Nominal material marked confirmed",
            "Material summary reports nominal/approximate composition as confirmed.",
            "materials",
        )
    if (
        audit_input.reference_patch_policy or "off"
    ) == "off" and audit_input.reference_patch_usage.get("used"):
        add(
            "audit.reference.unexpected_reference_usage",
            "Reference patch used while policy is off",
            "Reference patch usage is present even though policy is off.",
            "none",
        )
    if audit_input.renderability in {"exportable", "runnable"} and _has_blocking_issue(
        audit_input.validation_summary
    ):
        add(
            "audit.capability.renderer_claim_conflict",
            "Renderer claim conflicts with validation",
            "Renderer is exportable/runnable while validation summary contains blocking issues.",
            "capability",
        )
    if _has_hidden_fact_gap(audit_input):
        add(
            "audit.fact_gap.unresolved_fact_hidden",
            "Unresolved fact gap hidden",
            "Requirement or material summary indicates missing facts that require human confirmation.",
            "facts",
            True,
        )
    return SemanticAuditResult(
        audit_id=audit_input.audit_id,
        findings=findings,
        auditor="deterministic",
        fallback_used=True,
        input_summary=_input_summary(audit_input),
    )


class FakeSemanticAuditClient:
    def audit(
        self,
        audit_input: SemanticAuditInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        result = run_deterministic_semantic_audit(audit_input)
        return {
            "audit_id": audit_input.audit_id,
            "ok": True,
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "warnings": [],
        }


class _CallableSemanticAuditClient:
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

    def audit(
        self,
        audit_input: SemanticAuditInput,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]:
        if hasattr(self.llm, "audit"):
            return self.llm.audit(audit_input, prompt=prompt, json_schema=json_schema)
        if callable(self.llm):
            return self.llm(prompt)
        raise ConnectionError("semantic audit llm client is not callable")


def make_semantic_audit_client(
    *,
    llm: Any | None = None,
    model_name: str | None = None,
    temperature: float = 0.0,
    output_mode: Literal["auto", "json_object", "json_schema", "plain_prompt"] = "auto",
) -> SemanticAuditLLMClient:
    if llm is None:
        raise ValueError("llm is required for real semantic audit client")
    return _CallableSemanticAuditClient(llm, model_name, temperature, output_mode)


def run_semantic_plan_audit(
    audit_input: SemanticAuditInput,
    *,
    mode: SemanticAuditMode = SemanticAuditMode.WARNING_ONLY,
    client: SemanticAuditLLMClient | None = None,
    model_name: str | None = None,
    allow_fallback: bool = True,
) -> SemanticAuditResult:
    if mode == SemanticAuditMode.OFF:
        return SemanticAuditResult(
            audit_id=audit_input.audit_id,
            mode=mode,
            auditor="off",
            input_summary=_input_summary(audit_input),
        )
    start = time.perf_counter()
    warnings: list[str] = []
    if client is None:
        res = run_deterministic_semantic_audit(audit_input)
        res.mode = mode
        res.duration_ms = (time.perf_counter() - start) * 1000
        return res
    from openmc_agent.semantic_audit_prompts import build_semantic_audit_prompt

    prompt = build_semantic_audit_prompt(audit_input)
    schema = SemanticAuditResult.model_json_schema()
    raw_len = None
    for attempt in range(2):
        try:
            raw = client.audit(audit_input, prompt=prompt, json_schema=schema)
            raw_len = (
                len(raw)
                if isinstance(raw, str)
                else len(json.dumps(raw, ensure_ascii=False))
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            result = _parse_result(data, audit_input, mode, warnings)
            result.auditor = client.__class__.__name__
            result.model = model_name
            result.raw_response_chars = raw_len
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result
        except Exception as exc:
            warnings.append(f"semantic audit attempt {attempt+1} failed: {exc}")
    if allow_fallback:
        res = run_deterministic_semantic_audit(audit_input)
        res.mode = mode
        res.model = model_name
        res.warnings = warnings
        res.raw_response_chars = raw_len
        res.duration_ms = (time.perf_counter() - start) * 1000
        return res
    return SemanticAuditResult(
        audit_id=audit_input.audit_id,
        ok=True,
        mode=mode,
        auditor="failed",
        model=model_name,
        warnings=warnings,
        raw_response_chars=raw_len,
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _parse_result(
    data: Any,
    audit_input: SemanticAuditInput,
    mode: SemanticAuditMode,
    warnings: list[str],
) -> SemanticAuditResult:
    if not isinstance(data, dict):
        raise ValueError("audit response must be an object")
    findings = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("finding_code")
        if code not in SEMANTIC_AUDIT_FINDING_CODES:
            warnings.append(f"unknown finding_code normalized: {code}")
            item = dict(item)
            item["finding_code"] = UNKNOWN_FINDING_CODE
        ev = item.get("evidence") or []
        if not ev:
            item["evidence"] = [
                {
                    "source_type": "workflow_metadata",
                    "summary": "auditor did not provide evidence; normalized",
                }
            ]
        findings.append(SemanticAuditFinding.model_validate(item))
    return SemanticAuditResult(
        audit_id=str(data.get("audit_id") or audit_input.audit_id),
        ok=bool(data.get("ok", True)),
        mode=mode,
        findings=findings,
        input_summary=_input_summary(audit_input),
        warnings=[*warnings, *list(data.get("warnings") or [])],
    )


def _dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj if isinstance(obj, dict) else {}


def _clean_text(text: str) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text or "",
    )


def _truncate_obj(obj: Any, cap: int) -> Any:
    if isinstance(obj, str):
        return _clean_text(obj)[:cap]
    if isinstance(obj, dict):
        return {
            str(k): _truncate_obj(v, cap)
            for k, v in list(obj.items())[:40]
            if "secret" not in str(k).lower()
            and "token" not in str(k).lower()
            and "api_key" not in str(k).lower()
        }
    if isinstance(obj, list):
        return [_truncate_obj(v, cap) for v in obj[:20]]
    return obj


def _summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    cm = plan.get("complex_model") or {}
    core = cm.get("core") or {}
    lats = cm.get("lattices") or []
    mats = cm.get("materials") or []
    lattices = []
    role_counts = {}
    special = []
    for lat in lats:
        pat = lat.get("universe_pattern") or []
        dims = [len(pat), max([len(r) for r in pat], default=0)]
        counts = {}
        for r, row in enumerate(pat):
            if not isinstance(row, list):
                continue
            for c, u in enumerate(row):
                counts[str(u)] = counts.get(str(u), 0) + 1
                if _is_special_role(str(u)):
                    special.append(
                        {"lattice_id": lat.get("id"), "role": str(u), "coord": [r, c]}
                    )
        lattices.append(
            {
                "id": lat.get("id"),
                "dimensions": dims,
                "role_counts": counts,
                "special_role_count": len(special),
            }
        )
        for k, v in counts.items():
            role_counts[k] = role_counts.get(k, 0) + v
    return {
        "dimension": (
            "3d" if core.get("axial_layers") or core.get("axial_overlays") else "2d"
        ),
        "symmetry": core.get("symmetry"),
        "lattices": lattices,
        "pin_role_counts": role_counts,
        "special_pin_roles": [s["role"] for s in special[:30]],
        "special_coordinates_summary": special[:30],
        "axial_layers": [
            {
                "id": x.get("id"),
                "z_min_cm": x.get("z_min_cm"),
                "z_max_cm": x.get("z_max_cm"),
                "lattice_loading_id": x.get("lattice_loading_id"),
            }
            for x in (core.get("axial_layers") or [])
        ],
        "lattice_loadings": [
            {
                "id": x.get("id"),
                "base_lattice_id": x.get("base_lattice_id"),
                "override_roles": list((x.get("overrides") or {}).keys()),
                "purpose": x.get("purpose"),
            }
            for x in (cm.get("lattice_loadings") or [])
        ],
        "axial_overlays": [
            {
                "id": x.get("id"),
                "kind": x.get("overlay_kind") or x.get("kind"),
                "z_min_cm": x.get("z_min_cm"),
                "z_max_cm": x.get("z_max_cm"),
                "target": x.get("target_universe_id"),
            }
            for x in (core.get("axial_overlays") or [])
        ],
        "materials": [
            {
                "id": m.get("id"),
                "status": m.get("composition_status")
                or m.get("source")
                or m.get("source_note"),
                "requires_human_confirmation": m.get("requires_human_confirmation"),
            }
            for m in mats[:50]
        ],
        "source_bounds": ((cm.get("settings") or {}).get("source") or {}).get("bounds"),
        "geometry_bounds": core.get("boundary_conditions") or core.get("boundary"),
        "boundary_conditions": core.get("boundary_conditions") or core.get("boundary"),
        "renderer": (plan.get("capability_report") or {}).get("supported_renderer"),
        "renderability": (plan.get("capability_report") or {}).get("renderability"),
    }


def _summarize_patches(pbs: Any, inc: Any) -> dict[str, Any]:
    out = {
        "patches": {},
        "reference_patches_used": (
            inc.get("reference_patches_used") if isinstance(inc, dict) else []
        ),
    }
    patches = (pbs or {}).get("patches") if isinstance(pbs, dict) else {}
    if isinstance(patches, dict):
        for pt, info in patches.items():
            d = info if isinstance(info, dict) else _dump(info)
            out["patches"][pt] = {
                "patch_type": pt,
                "status": d.get("status"),
                "source": d.get("source") or d.get("generator"),
                "key_counts": {
                    k: len(v) for k, v in d.items() if isinstance(v, (list, dict))
                },
            }
    return out


def _summarize_validation(v: dict[str, Any]) -> dict[str, Any]:
    issues = v.get("issues") or []
    return {
        "is_valid": v.get("is_valid"),
        "issue_count": len(issues),
        "issue_codes": v.get("issue_codes")
        or [i.get("code") for i in issues if isinstance(i, dict)],
        "blocking_issue_count": sum(
            1
            for i in issues
            if isinstance(i, dict)
            and (
                i.get("severity") in {"error", "critical"}
                or i.get("route_hint") in {"ask_expert", "reflect"}
            )
        ),
    }


def _summarize_capability(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "is_executable": c.get("is_executable"),
        "supported_renderer": c.get("supported_renderer"),
        "renderability": c.get("renderability"),
        "issue_codes": [
            i.get("code") for i in c.get("issues", []) if isinstance(i, dict)
        ],
        "required_human_confirmations": c.get("required_human_confirmations") or [],
    }


def _planning_mode(ws: Mapping[str, Any]) -> str | None:
    inc = ws.get("incremental_execution_result") or {}
    dec = ws.get("planning_mode_decision") or {}
    return (inc.get("planning_mode") if isinstance(inc, dict) else None) or (
        dec.get("mode") if isinstance(dec, dict) else None
    )


def _reference_source_paths(pbs: Any) -> list[str]:
    """Extract compact reference patch source paths from plan-build metadata.

    The executor has evolved through a few metadata shapes, so this accepts
    generic dict/list structures and keeps only path-like strings attached to
    reference-related records. Values are sanitized by the same redaction helper
    used for prompts and capped to avoid leaking large artifact payloads.
    """

    paths: list[str] = []

    def walk(value: Any, *, reference_context: bool = False) -> None:
        if len(paths) >= 20:
            return
        if isinstance(value, dict):
            local_reference = reference_context or any(
                "reference" in str(v).lower()
                for k, v in value.items()
                if k in {"source", "event_type", "generator", "policy", "code"}
            )
            for key, child in value.items():
                key_text = str(key).lower()
                if local_reference and key_text in {
                    "path",
                    "source_path",
                    "source_paths",
                    "reference_path",
                }:
                    for path in _string_values(child):
                        clean = _clean_text(path)[:500]
                        if clean and clean not in paths:
                            paths.append(clean)
                walk(child, reference_context=local_reference)
        elif isinstance(value, list):
            for child in value[:100]:
                walk(child, reference_context=reference_context)

    walk(pbs)
    return paths


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _artifact_keys(a: Any) -> list[str]:
    if isinstance(a, dict):
        return list(a.keys())
    if isinstance(a, list):
        return [str(x).split("/")[-1].split(".")[0] for x in a]
    return []


def _safe_metadata(m: dict[str, Any]) -> dict[str, Any]:
    return _truncate_obj(m, 500) if isinstance(m, dict) else {}


def _input_summary(i: SemanticAuditInput) -> dict[str, Any]:
    return {
        "case_id": i.case_id,
        "renderer": i.renderer,
        "renderability": i.renderability,
        "planning_mode": i.planning_mode,
        "reference_patch_usage": i.reference_patch_usage,
    }


def _is_special_role(u: str) -> bool:
    return any(
        tok in u.lower()
        for tok in (
            "poison",
            "pyrex",
            "plug",
            "insert",
            "thimble",
            "control",
            "burnable",
        )
    )


def _finite_insert_conflict(s: dict[str, Any]) -> bool:
    specials = set(s.get("special_pin_roles") or [])
    loading_roles = {
        r for l in s.get("lattice_loadings", []) for r in l.get("override_roles", [])
    }
    return bool(
        specials and not (specials & loading_roles) and not s.get("axial_layers")
    )


def _mentions_3d(t: str) -> bool:
    return any(
        x in t
        for x in (
            "3d",
            "three-dimensional",
            "axial",
            "z range",
            "height",
            "finite",
            "轴向",
            "三维",
        )
    )


def _material_nominal_confirmed(m: Any, s: dict[str, Any]) -> bool:
    txt = (
        json.dumps(m, ensure_ascii=False).lower()
        + json.dumps(s.get("materials"), ensure_ascii=False).lower()
    )
    return ("nominal" in txt or "approx" in txt) and "confirmed" in txt


def _has_blocking_issue(v: dict[str, Any]) -> bool:
    return bool(
        v.get("blocking_issue_count")
        or any("blocking" in str(c) for c in v.get("issue_codes", []))
    )


def _has_hidden_fact_gap(i: SemanticAuditInput) -> bool:
    txt = (
        i.original_requirement
        + " "
        + i.resolved_requirement_summary
        + " "
        + json.dumps(i.material_summary, ensure_ascii=False)
    ).lower()
    return any(
        x in txt
        for x in (
            "fact gap",
            "missing composition",
            "unknown density",
            "requires human confirmation",
            "缺少",
            "待确认",
        )
    )
