"""Provider-tolerant structured review I/O shared by plan gates.

A single :func:`normalize_llm_review_candidate` handles all known LLM output
deviations (field aliases, enum normalization, extra-field stripping,
single-finding wrapping, confidence label mapping) so individual gate
reviewers do not need ad-hoc normalizers.
"""

from __future__ import annotations

import json
import typing
from typing import Any, Callable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .models import PlanClosedLoopPolicy, PlanGateId


# --------------------------------------------------------------------------- #
# Review call / attempt / result models
# --------------------------------------------------------------------------- #

class StructuredReviewCallSpec(AgentBaseModel):
    role_id: str
    gate_id: PlanGateId
    schema_name: str
    json_schema: dict[str, Any]
    max_attempts: int = 2
    temperature: float = 0
    max_tokens: int | None = None
    allow_embedded_json: bool = True
    artifact_prefix: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredReviewAttempt(AgentBaseModel):
    attempt_index: int
    prompt_hash: str
    raw_text: str = ""
    raw_chars: int = 0
    requested_output_mode: str = "protocol_or_plain_prompt"
    actual_output_mode: str = "protocol_or_plain_prompt"
    structured_fallback_used: bool = False
    fallback_reasons: list[str] = Field(default_factory=list)
    extraction_strategy: str = "none"
    extracted_candidate_count: int = 0
    schema_errors: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    truncated_suspected: bool = False
    accepted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredReviewResult(AgentBaseModel):
    ok: bool = False
    parsed_output: dict[str, Any] | None = None
    attempts: list[StructuredReviewAttempt] = Field(default_factory=list)
    call_count: int = 0
    schema_retry_count: int = 0
    parse_complete: bool = False
    schema_complete: bool = False
    error_code: str = ""
    error_detail: str = ""


# --------------------------------------------------------------------------- #
# Unified LLM output normalizer
# --------------------------------------------------------------------------- #

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "message": ("description", "detail", "reason", "explanation"),
    "code": ("finding_id", "issue_id", "name"),
}

_CONFIDENCE_LABELS: dict[str, float] = {
    "high": 0.9, "medium": 0.6, "moderate": 0.6,
    "low": 0.3, "very_low": 0.1, "unknown": 0.0,
    "certain": 1.0, "confident": 0.9,
}

_VALID_FINDING_CATEGORIES = frozenset({
    "source_coverage", "unsupported_inference", "cross_patch_mismatch",
    "placement_gap", "reachability_gap", "physical_ambiguuity",
    "representation_error", "schema_or_format", "no_progress",
    "budget_exhausted",
})

_REVIEW_STATUS_FIXES: dict[str, str] = {
    "incomplete": "complete", "done": "complete",
    "finished": "complete", "ok": "complete",
}


def _unwrap_ann(ann: Any) -> Any:
    if ann is None:
        return None
    if hasattr(ann, "model_fields"):
        return ann
    args = typing.get_args(ann)
    if args:
        return next((a for a in args if a is not type(None) and hasattr(a, "model_fields")), ann)
    return ann


def _normalize_finding(f: dict[str, Any]) -> dict[str, Any]:
    for canonical, aliases in _FIELD_ALIASES.items():
        if canonical not in f:
            for alias in aliases:
                if alias in f:
                    f[canonical] = f.pop(alias)
                    break
    cat = f.get("category")
    if isinstance(cat, str) and cat.lower() not in _VALID_FINDING_CATEGORIES:
        f["category"] = "cross_patch_mismatch"
    conf = f.get("confidence")
    if isinstance(conf, str):
        label = conf.lower().strip()
        f["confidence"] = _CONFIDENCE_LABELS.get(label, 0.5)
    return f


