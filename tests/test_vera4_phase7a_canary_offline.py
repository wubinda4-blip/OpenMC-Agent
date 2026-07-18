"""VERA4 Phase 7A offline canary qualification.

These tests do NOT call the real LLM or OpenMC.  They verify the
campaign harness wires up correctly for a VERA4 fragmented-universe
planning canary.
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


def test_builtin_registry_contains_vera4():
    registry = builtin_case_registry()
    assert "vera4" in registry
    case = registry["vera4"]
    assert case.case_id == "vera4"
    assert case.benchmark_label == "VERA4"
    assert case.operating_state == ""  # VERA4 has no substates


def test_resolve_case_vera4_preset():
    case = resolve_case(
        case="vera4", input_path=None, operating_state="",
        model="ds:test", output_dir="/tmp/out",
        planning_stage="planning",
        human_answer_file=None, acceptance_profile="pilot",
    )
    assert case.case_id == "vera4"
    assert case.operating_state == ""


def test_vera4_default_universes_generation_mode_is_auto():
    """The campaign config defaults to 'auto'; the CLI defaults to 'auto' too.
    Phase 7A VERA4 canary runs in 'fragmented' mode explicitly via CLI."""
    case = RealCampaignCaseSpec(
        case_id="vera4", input_path="/tmp/x.md",
        operating_state="", benchmark_label="VERA4",
        model="fake:test", output_dir="/tmp/out",
    )
    cfg = CanaryCampaignConfig(case=case, runs=1, model="fake:test")
    assert cfg.universes_generation_mode == "auto"


def test_vera4_fragmented_mode_propagates_to_manifest(tmp_path: Path, monkeypatch):
    """The manifest must record universes_generation_mode=fragmented when set."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        universes_generation_mode="fragmented",
        expected_universe_count=11,
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    assert manifest["universes_generation_mode"] == "fragmented"
    assert manifest["case"]["case_id"] == "vera4"


def test_vera4_planning_canary_blocked_by_fake_environment(tmp_path: Path, monkeypatch):
    """Offline-safe: a Fake provider prefix must NOT silently run."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        universes_generation_mode="fragmented",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    assert manifest["aggregate_status"] == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_vera4_fragmented_budget_includes_fragment_calls(tmp_path: Path, monkeypatch):
    """A blocked VERA4 fragmented canary still records the budget that
    WOULD have been used (so resume / auditing works)."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        universes_generation_mode="fragmented",
        expected_universe_count=11,
    )
    run_real_canary_campaign(tmp_path, campaign)
    budget = json.loads((tmp_path / "llm_budget.json").read_text())
    assert budget["universe_fragments"] >= 11
    assert budget["universe_manifest"] == 1


def test_vera4_strict_structured_output_propagates(tmp_path: Path, monkeypatch):
    """The campaign manifest must record strict_structured_patch_output."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        strict_structured_patch_output=True,
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    assert manifest["strict_structured_patch_output"] is True


def test_vera4_no_partial_fragment_exposure_offline(tmp_path: Path, monkeypatch):
    """A blocked canary produces no fragment artifacts at all — the
    blocked-run must not have any partial fragment leaked."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        universes_generation_mode="fragmented",
    )
    run_real_canary_campaign(tmp_path, campaign)
    # No runs dir created because blocked.
    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or not list(runs_dir.iterdir())
