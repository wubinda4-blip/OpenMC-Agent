"""Tests for monolithic fallback provenance extraction (S4)."""

from __future__ import annotations

from openmc_agent.real_campaign import RealCampaignRunResult, _populate_provenance, validate_real_run_truthfulness


def _make_result(**kw):
    defaults = dict(
        run_id="t1", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0,
        git_sha="", input_sha="", configuration_hash="",
        provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
    )
    defaults.update(kw)
    return RealCampaignRunResult(**defaults)


def test_monolithic_fallback_from_planning_mode():
    result = _make_result()
    ws = {"planning_mode": "monolithic"}
    _populate_provenance(result, ws)
    assert result.monolithic_fallback_used
    assert result.monolithic_plan_source == "direct"


def test_monolithic_fallback_attempted_from_fallback():
    result = _make_result()
    ws = {"fallback_attempted": True, "fallback_source": "incremental_failure"}
    _populate_provenance(result, ws)
    assert result.monolithic_fallback_attempted
    assert result.monolithic_plan_source == "incremental_failure"


def test_no_monolithic_fallback_clean():
    result = _make_result()
    ws = {}
    _populate_provenance(result, ws)
    assert not result.monolithic_fallback_used
    assert not result.monolithic_fallback_attempted


def test_monolithic_fallback_flagged_in_truthfulness():
    result = _make_result(monolithic_fallback_used=True)
    violations = validate_real_run_truthfulness(result, {})
    assert "monolithic_fallback_used" in violations


def test_monolithic_fallback_attempted_also_flagged():
    result = _make_result(monolithic_fallback_attempted=True)
    violations = validate_real_run_truthfulness(result, {})
    assert "monolithic_fallback_attempted" in violations


def test_monolithic_fallback_from_graph_trace():
    result = _make_result()
    ws = {"graph_trace": [
        {"monolithic_fallback_attempted": True, "fallback_reason": "assembly_failed"},
    ]}
    _populate_provenance(result, ws)
    assert result.monolithic_fallback_attempted
