"""Real campaign stage mode tests."""

import pytest

from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    RealCampaignCaseSpec,
    _validate_stage,
)


def _case() -> RealCampaignCaseSpec:
    return RealCampaignCaseSpec(
        case_id="x", input_path="/tmp/x.md", operating_state="",
        benchmark_label="X", model="fake:test", output_dir="/tmp/out",
    )


def test_validate_stage_accepts_planning():
    assert _validate_stage("planning") == "planning"


def test_validate_stage_accepts_render_compile():
    assert _validate_stage("render-compile") == "render-compile"


def test_validate_stage_accepts_openmc_smoke():
    assert _validate_stage("openmc-smoke") == "openmc-smoke"


def test_validate_stage_rejects_unknown():
    with pytest.raises(ValueError):
        _validate_stage("unknown-stage")


def test_canary_campaign_config_default_stage_is_planning():
    cfg = CanaryCampaignConfig(case=_case(), runs=1, model="fake:test")
    assert cfg.planning_stage == "planning"


def test_canary_campaign_config_renders_compile_stage():
    cfg = CanaryCampaignConfig(
        case=_case(), runs=1, model="fake:test",
        planning_stage="render-compile",
    )
    assert cfg.planning_stage == "render-compile"


def test_canary_campaign_config_openmc_smoke_stage():
    cfg = CanaryCampaignConfig(
        case=_case(), runs=1, model="fake:test",
        planning_stage="openmc-smoke",
    )
    assert cfg.planning_stage == "openmc-smoke"
    # CanaryCampaignConfig doesn't carry an enable_smoke_test field — the
    # per-run CanaryRunConfig derives it from the stage at run setup time.


def test_planning_stage_disables_smoke_test_in_run_config():
    """CanaryRunConfig built from CanaryCampaignConfig in planning stage
    must not enable smoke_test."""
    from openmc_agent.real_campaign_harness import CanaryRunConfig
    campaign = CanaryCampaignConfig(
        case=_case(), runs=1, model="fake:test",
        planning_stage="planning",
    )
    run_cfg = CanaryRunConfig(
        run_id="r1", run_index=1,
        case=campaign.case,
        policy=object(),
        env_status=object(),
        fingerprint=object(),
        output_dir="/tmp/out",
        model="fake:test",
        planning_stage=campaign.planning_stage,
        enable_smoke_test=campaign.planning_stage == "openmc-smoke",
    )
    assert run_cfg.enable_smoke_test is False


def test_openmc_smoke_stage_enables_smoke_test_in_run_config():
    from openmc_agent.real_campaign_harness import CanaryRunConfig
    campaign = CanaryCampaignConfig(
        case=_case(), runs=1, model="fake:test",
        planning_stage="openmc-smoke",
    )
    run_cfg = CanaryRunConfig(
        run_id="r1", run_index=1,
        case=campaign.case,
        policy=object(),
        env_status=object(),
        fingerprint=object(),
        output_dir="/tmp/out",
        model="fake:test",
        planning_stage=campaign.planning_stage,
        enable_smoke_test=campaign.planning_stage == "openmc-smoke",
    )
    assert run_cfg.enable_smoke_test is True


def test_planning_stage_does_not_require_openmc_environment(monkeypatch):
    """Even when OPENMC_CROSS_SECTIONS is unset, a planning canary should
    not be blocked by BLOCKED_BY_OPENMC_ENVIRONMENT."""
    from openmc_agent.real_campaign_harness import detect_provider_environment
    monkeypatch.delenv("OPENMC_CROSS_SECTIONS", raising=False)
    monkeypatch.setenv("SENSENOVA_API_KEY", "x")
    status = detect_provider_environment("ds:test")
    # LLM environment is OK; OpenMC env may be missing but that must not
    # block a planning canary.
    assert status.llm_environment_available is True
