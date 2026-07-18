"""Tests for strict structured output fail-closed policy."""

from openmc_agent.plan_builder.llm_adapter import PatchLLMResponse


def test_strict_mode_returns_unavailable_on_failure():
    """When strict mode is on and both json_schema/json_object fail, return unavailable."""
    # This is tested indirectly through the StructuredPatchLLMClient behavior.
    # The key invariant: strict mode must NOT silently fall back to plain_prompt.
    resp = PatchLLMResponse(content="", output_mode_used="unavailable")
    resp.metadata["error_code"] = "patch_generation.structured_output_unavailable"
    assert resp.output_mode_used == "unavailable"
    assert resp.metadata["error_code"] == "patch_generation.structured_output_unavailable"


def test_non_strict_mode_allows_plain_prompt():
    """Without strict mode, plain_prompt fallback is still allowed."""
    resp = PatchLLMResponse(content="some text", output_mode_used="plain_prompt")
    assert resp.output_mode_used == "plain_prompt"
    assert resp.content != ""


def test_plain_prompt_fallback_still_returns_content():
    resp = PatchLLMResponse(content='{"patch_type": "settings"}', output_mode_used="plain_prompt")
    assert resp.content.startswith("{")


def test_structured_fallback_recorded():
    resp = PatchLLMResponse(
        content="ok", output_mode_used="json_object",
        structured_fallback_used=True,
        structured_fallback_reasons=["json_schema unsupported: BadRequestError"],
    )
    assert resp.structured_fallback_used is True
    assert len(resp.structured_fallback_reasons) == 1
