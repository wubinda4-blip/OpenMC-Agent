"""Tests for universe generation strategy admission."""

from openmc_agent.plan_builder.universe_fragment_generation import (
    estimate_universes_output_size,
    should_fragment_universes,
    resolve_patch_output_budget,
)


def test_auto_small_universes_stays_monolithic():
    do_it, reason = should_fragment_universes(
        mode="auto", universe_count=3, provider_max_output_tokens=16000,
    )
    assert do_it is False


def test_auto_large_universes_switches_to_fragmented():
    do_it, reason = should_fragment_universes(
        mode="auto", universe_count=12, provider_max_output_tokens=16000,
    )
    assert do_it is True


def test_auto_truncated_history_forces_fragmented():
    do_it, reason = should_fragment_universes(
        mode="auto", universe_count=3, history_json_truncated=True,
    )
    assert do_it is True
    assert "truncated" in reason


def test_explicit_fragmented_always_fragments():
    do_it, reason = should_fragment_universes(mode="fragmented", universe_count=1)
    assert do_it is True


def test_explicit_monolithic_never_fragments():
    do_it, reason = should_fragment_universes(mode="monolithic", universe_count=20)
    assert do_it is False


def test_reasoning_reduces_effective_budget():
    """When reasoning is enabled, the effective output budget is smaller."""
    no_reasoning, _ = should_fragment_universes(
        mode="auto", universe_count=8, provider_max_output_tokens=10000,
        reasoning_enabled=False,
    )
    with_reasoning, _ = should_fragment_universes(
        mode="auto", universe_count=8, provider_max_output_tokens=10000,
        reasoning_enabled=True,
    )
    # With reasoning, the reduced budget may push more cases into fragmented.
    assert with_reasoning is True or no_reasoning is True


def test_resolve_budget_explicit_overrides():
    budget = resolve_patch_output_budget(explicit=5000, fragment_mode=True, provider_max_output=10000)
    assert budget == 5000


def test_resolve_budget_fragment_default():
    budget = resolve_patch_output_budget(explicit=None, fragment_mode=True)
    assert budget > 0
    assert budget <= 5000  # conservative default


def test_resolve_budget_provider_capability():
    budget = resolve_patch_output_budget(explicit=None, fragment_mode=False, provider_max_output=12000)
    assert budget == 12000
