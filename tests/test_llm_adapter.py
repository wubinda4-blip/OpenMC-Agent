"""Tests for real LLM adapter (Phase 7)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.llm_adapter import (
    PATCH_MAX_TOKENS,
    make_patch_llm_client,
)
from openmc_agent.plan_builder.patch_generator import FakePatchLLM


# ---------------------------------------------------------------------------
# 1. Adapter wraps existing callable
# ---------------------------------------------------------------------------


def test_adapter_wraps_callable() -> None:
    """A pre-existing callable is used directly."""
    fake = FakePatchLLM(['{"patch_type": "facts"}'])
    client = make_patch_llm_client(fake)
    result = client("test prompt")
    assert result == '{"patch_type": "facts"}'
    assert len(fake.prompts) == 1
    assert fake.prompts[0] == "test prompt"


def test_adapter_from_model_name_raises_without_provider() -> None:
    """Without llm or model_name, adapter should raise."""
    with pytest.raises(ValueError, match="requires either"):
        make_patch_llm_client(None)


def test_adapter_wraps_openai_compatible_client() -> None:
    """An OpenAI-compatible client is wrapped correctly."""

    class FakeChoice:
        class message:
            content = '{"patch_type": "settings"}'

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["model"] == "test:model"
                    assert kwargs["temperature"] == 0.0
                    return FakeResponse()

    client = make_patch_llm_client(FakeClient(), model_name="test:model")
    result = client("prompt")
    assert result == '{"patch_type": "settings"}'


def test_patch_max_tokens_budgets() -> None:
    """Token budgets are defined for all patch types."""
    for ptype in ("facts", "materials", "universes", "pin_map", "axial_layers", "axial_overlays"):
        assert ptype in PATCH_MAX_TOKENS
        assert PATCH_MAX_TOKENS[ptype] > 0
        assert PATCH_MAX_TOKENS[ptype] < 5000  # small budgets
