"""Tests that full VERA3B acceptance is mandatory for qualification."""

from __future__ import annotations

from openmc_agent.campaign_eval.vera3_campaign_acceptance import (
    FullAcceptanceLoadError,
    make_vera3b_acceptance_callback,
    _ACCEPTANCE_CONTRACT_VERSION,
)


def test_full_acceptance_callback_loads():
    """The full acceptance callback should load successfully."""
    callback = make_vera3b_acceptance_callback()
    assert callable(callback)


def test_acceptance_contract_version():
    """Acceptance contract version should be tracked."""
    assert _ACCEPTANCE_CONTRACT_VERSION == "2.0.0"


def test_callback_returns_tuple():
    """The callback should return (passed, issue_codes) tuple."""
    callback = make_vera3b_acceptance_callback()
    result = callback(None)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] is False
    assert "no_plan" in result[1]
