"""Tests for the bounded, hash-preserving structured-output transaction."""

from __future__ import annotations

import json

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import (
    StructuredOutputRepairPrompt,
    canonical_payload_hash,
    run_structured_output_transaction,
)


class _Output(AgentBaseModel):
    value: int


class _Client:
    last_output_mode_requested = "json_schema"
    last_output_mode_used = "json_schema"
    last_output_fallback_used = False
    last_output_fallback_reasons: list[str] = []


def _run(responses, *, payload=None, retry_builder=None, budget=None):
    if budget is None:
        budget = [0, 2]
    client = _Client()
    calls = iter(responses)
    return run_structured_output_transaction(
        client=client,
        initial_prompt="return JSON",
        retry_prompt_builder=retry_builder or (lambda raw, error: "repair: " + error),
        output_model=_Output,
        call=lambda _client, _prompt: next(calls),
        payload=payload or {"target": "value"},
        budget_available=(lambda: budget[0] < budget[1]) if budget is not None else None,
        charge_budget=(lambda: budget.__setitem__(0, budget[0] + 1)) if budget is not None else None,
    )


def test_transaction_accepts_fenced_json_and_records_payload_hash() -> None:
    payload = {"target": "value", "order": [2, 1]}
    result = _run(["```json\n{\"value\": 7}\n```"] , payload=payload)
    assert result.ok
    assert result.parsed_output == {"value": 7}
    assert result.input_payload_hash == canonical_payload_hash(payload)
    assert result.attempts[0].input_payload_hash == result.input_payload_hash
    assert result.attempts[0].raw_hash


def test_transaction_retries_schema_failure_once_and_charges_each_call() -> None:
    budget = [0, 2]
    result = _run(["not json", json.dumps({"value": 3})], budget=budget)
    assert result.ok
    assert result.call_count == 2
    assert result.schema_retry_count == 1
    assert budget[0] == 2
    assert len(result.attempts) == 2


def test_transaction_rejects_repair_payload_hash_drift_without_second_call() -> None:
    calls = [0]
    expected = canonical_payload_hash({"target": "value"})

    def retry(_raw: str, _error: str) -> StructuredOutputRepairPrompt:
        return StructuredOutputRepairPrompt(prompt="repair", input_payload_hash="tampered")

    result = _run(["not json", json.dumps({"value": 3})], retry_builder=retry)
    assert not result.ok
    assert result.error_code == "structured_output.payload_hash_mismatch"
    assert result.call_count == 1
    assert result.input_payload_hash == expected


def test_transaction_rejects_reused_raw_output() -> None:
    result = _run(["not json", "not json"])
    assert not result.ok
    assert result.error_code == "structured_output.stale_output_reused"
    assert result.attempts[-1].parse_errors == ["stale_output_reused"]


def test_transaction_fails_closed_before_unbudgeted_call() -> None:
    budget = [0, 0]
    result = _run([json.dumps({"value": 1})], budget=budget)
    assert not result.ok
    assert result.error_code == "structured_output.budget_exhausted"
    assert result.call_count == 0

def test_transaction_does_not_persist_raw_reasoning() -> None:
    result = _run(
        [json.dumps({"value": 1, "reasoning": "private chain"}), json.dumps({"value": 2})]
    )
    assert result.ok
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "private chain" not in serialized
    assert "raw_text" not in result.attempts[0].model_dump(mode="json")
def test_transaction_rejects_declared_payload_hash_drift() -> None:
    payload = {"target": "value"}
    result = run_structured_output_transaction(
        client=_Client(),
        initial_prompt="return JSON",
        retry_prompt_builder=lambda _raw, _error: "repair",
        output_model=_Output,
        call=lambda _client, _prompt: json.dumps({"value": 1}),
        payload=payload,
        input_payload_hash="tampered",
    )
    assert not result.ok
    assert result.error_code == "structured_output.payload_hash_mismatch"
    assert result.call_count == 0
def test_transaction_fails_closed_without_budget_accounting() -> None:
    result = run_structured_output_transaction(
        client=_Client(),
        initial_prompt="return JSON",
        retry_prompt_builder=lambda _raw, _error: "repair",
        output_model=_Output,
        call=lambda _client, _prompt: json.dumps({"value": 1}),
    )
    assert not result.ok
    assert result.error_code == "structured_output.unbudgeted_call"
    assert result.call_count == 0


def test_transaction_fails_closed_when_budget_charge_raises() -> None:
    result = run_structured_output_transaction(
        client=_Client(),
        initial_prompt="return JSON",
        retry_prompt_builder=lambda _raw, _error: "repair",
        output_model=_Output,
        call=lambda _client, _prompt: json.dumps({"value": 1}),
        charge_budget=lambda: (_ for _ in ()).throw(RuntimeError("budget")),
    )
    assert not result.ok
    assert result.error_code == "structured_output.unbudgeted_call"
    assert result.call_count == 1
    assert result.attempts[0].budget_charged is False


def test_provider_timeout_is_billed_and_does_not_trigger_schema_retry() -> None:
    calls = [0]

    def invoke(_client, _prompt):
        calls[0] += 1
        raise TimeoutError("provider deadline exceeded")

    budget = [0, 2]
    result = run_structured_output_transaction(
        client=_Client(),
        initial_prompt="return JSON",
        retry_prompt_builder=lambda _raw, _error: "repair",
        output_model=_Output,
        call=invoke,
        payload={"target": "value"},
        budget_available=lambda: budget[0] < budget[1],
        charge_budget=lambda: budget.__setitem__(0, budget[0] + 1),
    )
    assert not result.ok
    assert result.error_code == "provider.timeout"
    assert result.provider_timeout
    assert result.call_count == 1
    assert result.billed_call_count == 1
    assert budget[0] == 1
    assert calls[0] == 1
    assert result.attempts[0].input_payload_hash == canonical_payload_hash({"target": "value"})
