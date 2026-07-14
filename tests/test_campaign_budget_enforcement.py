"""Tests for campaign budget enforcement (S8)."""

from __future__ import annotations

import pytest

from openmc_agent.llm_call_recorder import LLMCallRecorder, LLMBudgetExhausted


def test_budget_check_raises_at_limit():
    rec = LLMCallRecorder(run_id="t", model="m", provider="deepseek", max_calls=1)
    rec.record_call(
        role="planning_patch", task_name="p",
        client_instance_id="c",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:01Z",
        duration_ms=1000, success=True,
        response_chars=100,
    )
    with pytest.raises(LLMBudgetExhausted):
        rec.check_budget()


def test_budget_marked_exhausted():
    rec = LLMCallRecorder(run_id="t", model="m", provider="deepseek", max_calls=1)
    rec.record_call(
        role="planning_patch", task_name="p",
        client_instance_id="c",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:01Z",
        duration_ms=1000, success=True,
        response_chars=100,
    )
    try:
        rec.check_budget()
    except LLMBudgetExhausted:
        pass
    assert rec.is_budget_exhausted
    ev = rec.evidence_summary()
    assert ev["budget_exhausted"]


def test_budget_not_exhausted_below_limit():
    rec = LLMCallRecorder(run_id="t", model="m", provider="deepseek", max_calls=5)
    for i in range(3):
        rec.record_call(
            role="planning_patch", task_name=f"p{i}",
            client_instance_id="c",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:01Z",
            duration_ms=1000, success=True,
            response_chars=100,
        )
    rec.check_budget()  # should not raise
    assert not rec.is_budget_exhausted


def test_wrap_callable_enforces_budget():
    rec = LLMCallRecorder(run_id="t", model="m", provider="deepseek", max_calls=1)

    call_count = [0]
    def _inner(input_dict, *, prompt, json_schema):
        call_count[0] += 1
        return "response"

    wrapped = rec.wrap_callable(_inner, role="planning_patch", client_instance_id="c")
    wrapped(None, prompt="test", json_schema={})
    assert call_count[0] == 1
    assert rec.call_count == 1

    with pytest.raises(LLMBudgetExhausted):
        wrapped(None, prompt="test2", json_schema={})


def test_budget_evidence_in_summary():
    rec = LLMCallRecorder(run_id="t", model="m", provider="deepseek", max_calls=3)
    ev = rec.evidence_summary()
    assert ev["max_calls"] == 3
    assert ev["budget_exhausted"] is False
