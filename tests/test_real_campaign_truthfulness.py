"""Real campaign executor truthfulness tests.

These tests verify that the campaign correctly gates on truthfulness
requirements without making any real LLM/OpenMC calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openmc_agent.runtime_metrics import (
    aggregate_real_campaign,
    real_campaign_status,
)
from openmc_agent.real_campaign import (
    RealCampaignRunConfig,
    RealCampaignRunResult,
    classify_real_campaign_run,
    validate_real_run_truthfulness,
)


# -- Promotion gate tests --


def test_zero_llm_calls_cannot_be_pilot_passed():
    m = aggregate_real_campaign([], requested_runs=3)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_PILOT_PENDING"


def test_mocked_openmc_cakes_cannot_be_pilot_passed():
    """Even with 3 'successes', if artifacts are incomplete it's not PASSED."""
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": False}
    ] * 3
    m = aggregate_real_campaign(runs, requested_runs=3)
    assert real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True) == "VERA3B_REAL_LLM_PILOT_FAILED"


def test_pilot_passed_requires_all_3_success():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": True, "runtime_iterations": 0}
    ] * 3
    m = aggregate_real_campaign(runs, requested_runs=3)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert status == "VERA3B_REAL_LLM_PILOT_PASSED"


def test_pilot_failed_with_unsafe_acceptance():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 1, "unsafe_accepted_count": 1, "artifact_complete": True, "runtime_iterations": 0}
    ] * 3
    m = aggregate_real_campaign(runs, requested_runs=3)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert status == "VERA3B_REAL_LLM_PILOT_FAILED"


def test_pilot_pending_with_fewer_than_3_runs():
    runs = [
        {"final_disposition": "FIRST_PASS_SUCCESS", "unsafe_proposal_count": 0, "artifact_complete": True, "runtime_iterations": 0}
    ] * 2
    m = aggregate_real_campaign(runs, requested_runs=3)
    status = real_campaign_status(m, real_environment_available=True, executor_implemented=True, confirmed=True)
    assert status == "VERA3B_REAL_LLM_PILOT_PENDING"


# -- Truthfulness validation --


def test_fake_client_flagged():
    result = RealCampaignRunResult(
        run_id="test", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0, git_sha="", input_sha="",
        configuration_hash="", provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
        fake_client_used=True,
    )
    violations = validate_real_run_truthfulness(result, {})
    assert "fake_client_used" in violations


def test_reference_patch_flagged():
    result = RealCampaignRunResult(
        run_id="test", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0, git_sha="", input_sha="",
        configuration_hash="", provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
        reference_patches_used=["ref_001"],
    )
    violations = validate_real_run_truthfulness(result, {})
    assert "reference_patches_used" in violations


def test_monolithic_fallback_flagged():
    result = RealCampaignRunResult(
        run_id="test", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0, git_sha="", input_sha="",
        configuration_hash="", provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
        monolithic_fallback_used=True,
    )
    violations = validate_real_run_truthfulness(result, {})
    assert "monolithic_fallback_used" in violations


def test_clean_run_no_violations():
    result = RealCampaignRunResult(
        run_id="test", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0, git_sha="", input_sha="",
        configuration_hash="", provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
    )
    violations = validate_real_run_truthfulness(result, {})
    assert violations == []


# -- Run classification --


def _make_result(**kw) -> RealCampaignRunResult:
    defaults = dict(
        run_id="test", status="completed", final_disposition="",
        started_at="", completed_at="", duration_s=0, git_sha="", input_sha="",
        configuration_hash="", provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
    )
    defaults.update(kw)
    return RealCampaignRunResult(**defaults)


def test_classify_first_pass_success():
    result = _make_result(simulation_plan_present=True, smoke_passed=True, xml_exported=True,
                          geometry_debug_passed=True, real_openmc_verified=True)
    ws = {"error": "", "validation_report": {"is_valid": True},
          "runtime_committed_repair_count": 0, "runtime_iteration_count": 0}
    assert classify_real_campaign_run(result, ws) == "FIRST_PASS_SUCCESS"


def test_classify_recovered_success():
    result = _make_result(simulation_plan_present=True, smoke_passed=True, xml_exported=True,
                          geometry_debug_passed=True, real_openmc_verified=True)
    ws = {"error": "", "validation_report": {"is_valid": True},
          "runtime_committed_repair_count": 1, "runtime_iteration_count": 1}
    assert classify_real_campaign_run(result, ws) == "RECOVERED_SUCCESS"


def test_classify_planning_failure():
    result = _make_result(simulation_plan_present=False)
    ws = {"error": "", "validation_report": {"is_valid": False}}
    assert classify_real_campaign_run(result, ws) == "PLANNING_FAILURE"


def test_classify_smoke_failure():
    result = _make_result(simulation_plan_present=True, xml_exported=True,
                          geometry_debug_passed=True, smoke_passed=False)
    ws = {"error": "smoke failed", "validation_report": {"is_valid": False}}
    assert classify_real_campaign_run(result, ws) == "OPENMC_SMOKE_FAILURE"


def test_classify_infrastructure_failure():
    result = _make_result(status="infrastructure_failure")
    ws = {}
    assert classify_real_campaign_run(result, ws) == "CAMPAIGN_INFRASTRUCTURE_FAILURE"


# -- Campaign environment gate --


def test_campaign_without_key_not_run_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from openmc_agent.real_campaign import run_real_campaign
    manifest = run_real_campaign(tmp_path, profile="pilot", runs=3, confirm_real_campaign=True)
    assert manifest["aggregate_status"] == "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"
    assert manifest["completed_runs"] == 0


def test_campaign_with_key_but_no_confirm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from openmc_agent.real_campaign import run_real_campaign
    manifest = run_real_campaign(tmp_path, profile="pilot", runs=3, confirm_real_campaign=False)
    assert manifest["aggregate_status"] == "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"
    assert manifest["completed_runs"] == 0


def test_campaign_manifest_has_input_sha(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from openmc_agent.real_campaign import run_real_campaign
    manifest = run_real_campaign(tmp_path, profile="pilot", runs=3, confirm_real_campaign=False)
    assert manifest["input_sha"]
    assert len(manifest["input_sha"]) == 64  # SHA-256 hex


def test_campaign_config_hash_stable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from openmc_agent.real_campaign import run_real_campaign, _config_hash, RealCampaignRunConfig
    config = RealCampaignRunConfig(
        run_id="", run_index=0, input_path="test",
        model="deepseek:deepseek-chat", temperature=0.0,
        runtime_supervisor_mode="deterministic",
    )
    h1 = _config_hash(config)
    h2 = _config_hash(config)
    assert h1 == h2


def test_campaign_no_api_key_in_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key-12345")
    monkeypatch.setenv("OPENMC_CROSS_SECTIONS", "/fake/path")
    from openmc_agent.real_campaign import run_real_campaign
    manifest = run_real_campaign(tmp_path, profile="pilot", runs=3, confirm_real_campaign=False)
    # Check that no artifact file contains the key.
    for f in tmp_path.rglob("*.json"):
        content = f.read_text()
        assert "secret-key-12345" not in content, f"API key found in {f}"
