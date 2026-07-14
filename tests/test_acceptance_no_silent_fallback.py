"""Tests that acceptance does not silently fall back to basic."""

from __future__ import annotations

import pytest

from openmc_agent.campaign_eval.vera3_campaign_acceptance import (
    FullAcceptanceLoadError,
    make_vera3b_acceptance_callback,
)


def test_load_error_raised_on_failure(monkeypatch):
    """FullAcceptanceLoadError should be raised, not silently swallowed."""
    def _bad_import(*args, **kwargs):
        raise ImportError("simulated failure")

    monkeypatch.setattr(
        "openmc_agent.campaign_eval.vera3_campaign_acceptance._ensure_test_helpers_importable",
        lambda: None,
    )
    # Patch the import to fail.
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name.startswith("tests.helpers.vera3_acceptance"):
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    with pytest.raises(FullAcceptanceLoadError):
        make_vera3b_acceptance_callback()


def test_no_silent_basic_fallback():
    """The module should not provide any basic acceptance fallback."""
    import openmc_agent.campaign_eval.vera3_campaign_acceptance as mod
    # The module should not have a basic fallback function.
    assert not hasattr(mod, "make_basic_acceptance_callback")
    assert not hasattr(mod, "basic_callback")
