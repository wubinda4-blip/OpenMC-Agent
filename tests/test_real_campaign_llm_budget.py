"""Real campaign LLM budget estimator tests."""

from openmc_agent.real_campaign_harness import (
    CampaignLLMBudget,
    estimate_real_campaign_llm_budget,
)


def test_budget_returns_a_campaign_llm_budget_instance():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=4,
    )
    assert isinstance(budget, CampaignLLMBudget)


def test_budget_includes_patch_generation_reserve():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
    )
    assert budget.patch_generation >= 8


def test_budget_includes_universe_manifest_and_fragments_for_fragmented():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=11,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="fragmented",
    )
    assert budget.universe_manifest == 1
    assert budget.universe_fragments >= 11


def test_budget_omits_fragments_for_monolithic():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="monolithic",
    )
    assert budget.universe_manifest == 0
    assert budget.universe_fragments == 0


def test_budget_gate_review_grows_with_gate_count():
    small = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=1, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
    )
    large = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
    )
    assert large.gate_review > small.gate_review
    assert large.plan_repair > small.plan_repair


def test_budget_includes_runtime_reserve_when_iterations_nonzero():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=4,
    )
    assert budget.runtime_diagnosis >= 4
    assert budget.runtime_proposal >= 4


def test_budget_omits_runtime_supervisor_when_deterministic():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=4,
        enable_runtime_supervisor=False,
    )
    assert budget.runtime_supervisor == 0


def test_budget_includes_runtime_supervisor_when_real():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=4,
        enable_runtime_supervisor=True,
    )
    assert budget.runtime_supervisor >= 4


def test_budget_total_is_sum_of_components():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=11,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=2,
        universes_generation_mode="fragmented",
    )
    expected_total = (
        budget.patch_generation
        + budget.universe_manifest
        + budget.universe_fragments
        + budget.gate_review
        + budget.plan_repair
        + budget.runtime_diagnosis
        + budget.runtime_proposal
        + budget.runtime_supervisor
    )
    assert budget.total == expected_total


def test_budget_to_dict_includes_total():
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
    )
    d = budget.to_dict()
    assert d["total"] == budget.total
    assert "patch_generation" in d
    assert "universe_fragments" in d
    assert "gate_review" in d
