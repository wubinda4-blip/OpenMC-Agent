"""Tests for real LLM evidence strengthening (S2, S3)."""

from __future__ import annotations

from openmc_agent.llm_call_recorder import (
    LLMCallRecorder,
    LLMBudgetExhausted,
    verify_real_llm,
)


def test_recorder_starts_empty():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    assert rec.call_count == 0
    assert rec.evidence_summary()["real_network_call_count"] == 0


def test_recorder_records_successful_call():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=5000, success=True,
        response_chars=1000,
        network_call_verified=True,
    )
    assert rec.call_count == 1
    ev = rec.evidence_summary()
    assert ev["real_network_call_count"] == 1
    assert ev["successful_network_call_count"] == 1


def test_recorder_records_failed_call():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=5000, success=False,
        response_chars=0, error_type="APIError",
    )
    ev = rec.evidence_summary()
    assert ev["failed_network_call_count"] == 1
    assert ev["successful_network_call_count"] == 0


def test_verify_real_llm_no_calls_fails():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    ok, reasons = verify_real_llm(rec, model="deepseek:deepseek-chat", run_started_at="2026-01-01T00:00:00Z")
    assert not ok
    assert "no_planning_network_call" in reasons


def test_verify_real_llm_fake_provider_fails():
    rec = LLMCallRecorder(run_id="t1", model="fake:test", provider="fake")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=5000, success=True,
        response_chars=1000,
    )
    ok, reasons = verify_real_llm(rec, model="fake:test", run_started_at="2026-01-01T00:00:00Z")
    assert not ok
    assert "provider_is_fake" in reasons


def test_verify_real_llm_with_real_calls():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:01Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=4000, success=True,
        response_chars=2000,
        network_call_verified=True,
    )
    ok, reasons = verify_real_llm(rec, model="deepseek:deepseek-chat", run_started_at="2026-01-01T00:00:00Z")
    assert ok, f"Expected True but got reasons: {reasons}"


def test_verify_real_llm_cached_response_fails():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:01Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=4000, success=True,
        response_chars=2000,
        network_call_verified=True,
        cached_response=True,
    )
    ok, reasons = verify_real_llm(rec, model="deepseek:deepseek-chat", run_started_at="2026-01-01T00:00:00Z")
    assert not ok
    assert "cached_response_used" in reasons


def test_verify_real_llm_zero_chars_fails():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    rec.record_call(
        role="planning_patch", task_name="universes",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:01Z",
        completed_at="2026-01-01T00:00:05Z",
        duration_ms=4000, success=True,
        response_chars=0,
        network_call_verified=True,
    )
    ok, reasons = verify_real_llm(rec, model="deepseek:deepseek-chat", run_started_at="2026-01-01T00:00:00Z")
    assert not ok
    assert "no_response_content" in reasons


def test_budget_enforcement_raises():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek", max_calls=2)
    rec.record_call(
        role="planning_patch", task_name="p1",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:01Z",
        completed_at="2026-01-01T00:00:02Z",
        duration_ms=1000, success=True,
        response_chars=500,
    )
    rec.record_call(
        role="planning_patch", task_name="p2",
        client_instance_id="c1",
        started_at="2026-01-01T00:00:02Z",
        completed_at="2026-01-01T00:00:03Z",
        duration_ms=1000, success=True,
        response_chars=500,
    )
    with pytest.raises(LLMBudgetExhausted):
        rec.check_budget()
    assert rec.is_budget_exhausted


def test_recorder_role_counting():
    rec = LLMCallRecorder(run_id="t1", model="deepseek:deepseek-chat", provider="deepseek")
    for role in ["planning_patch", "runtime_diagnostician", "runtime_patch_proposer", "runtime_supervisor"]:
        rec.record_call(
            role=role, task_name=role,
            client_instance_id="c1",
            started_at="2026-01-01T00:00:01Z",
            completed_at="2026-01-01T00:00:02Z",
            duration_ms=1000, success=True,
            response_chars=500,
            network_call_verified=True,
        )
    ev = rec.evidence_summary()
    assert ev["planning_network_call_count"] == 1
    assert ev["runtime_diagnosis_network_call_count"] == 1
    assert ev["runtime_proposal_network_call_count"] == 1
    assert ev["runtime_supervisor_network_call_count"] == 1


def test_investigator_calls_count_as_planning():
    """The investigation substage is part of planning; its calls must
    count toward ``planning_network_call_count`` so a canary that stops
    after Facts investigation is not falsely flagged as
    ``real_llm_not_verified``.
    """
    rec = LLMCallRecorder(run_id="t1", model="ds:deepseek-v4-flash", provider="ds")
    for role in ["plan_investigator", "plan_investigator"]:
        rec.record_call(
            role=role,
            task_name="investigation",
            client_instance_id="plan_investigator",
            started_at="2026-01-01T00:00:01Z",
            completed_at="2026-01-01T00:00:02Z",
            duration_ms=1000,
            success=True,
            response_chars=500,
            network_call_verified=True,
        )
    ev = rec.evidence_summary()
    assert ev["planning_network_call_count"] == 2
    assert ev["real_network_call_count"] == 2


import pytest
