"""Provider-tolerant structured review I/O shared by plan gates.

The helpers deliberately only recover complete JSON objects and validate them
against the caller supplied Pydantic model.  They never turn prose into
business data, which keeps a provider's reasoning text outside the ledger.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .models import PlanClosedLoopPolicy, PlanGateId


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


def run_structured_review_call(
    *, client: Any, initial_prompt: str,
    retry_prompt_builder: Callable[[str, str], str], output_model: Any,
    call_spec: StructuredReviewCallSpec, state: Any, stage: Any,
    policy: PlanClosedLoopPolicy,
) -> StructuredReviewResult:
    """Call a critic once plus at most one schema retry.

    ``retry_prompt_builder`` receives the provider text and exact validation
    error, and must include the original evidence pack/schema itself.  The
    kernel cannot accidentally manufacture a lossy retry context.
    """
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
            # Last schema-valid complete object wins: many providers emit a
            # draft JSON object before their final correction.
            for candidate in reversed(candidates):
                try:
                    parsed = output_model.model_validate(candidate)
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
