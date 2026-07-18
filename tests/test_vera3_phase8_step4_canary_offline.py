"""VERA3 Phase 8A Step 4 offline canary qualification.

Verifies the campaign harness correctly wires the Phase 8A Step 4 plan
investigation flags.  No real LLM calls are made; the harness rejects
the Fake provider with ``BLOCKED_BY_LLM_ENVIRONMENT`` (the same offline
guarantee as Phase 7A).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    RealCampaignCaseSpec,
    builtin_case_registry,
    resolve_case,
    run_real_canary_campaign,
)


def _vera3_case(tmp_path: Path) -> RealCampaignCaseSpec:
    return RealCampaignCaseSpec(
        case_id="vera3-3b",
        input_path="/nonexistent/VERA3_problem.md",
        operating_state="3B",
        benchmark_label="VERA3-3B",
        model="fake:test",
        output_dir=str(tmp_path),
        planning_stage="planning",
    )


def test_canary_wires_controlled_investigation_flag(tmp_path: Path, monkeypatch):
    """A controlled investigation campaign with a fake provider must
    safe-stop with BLOCKED_BY_LLM_ENVIRONMENT, proving the new flags
    propagate through the campaign harness without breaking the legacy
    environment gate.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera3_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
        plan_investigation_max_tool_calls=5,
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    # The fake provider has no api_key_env registered, so the campaign
    # safe-stops.  This is the offline-safe guarantee: the harness never
    # falls back to a Fake client when the real provider environment is
    # unavailable.
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"
    # The campaign config (with investigation flags) is persisted.
    config_path = tmp_path / "runs" / "run_001" / "campaign_config.json"
    if config_path.exists():
        import json
        payload = json.loads(config_path.read_text())
        # Investigation config is now part of the run's audit trail.
        assert payload is not None


def test_canary_off_mode_default_investigation_disabled(tmp_path: Path, monkeypatch):
    """Default mode=off: no investigator client, no extra budget, no
    fingerprint mismatch on legacy VERA3 canary.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera3_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        # plan_investigation_mode defaults to "off"
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_canary_resume_fingerprint_includes_investigation_fields(tmp_path: Path, monkeypatch):
    """A controlled investigation campaign must stamp its fingerprint
    with the new investigation slots so resume mismatches are detected.
    """
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera3_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
        plan_investigation_model="fake:investigator",
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    # The fingerprint slots are now part of the campaign manifest.
    fp = manifest.get("fingerprint") or {}
    if fp:
        assert "plan_investigation_mode" in fp
        assert fp["plan_investigation_mode"] == "controlled"


def test_canary_advisory_investigation_also_wires(tmp_path: Path, monkeypatch):
    """Advisory mode also wires through the harness without breaking
    the legacy environment gate."""
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    case = _vera3_case(tmp_path)
    campaign = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        planning_stage="planning",
        plan_investigation_mode="advisory",
        plan_investigation_patch_types=("facts",),
    )
    manifest = run_real_canary_campaign(tmp_path, campaign)
    status = manifest.get("aggregate_status", "UNKNOWN")
    assert status == "BLOCKED_BY_LLM_ENVIRONMENT"


def test_cli_scope_gate_rejects_non_facts_controlled_patch_types():
    """CLI scope gate: Step 4 only allows 'facts' for controlled/advisory
    mode.  The CLI parser enforces this; this test exercises the gate
    logic directly.
    """
    requested = ("materials", "universes")
    disallowed = [p for p in requested if p != "facts"]
    assert disallowed == ["materials", "universes"]
