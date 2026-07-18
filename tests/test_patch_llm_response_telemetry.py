"""Tests for PatchLLMResponse telemetry and normalize_patch_llm_response."""

from openmc_agent.plan_builder.llm_adapter import PatchLLMResponse, normalize_patch_llm_response


def test_str_response_normalized():
    resp = normalize_patch_llm_response("hello world")
    assert isinstance(resp, PatchLLMResponse)
    assert resp.content == "hello world"
    assert resp.output_mode_used == "plain_prompt"


def test_patch_llm_response_passthrough():
    original = PatchLLMResponse(content="test", finish_reason="length")
    resp = normalize_patch_llm_response(original)
    assert resp is original


def test_finish_reason_length_is_truncated():
    resp = PatchLLMResponse(content="", finish_reason="length")
    assert resp.is_truncated is True


def test_finish_reason_stop_not_truncated():
    resp = PatchLLMResponse(content="ok", finish_reason="stop")
    assert resp.is_truncated is False


def test_finish_reason_none_not_truncated():
    resp = PatchLLMResponse(content="ok", finish_reason=None)
    assert resp.is_truncated is False


def test_reasoning_content_not_persisted():
    resp = PatchLLMResponse(content="ok", metadata={"reasoning_chars": 500, "reasoning_hash": "abc123"})
    assert resp.reasoning_chars == 500
    assert resp.reasoning_hash == "abc123"
    assert "reasoning_content" not in resp.metadata


def test_token_fields_default_none():
    resp = PatchLLMResponse(content="ok")
    assert resp.prompt_tokens is None
    assert resp.completion_tokens is None
    assert resp.reasoning_tokens is None
    assert resp.total_tokens is None
