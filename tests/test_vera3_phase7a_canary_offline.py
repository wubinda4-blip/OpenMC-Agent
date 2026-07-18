"""VERA3 Phase 7A offline canary qualification.

These tests do NOT call the real LLM or OpenMC.  They verify the
campaign harness wires up correctly for a VERA3 3B planning canary,
using a Fake provider prefix that the harness rejects with
``BLOCKED_BY_LLM_ENVIRONMENT`` (since ``fake:`` is not a registered
provider).

This matches the Phase 7A design: environment gaps return explicit
``BLOCKED_BY_*`` status codes, never Fake fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    RealCampaignCaseSpec,
    builtin_case_registry,
    resolve_case,
    run_real_canary_campaign,
)


def test_builtin_registry_contains_vera3_3b():
    registry = builtin_case_registry()
    assert "vera3-3b" in registry
    case = registry["vera3-3b"]
    assert case.case_id == "vera3-3b"
    assert case.benchmark_label == "VERA3-3B"
    assert case.operating_state == "3B"


def test_resolve_case_vera3_3b_preset():
    case = resolve_case(
        case="vera3-3b", input_path=None, operating_state="",
        model="ds:test", output_dir="/tmp/out",
        planning_stage="planning",
        human_answer_file=None,
        acceptance_profile="pilot",
    )
    assert case.case_id == "vera3-3b"
    assert case.operating_state == "3B"


def test_resolve_case_input_override():
    """--input always overrides the preset's input_path."""
    case = resolve_case(
        case="vera3-3b", input_path="/custom/path.md",
        operating_state="", model="ds:test",
        output_dir="/tmp/out", planning_stage="planning",
        human_answer_file=None, acceptance_profile="pilot",
    )
    assert case.input_path == "/custom/path.md"


def test_unknown_case_preset_rejected():
    with pytest.raises(ValueError):
        resolve_case(
            case="unknown", input_path=None, operating_state="",
            model="ds:test", output_dir="/tmp/out",
            planning_stage="planning",
            human_answer_file=None, acceptance_profile="pilot",
        )


def test_vera3_3b_planning_canary_blocked_by_fake_environment(tmp_path: Path, monkeypatch):
    """A Fake provider has no api_key_env registered, so the campaign
    must safe-stop with BLOCKED_BY_LLM_ENVIRONMENT.

    This is the offline-safe guarantee: the harness never falls back to
    a Fake client when the real provider environment is unavailable.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    assert manifest["aggregate_status"] == "BLOCKED_BY_LLM_ENVIRONMENT"
    assert manifest["completed_runs"] == 0


def test_vera3_3b_render_compile_blocked_by_openmc_environment(tmp_path: Path, monkeypatch):
    """render-compile stage requires the OpenMC library + cross sections."""
    monkeypatch.setenv("SENSENOVA_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="ds:deepseek-v4-flash",
        output_dir=str(tmp_path),
        planning_stage="render-compile",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="ds:deepseek-v4-flash",
        planning_stage="render-compile",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    # Either LLM blocked or OpenMC blocked, depending on whether OpenMC is
    # importable in the test environment.
    assert manifest["aggregate_status"] in {
        "BLOCKED_BY_OPENMC_ENVIRONMENT",
        "BLOCKED_BY_CROSS_SECTIONS_ENVIRONMENT",
        "BLOCKED_BY_LLM_ENVIRONMENT",
    }
    assert manifest["completed_runs"] == 0


def test_vera3_3b_writes_campaign_manifest_and_budget(tmp_path: Path, monkeypatch):
    """Even when blocked, the harness writes the manifest and budget."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
    )
    run_real_canary_campaign(tmp_path, campaign)
    assert (tmp_path / "campaign_manifest.json").exists()
    assert (tmp_path / "llm_budget.json").exists()
    budget = json.loads((tmp_path / "llm_budget.json").read_text())
    assert "patch_generation" in budget
    assert "gate_review" in budget
    assert "universe_fragments" in budget


def test_vera3_3b_manifest_records_five_gate_policy(tmp_path: Path, monkeypatch):
    """The campaign manifest must show all five gates enabled and controlled."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    enabled = manifest["enabled_gates"]
    assert "facts" in enabled
    assert "material_universe" in enabled
    assert "placement" in enabled
    assert "axial_geometry" in enabled
    assert "assembled_plan" in enabled
    review_modes = manifest["review_modes"]
    assert review_modes.count("controlled") == 4


def test_vera3_3b_planning_stage_does_not_invoke_smoke_tools(tmp_path: Path, monkeypatch):
    """A blocked planning canary must never have produced smoke artifacts.

    The harness doesn't even invoke the production graph when blocked,
    so no tool calls happen.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
    )
    run_real_canary_campaign(tmp_path, campaign)
    # No runs/ directory should have been created (campaign was blocked).
    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or not list(runs_dir.iterdir())


def test_vera3_3b_pilot_canary_exits_zero_only_on_real_success(tmp_path: Path, monkeypatch):
    """The pilot success path is unreachable from this offline test — it
    would require a real LLM.  The harness must NOT declare pilot
    success when blocked."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
        acceptance_profile="pilot",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=3, model="fake:test",
        planning_stage="planning",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    assert manifest["aggregate_status"] != "VERA3_REAL_CONTROLLED_PLANNING_CANARY_PASSED"
