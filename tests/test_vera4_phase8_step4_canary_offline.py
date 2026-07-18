"""VERA4 Phase 8A Step 4 offline canary qualification.

Mirror of the VERA3 Step 4 tests for the VERA4 case preset.  No real
LLM calls are made; the harness rejects the Fake provider with
``BLOCKED_BY_LLM_ENVIRONMENT``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    RealCampaignCaseSpec,
    builtin_case_registry,
    run_real_canary_campaign,
)


def _vera4_case(tmp_path: Path) -> RealCampaignCaseSpec:
    return RealCampaignCaseSpec(
        case_id="vera4",
        input_path="/nonexistent/VERA4_problem.md",
        operating_state="",
        benchmark_label="VERA4",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )


def test_vera4_case_in_registry():
    registry = builtin_case_registry()
    assert "vera4" in registry
    case = registry["vera4"]
    assert case.case_id == "vera4"


def test_vera4_controlled_investigation_safe_stops(tmp_path: Path, monkeypatch):
    """A controlled investigation campaign with a fake provider must
    safe-stop with BLOCKED_BY_LLM_ENVIRONMENT.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera4_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_vera4_off_mode_default(tmp_path: Path, monkeypatch):
    """Default mode=off: legacy VERA4 canary behaviour preserved."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera4_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_vera4_advisory_investigation_wires(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera4_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="advisory",
        plan_investigation_patch_types=("facts",),
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_vera4_investigation_with_reasoning_effort_safe_stops(tmp_path: Path, monkeypatch):
    """A fully-configured controlled campaign (reasoning_effort,
    output_mode) still safe-stops on a fake provider."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera4_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
        plan_investigation_model="fake:investigator",
        plan_investigation_reasoning_effort="medium",
        plan_investigation_output_mode="json_schema",
        plan_investigation_max_tool_calls=3,
        plan_investigation_max_evidence_claims=50,
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"
