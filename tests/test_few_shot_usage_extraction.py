"""Tests for few-shot provenance extraction (S4)."""

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


def test_few_shot_ids_extracted():
    result = _make_result()
    ws = {"few_shot_examples": [
        {"id": "ex1", "source": "anonymous", "benchmark": ""},
        {"id": "ex2", "source": "anonymous", "benchmark": ""},
    ]}
    _populate_provenance(result, ws)
    assert "ex1" in result.selected_few_shot_ids
    assert "ex2" in result.selected_few_shot_ids
    assert not result.benchmark_specific_few_shot_used


def test_benchmark_specific_few_shot_detected():
    result = _make_result()
    ws = {"few_shot_examples": [
        {"id": "ex1", "source": "VERA3", "benchmark": "VERA3", "is_benchmark_specific": True},
    ]}
    _populate_provenance(result, ws)
    assert result.benchmark_specific_few_shot_used
    assert "VERA3" in result.selected_few_shot_benchmarks


def test_gold_few_shot_detected():
    result = _make_result()
    ws = {"few_shot_examples": [
        {"id": "gold1", "source": "gold", "is_gold": True},
    ]}
    _populate_provenance(result, ws)
    assert result.gold_few_shot_used


def test_benchmark_few_shot_flagged_in_truthfulness():
    result = _make_result(benchmark_specific_few_shot_used=True)
    violations = validate_real_run_truthfulness(result, {})
    assert "benchmark_specific_few_shot_used" in violations


def test_gold_few_shot_flagged_in_truthfulness():
    result = _make_result(gold_few_shot_used=True)
    violations = validate_real_run_truthfulness(result, {})
    assert "gold_few_shot_used" in violations


def test_no_few_shot_clean():
    result = _make_result()
    _populate_provenance(result, {})
    violations = validate_real_run_truthfulness(result, {})
    assert "benchmark_specific_few_shot_used" not in violations
    assert "gold_few_shot_used" not in violations


def test_few_shot_without_source_flagged():
    result = _make_result()
    result.selected_few_shot_ids = ["ex1"]
    result.selected_few_shot_sources = []  # no provenance
    violations = validate_real_run_truthfulness(result, {})
    assert "few_shot_provenance_unverifiable" in violations
