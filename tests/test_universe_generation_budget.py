"""Tests for universe generation token budget policy."""

from openmc_agent.plan_builder.universe_fragment_generation import resolve_patch_output_budget


def test_explicit_budget_used_when_provided():
    assert resolve_patch_output_budget(explicit=8000) == 8000


def test_fragment_budget_smaller_than_monolithic():
    fragment_budget = resolve_patch_output_budget(explicit=None, fragment_mode=True)
    monolithic_budget = resolve_patch_output_budget(explicit=None, fragment_mode=False, provider_max_output=16000)
    assert fragment_budget < monolithic_budget


def test_conservative_default_when_nothing_provided():
    budget = resolve_patch_output_budget(explicit=None, fragment_mode=False, provider_max_output=None)
    assert budget > 0
    assert budget >= 4000  # must be large enough for a small patch


def test_fragment_explicit_overrides_fragment_mode():
    budget = resolve_patch_output_budget(explicit=3000, fragment_mode=True)
    assert budget == 3000
