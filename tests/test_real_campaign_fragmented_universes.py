"""Phase 7A real campaign fragmented universes integration tests."""

from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    RealCampaignCaseSpec,
    estimate_real_campaign_llm_budget,
)


def test_fragmented_mode_increases_budget_for_many_universes():
    """When universes_generation_mode=fragmented, the budget must include
    fragment calls — and grow with the universe count."""
    budget_fragmented = estimate_real_campaign_llm_budget(
        expected_patch_count=8,
        expected_universe_count=11,
        enabled_gate_count=5,
        max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2,
        max_runtime_iterations=0,
        universes_generation_mode="fragmented",
    )
    budget_monolithic = estimate_real_campaign_llm_budget(
        expected_patch_count=8,
        expected_universe_count=0,
        enabled_gate_count=5,
        max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2,
        max_runtime_iterations=0,
        universes_generation_mode="monolithic",
    )
    assert budget_fragmented.universe_fragments > 0
    assert budget_fragmented.universe_manifest == 1
    assert budget_monolithic.universe_fragments == 0
    assert budget_monolithic.universe_manifest == 0
    assert budget_fragmented.total > budget_monolithic.total


def test_auto_mode_with_many_universes_triggers_fragment_budget():
    """auto + expected_universe_count >= 6 implies fragmentation reserve."""
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8,
        expected_universe_count=11,
        enabled_gate_count=5,
        max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2,
        max_runtime_iterations=0,
        universes_generation_mode="auto",
    )
    assert budget.universe_fragments > 0
    assert budget.universe_manifest == 1


def test_canary_config_carries_fragmented_universes_mode():
    case = RealCampaignCaseSpec(
        case_id="vera4", input_path="/tmp/x.md",
        operating_state="", benchmark_label="VERA4",
        model="fake:test", output_dir="/tmp/out",
    )
    cfg = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        universes_generation_mode="fragmented",
        expected_universe_count=11,
    )
    assert cfg.universes_generation_mode == "fragmented"
    assert cfg.expected_universe_count == 11


def test_canary_config_strict_structured_default_is_true():
    case = RealCampaignCaseSpec(
        case_id="vera4", input_path="/tmp/x.md",
        operating_state="", benchmark_label="VERA4",
        model="fake:test", output_dir="/tmp/out",
    )
    cfg = CanaryCampaignConfig(case=case, runs=1, model="fake:test")
    assert cfg.strict_structured_patch_output is True


def test_canary_config_strict_structured_can_be_disabled():
    case = RealCampaignCaseSpec(
        case_id="vera4", input_path="/tmp/x.md",
        operating_state="", benchmark_label="VERA4",
        model="fake:test", output_dir="/tmp/out",
    )
    cfg = CanaryCampaignConfig(
        case=case, runs=1, model="fake:test",
        strict_structured_patch_output=False,
    )
    assert cfg.strict_structured_patch_output is False


def test_fragment_budget_proportional_to_universe_count():
    """More universes → more fragment calls reserved."""
    small = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=4,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="fragmented",
    )
    large = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=11,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="fragmented",
    )
    assert large.universe_fragments > small.universe_fragments
