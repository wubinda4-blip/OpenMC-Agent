"""Tests for real LLM adapter (Phase 7)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.llm_adapter import (
    PATCH_MAX_TOKENS,
    StructuredPatchLLMClient,
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
    """Reference token budgets are defined for all patch types.

    These are reference values (NOT auto-applied caps) documenting typical
    multi-assembly patch sizes; a caller may pass them explicitly to
    generate_patch(max_tokens=...). Provider defaults are used otherwise, as
    they are larger than any safe universal cap. See PATCH_MAX_TOKENS comment.
    """
    for ptype in ("facts", "materials", "universes", "pin_map", "axial_layers", "axial_overlays"):
        assert ptype in PATCH_MAX_TOKENS
        assert PATCH_MAX_TOKENS[ptype] > 0


def test_json_schema_response_format_is_sent_to_provider() -> None:
    calls = []

    class FakeChoice:
        class message:
            content = "{}"

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return FakeResponse()

    client = StructuredPatchLLMClient(FakeClient(), model_name="test:model", output_mode="json_schema")
    assert client.generate_patch_json(
        prompt="prompt", patch_type="pin_map", json_schema={"type": "object"},
    ) == "{}"
    assert calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "pin_map_patch_repair",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    assert client.last_output_mode_used == "json_schema"
    assert client.last_output_fallback_used is False


def test_json_schema_provider_rejection_falls_back_to_json_object() -> None:
    calls = []

    class FakeChoice:
        class message:
            content = "{}"

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if kwargs.get("response_format", {}).get("type") == "json_schema":
                        raise RuntimeError("unsupported response format")
                    return FakeResponse()

    client = StructuredPatchLLMClient(FakeClient(), model_name="test:model", output_mode="auto")
    client.generate_patch_json(prompt="prompt", patch_type="pin_map", json_schema={"type": "object"})
    assert [call.get("response_format", {}).get("type") for call in calls] == ["json_schema", "json_object"]
    assert client.last_output_mode_used == "json_object"
    assert client.last_output_fallback_used is True
    assert "json_schema unsupported: RuntimeError" in client.last_output_fallback_reasons