def _clean_nested(model_cls: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Keep all top-level keys (extra='forbid' catches mismatches).
    Recurse into declared nested-model fields and strip THEIR unknown keys.
    """
    out: dict[str, Any] = {}
    for k, v in data.items():
        fi = model_cls.model_fields.get(k)
        ann = _unwrap_ann(getattr(fi, "annotation", None)) if fi else None
        if fi is not None and isinstance(v, dict) and ann is not None and hasattr(ann, "model_fields"):
            out[k] = _clean_nested_strict(ann, v)
        elif fi is not None and isinstance(v, list):
            raw = getattr(fi, "annotation", None)
            item_type = None
            for a in typing.get_args(raw):
                ua = _unwrap_ann(a)
                if ua is not None and hasattr(ua, "model_fields"):
                    item_type = ua
                    break
            if item_type is not None:
                out[k] = [_clean_nested_strict(item_type, i) if isinstance(i, dict) else i for i in v]
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def _clean_nested_strict(model_cls: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys at this level and recurse into nested models."""
    known = set(model_cls.model_fields.keys())
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k not in known:
            continue
        fi = model_cls.model_fields.get(k)
        ann = _unwrap_ann(getattr(fi, "annotation", None)) if fi else None
        if isinstance(v, dict) and ann is not None and hasattr(ann, "model_fields"):
            out[k] = _clean_nested_strict(ann, v)
        elif isinstance(v, list) and fi is not None:
            raw = getattr(fi, "annotation", None)
            item_type = None
            for a in typing.get_args(raw):
                ua = _unwrap_ann(a)
                if ua is not None and hasattr(ua, "model_fields"):
                    item_type = ua
                    break
            if item_type is not None:
                out[k] = [_clean_nested_strict(item_type, i) if isinstance(i, dict) else i for i in v]
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def normalize_llm_review_candidate(candidate: dict[str, Any], output_model: Any) -> dict[str, Any]:
    """Unified normalizer for all known LLM output deviations."""
    if not isinstance(candidate, dict):
        return candidate
    # 1. Wrap bare finding.
    has_wrapper = "review_status" in candidate or "findings" in candidate
    looks_like_finding = any(k in candidate for k in ("code", "severity", "message", "description", "finding_id"))
    if not has_wrapper and looks_like_finding:
        candidate = {"review_status": "complete", "findings": [candidate]}
    data = dict(candidate)
    # 2-3. Normalize findings.
    findings = data.get("findings")
    if isinstance(findings, list):
        data["findings"] = [_normalize_finding(f) if isinstance(f, dict) else f for f in findings]
    # 3. Review status fix.
    status = data.get("review_status")
    if isinstance(status, str):
        data["review_status"] = _REVIEW_STATUS_FIXES.get(status.lower().strip(), status)
    # 4. Top-level confidence.
    conf = data.get("confidence")
    if isinstance(conf, str):
        data["confidence"] = _CONFIDENCE_LABELS.get(conf.lower().strip(), 0.5)
    # 5-6. Strip unknown at all levels.
    return _clean_nested(output_model, data)


# --------------------------------------------------------------------------- #
# Extraction + call helpers
# --------------------------------------------------------------------------- #

def _metadata(client: Any) -> dict[str, Any]:
    return {
        "requested_output_mode": getattr(client, "last_output_mode_requested", "protocol_or_plain_prompt"),
        "actual_output_mode": getattr(client, "last_output_mode_used", "protocol_or_plain_prompt"),
        "structured_fallback_used": bool(getattr(client, "last_output_fallback_used", False)),
        "fallback_reasons": list(getattr(client, "last_output_fallback_reasons", [])),
    }


def _extract(raw: str | dict[str, Any], *, allow_embedded_json: bool) -> tuple[list[dict[str, Any]], str]:
    if isinstance(raw, dict):
        return [raw], "dict"
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    try:
        value = json.loads(text)
        return ([value] if isinstance(value, dict) else []), "json"
    except json.JSONDecodeError:
        if not allow_embedded_json:
            return [], "none"
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates, "embedded_json" if candidates else "none"


def _call(client: Any, prompt: str, spec: StructuredReviewCallSpec) -> str | dict[str, Any]:
    if hasattr(client, "generate_patch_json"):
        return client.generate_patch_json(
            prompt=prompt, patch_type=spec.role_id, json_schema=spec.json_schema,
            temperature=spec.temperature,
        )
    return client(prompt)


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def run_structured_review_call(
    *, client: Any, initial_prompt: str,
    retry_prompt_builder: Callable[[str, str], str], output_model: Any,
    call_spec: StructuredReviewCallSpec, state: Any, stage: Any,
    policy: PlanClosedLoopPolicy,
) -> StructuredReviewResult:
    from .fingerprints import _digest

    result = StructuredReviewResult()
    prompt = initial_prompt
    last_error = ""
    for attempt_index in range(min(2, call_spec.max_attempts)):
        if state.plan_loop_additional_llm_calls >= policy.max_total_additional_llm_calls:
            result.error_code = "planning.closed_loop.budget_exhausted"
            result.error_detail = "additional LLM call budget exhausted"
            return result
        record = StructuredReviewAttempt(attempt_index=attempt_index, prompt_hash=_digest({"prompt": prompt}))
        try:
            raw = _call(client, prompt, call_spec)
            raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True)
            record.raw_text = raw_text
            record.raw_chars = len(raw_text)
            record.truncated_suspected = bool(raw_text) and raw_text.rstrip()[-1:] not in {"}", "]"}
            record.__dict__.update(_metadata(client))
            state.plan_loop_additional_llm_calls += 1
            result.call_count += 1
            candidates, strategy = _extract(raw, allow_embedded_json=call_spec.allow_embedded_json)
            record.extraction_strategy = strategy
            record.extracted_candidate_count = len(candidates)
            for candidate in reversed(candidates):
                try:
                    normalized = normalize_llm_review_candidate(candidate, output_model)
                    parsed = output_model.model_validate(normalized)
                    record.accepted = True
                    result.parsed_output = parsed.model_dump(mode="json")
                    result.ok = result.parse_complete = result.schema_complete = True
                    result.attempts.append(record)
                    return result
                except Exception as exc:
                    record.schema_errors.append(str(exc))
            if not candidates:
                record.parse_errors.append("output_not_json")
            last_error = "; ".join(record.schema_errors or record.parse_errors) or "schema_invalid"
        except Exception as exc:
            last_error = str(exc)
            record.parse_errors.append(last_error)
        result.attempts.append(record)
        if attempt_index == 0:
            result.schema_retry_count += 1
            prompt = retry_prompt_builder(record.raw_text, last_error)
    result.error_code = "structured_review.schema_invalid"
    result.error_detail = last_error
    return result
