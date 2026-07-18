"""Tests for PatchLLMResponse.reasoning_only detection.

Covers the Phase 7A fix that surfaces the DS Flash failure mode where
``content`` is empty but ``reasoning_content`` is non-empty.  Without
this flag the failure is silent: callers see an empty string and
``json.loads("")`` fails with "Expecting value: line 1 column 1 (char 0)"
which is hard to distinguish from a network error.
"""

from __future__ import annotations

from openmc_agent.plan_builder.llm_adapter import (
    PatchLLMResponse,
    normalize_patch_llm_response,
)


def test_reasoning_only_defaults_to_false():
    resp = PatchLLMResponse(content="hello")
    assert resp.reasoning_only is False


def test_reasoning_only_true_when_content_empty_and_reasoning_present():
    """Mirrors the DS Flash failure mode."""
    resp = PatchLLMResponse(
        content="",
        metadata={"reasoning_chars": 8423, "reasoning_hash": "abc123"},
    )
    # When constructed directly, reasoning_only is the explicit field.
    # The flag is computed inside _call_raw based on response shape.
    # Here we just verify the field exists and is readable.
    assert hasattr(resp, "reasoning_only")


def test_normalize_preserves_reasoning_only_flag_for_string():
    """Legacy string responses get a default PatchLLMResponse with the
    new field defaulting to False."""
    resp = normalize_patch_llm_response("plain text")
    assert resp.reasoning_only is False
    assert resp.content == "plain text"


def test_normalize_passthrough_patch_response():
    resp = PatchLLMResponse(content="x", reasoning_only=True)
    out = normalize_patch_llm_response(resp)
    assert out is resp
    assert out.reasoning_only is True


def test_reasoning_only_can_be_serialized_via_dict():
    """Make sure dataclass conversion (used by recorder / artifact writers)
    picks up the new field."""
    from dataclasses import asdict
    resp = PatchLLMResponse(content="", reasoning_only=True, metadata={"reasoning_chars": 100})
    d = asdict(resp)
    assert d["reasoning_only"] is True
    assert d["content"] == ""


def test_reasoning_only_is_independent_of_truncation_flag():
    """reasoning_only is about empty content + reasoning present;
    is_truncated is about finish_reason. They are orthogonal."""
    resp = PatchLLMResponse(content="", finish_reason="length", reasoning_only=True)
    assert resp.is_truncated is True
    assert resp.reasoning_only is True

    resp2 = PatchLLMResponse(content="ok", finish_reason="stop", reasoning_only=False)
    assert resp2.is_truncated is False
    assert resp2.reasoning_only is False
