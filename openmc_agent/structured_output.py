"""Bounded, hash-preserving structured-output transactions."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

__all__ = [
    "StructuredOutputAttempt",
    "StructuredOutputRepairPrompt",
    "StructuredOutputResult",
    "canonical_payload_hash",
    "run_structured_output_transaction",
]


class StructuredOutputAttempt(AgentBaseModel):
    """Telemetry for one provider attempt; raw content is never retained."""

    attempt_index: int
    prompt_hash: str
    input_payload_hash: str
    raw_hash: str = ""
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
    budget_charged: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredOutputRepairPrompt(AgentBaseModel):
    """Repair prompt and the immutable payload hash it is allowed to carry."""

    prompt: str
    input_payload_hash: str


class StructuredOutputResult(AgentBaseModel):
    """Result of a bounded structured-output transaction."""

    ok: bool = False
    parsed_output: dict[str, Any] | None = None
    attempts: list[StructuredOutputAttempt] = Field(default_factory=list)
    call_count: int = 0
    schema_retry_count: int = 0
    parse_complete: bool = False
    schema_complete: bool = False
    input_payload_hash: str = ""
    error_code: str = ""
    error_detail: str = ""
    provider_timeout: bool = False
    provider_deadline: str = ""
    billed_call_count: int = 0


def _canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, dict):
        value = {str(key): value[key] for key in value}
    elif isinstance(value, (tuple, list)):
        value = list(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda item: item.model_dump(mode="json")
        if hasattr(item, "model_dump")
        else str(item),
    )


def canonical_payload_hash(payload: Any) -> str:
    """Return a stable SHA-256 hash without retaining the payload."""

    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _raw_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _schema_error_summaries(exc: Exception) -> list[str]:
    """Return schema diagnostics without echoing invalid input values."""

    try:
        details = exc.errors()
    except Exception:
        return [type(exc).__name__]
    if not isinstance(details, list):
        return [type(exc).__name__]
    summaries: list[str] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        loc = ".".join(str(part) for part in detail.get("loc", ()))
        kind = str(detail.get("type", "schema_error"))
        summaries.append(f"{kind}@{loc}" if loc else kind)
    return summaries or [type(exc).__name__]


def _extract_candidates(
    raw: Any,
    *,
    allow_embedded_json: bool,
    allow_top_level_array: bool = False,
) -> tuple[list[Any], str]:
    """Extract JSON candidates without adding domain semantics."""

    if isinstance(raw, dict):
        return [raw], "dict"
    if allow_top_level_array and isinstance(raw, list):
        return [raw], "list"
    if not isinstance(raw, str):
        return [], "none"
    text = raw.strip()
    if not text:
        return [], "none"

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return [value], "json"
        if allow_top_level_array and isinstance(value, list):
            return [value], "json_array"
        return [], "json"
    except (TypeError, ValueError):
        pass

    fence_token = chr(96) * 3
    fence = re.search(
        rf"{fence_token}(?:json)?\s*\n?(.*?)\n?{fence_token}",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence:
        try:
            value = json.loads(fence.group(1).strip())
            if isinstance(value, dict):
                return [value], "markdown_fence"
            if allow_top_level_array and isinstance(value, list):
                return [value], "markdown_fence_array"
        except (TypeError, ValueError):
            pass

    if not allow_embedded_json:
        return [], "none"

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates, "embedded_json" if candidates else "none"


def _metadata(client: Any) -> dict[str, Any]:
    return {
        "requested_output_mode": getattr(
            client, "last_output_mode_requested", "protocol_or_plain_prompt"
        ),
        "actual_output_mode": getattr(
            client, "last_output_mode_used", "protocol_or_plain_prompt"
        ),
        "structured_fallback_used": bool(
            getattr(client, "last_output_fallback_used", False)
        ),
        "fallback_reasons": list(
            getattr(client, "last_output_fallback_reasons", [])
        ),
    }


def run_structured_output_transaction(
    *,
    client: Any,
    initial_prompt: str,
    retry_prompt_builder: Callable[[str, str], str | StructuredOutputRepairPrompt],
    output_model: Any,
    call: Callable[[Any, str], Any],
    payload: Any = None,
    input_payload_hash: str | None = None,
    normalize_candidate: Callable[[Any], Any] | None = None,
    max_attempts: int = 2,
    allow_embedded_json: bool = True,
    allow_top_level_array: bool = False,
    budget_available: Callable[[], bool] | None = None,
    charge_budget: Callable[[], None] | None = None,
    require_budget_accounting: bool = True,
    raw_sink: Callable[[str, int], None] | None = None,
) -> StructuredOutputResult:
    """Run at most two schema-checked calls with a fixed business hash."""

    computed_payload_hash = (
        canonical_payload_hash(payload) if payload is not None else None
    )
    if (
        input_payload_hash
        and computed_payload_hash
        and input_payload_hash != computed_payload_hash
    ):
        return StructuredOutputResult(
            input_payload_hash=input_payload_hash,
            error_code="structured_output.payload_hash_mismatch",
            error_detail="provided input payload hash does not match the immutable payload",
        )

    payload_hash = (
        input_payload_hash
        or computed_payload_hash
        or canonical_payload_hash(initial_prompt)
    )
    result = StructuredOutputResult(input_payload_hash=payload_hash)
    if require_budget_accounting and charge_budget is None:
        result.error_code = "structured_output.unbudgeted_call"
        result.error_detail = "a budget charge callback is required for structured output"
        return result

    prompt = initial_prompt
    last_error = ""
    stale_output_reused = False
    seen_raw_hashes: set[str] = set()

    def _charge_record(record: StructuredOutputAttempt) -> bool:
        if charge_budget is None:
            record.parse_errors.append("unbudgeted_call")
            return False
        try:
            charge_budget()
        except Exception:
            record.parse_errors.append("budget_charge_failed")
            return False
        record.budget_charged = True
        return True

    for attempt_index in range(max(1, min(2, max_attempts))):
        if budget_available is not None and not budget_available():
            result.error_code = "structured_output.budget_exhausted"
            result.error_detail = "structured-output call budget exhausted"
            return result

        raw_text_for_retry = ""
        record = StructuredOutputAttempt(
            attempt_index=attempt_index,
            prompt_hash=canonical_payload_hash({"prompt": prompt}),
            input_payload_hash=payload_hash,
        )

        try:
            raw = call(client, prompt)
            result.call_count += 1
            if not _charge_record(record):
                result.attempts.append(record)
                result.error_code = "structured_output.unbudgeted_call"
                result.error_detail = "structured-output call could not be charged"
                return result
            result.billed_call_count += 1
            try:
                raw_text = (
                    raw
                    if isinstance(raw, str)
                    else json.dumps(raw, ensure_ascii=False, sort_keys=True)
                )
            except Exception as exc:
                raw_text = ""
                record.parse_errors.append(type(exc).__name__)
            raw_text_for_retry = raw_text
            record.raw_chars = len(raw_text)
            if raw_text:
                record.raw_hash = _raw_hash(raw_text)
                record.truncated_suspected = (
                    raw_text.rstrip()[-1:] not in {"}", "]"}
                )
                if raw_sink is not None:
                    try:
                        raw_sink(raw_text, attempt_index)
                    except Exception:
                        pass
            record.metadata.update(_metadata(client))

            if not record.raw_hash:
                last_error = "; ".join(record.parse_errors) or "output_not_json"
            elif record.raw_hash in seen_raw_hashes:
                record.parse_errors.append("stale_output_reused")
                stale_output_reused = True
                last_error = "stale_output_reused"
            else:
                seen_raw_hashes.add(record.raw_hash)
                candidates, strategy = _extract_candidates(
                    raw,
                    allow_embedded_json=allow_embedded_json,
                    allow_top_level_array=allow_top_level_array,
                )
                record.extraction_strategy = strategy
                record.extracted_candidate_count = len(candidates)
                for candidate in reversed(candidates):
                    try:
                        normalized = (
                            normalize_candidate(candidate)
                            if normalize_candidate
                            else candidate
                        )
                        parsed = output_model.model_validate(normalized)
                        record.accepted = True
                        result.parsed_output = parsed.model_dump(mode="json")
                        result.ok = result.parse_complete = result.schema_complete = True
                        result.attempts.append(record)
                        return result
                    except Exception as exc:
                        record.schema_errors.extend(_schema_error_summaries(exc))
                if not candidates:
                    record.parse_errors.append("output_not_json")
                last_error = (
                    "; ".join(record.schema_errors or record.parse_errors)
                    or "schema_invalid"
                )
        except Exception as exc:
            result.call_count += 1
            last_error = type(exc).__name__
            record.parse_errors.append(last_error)
            if not _charge_record(record):
                result.attempts.append(record)
                result.error_code = "structured_output.unbudgeted_call"
                result.error_detail = "structured-output call could not be charged"
                return result
            result.billed_call_count += 1
            if _is_provider_timeout(exc):
                # A provider deadline is an infrastructure interruption, not a
                # schema-repair opportunity.  Never issue a hidden second call.
                result.provider_timeout = True
                result.provider_deadline = str(
                    getattr(exc, "deadline", "")
                    or getattr(exc, "timeout", "")
                    or ""
                )
                record.metadata.update(
                    {
                        "provider_error_type": type(exc).__name__,
                        "provider_timeout": True,
                        "provider_deadline": result.provider_deadline,
                    }
                )
                result.attempts.append(record)
                result.error_code = "provider.timeout"
                result.error_detail = "provider request exceeded its deadline"
                return result

        result.attempts.append(record)
        if attempt_index == 0:
            result.schema_retry_count = 1
            try:
                repair = retry_prompt_builder(raw_text_for_retry, last_error)
            except Exception as exc:
                result.error_code = "structured_output.schema_invalid"
                result.error_detail = type(exc).__name__
                return result
            if isinstance(repair, StructuredOutputRepairPrompt):
                if repair.input_payload_hash != payload_hash:
                    result.error_code = "structured_output.payload_hash_mismatch"
                    result.error_detail = (
                        "repair prompt changed the immutable business payload hash"
                    )
                    return result
                prompt = repair.prompt
            else:
                prompt = repair

    result.error_code = (
        "structured_output.stale_output_reused"
        if stale_output_reused
        else "structured_output.schema_invalid"
    )
    result.error_detail = last_error or "structured output did not validate"
    return result


def _is_provider_timeout(exc: Exception) -> bool:
    """Identify provider deadline failures without importing provider SDKs."""

    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        isinstance(exc, TimeoutError)
        or "timeout" in name
        or "deadline" in name
        or "timed out" in text
        or "deadline exceeded" in text
    )
