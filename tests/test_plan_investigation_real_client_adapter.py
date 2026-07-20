"""Tests for the investigator LLM adapter + recorder integration."""

from __future__ import annotations

from typing import Any

import pytest

from openmc_agent.llm_call_recorder import LLMCallRecorder
from openmc_agent.plan_investigation.llm_adapter import (
    PLAN_INVESTIGATOR_INSTANCE_PREFIX,
    PLAN_INVESTIGATOR_ROLE,
    make_investigation_llm_client,
)


class _FakeChatCompletion:
    def __init__(self, content: str) -> None:
        self.message = type("Msg", (), {"content": content})()


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = type(
            "Completions",
            (),
            {
                "create": lambda self, **kwargs: type(
                    "Resp", (), {"choices": [_FakeChatCompletion(content)]}
                )()
            },
        )()


class _FakeBaseLLM:
    """Minimal OpenAI-compatible provider for testing."""

    def __init__(self, content: str = "{'actions': []}") -> None:
        self.chat = _FakeChat(content)


def test_make_investigation_llm_client_returns_callable() -> None:
    base_llm = _FakeBaseLLM(content='{"actions": []}')
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="fake:test",
    )
    assert callable(client)
    result = client("test prompt")
    assert isinstance(result, str)


def test_make_investigation_llm_client_wraps_with_recorder() -> None:
    """When a recorder is supplied, the client is wrapped and the
    plan_investigator role is used.
    """
    base_llm = _FakeBaseLLM(content='{"actions": []}')
    recorder = LLMCallRecorder(
        run_id="run_test", model="fake:test", provider="fake", max_calls=10
    )
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="fake:test",
        recorder=recorder,
    )
    client("test prompt")
    assert recorder.call_count == 1
    summary = recorder.evidence_summary()
    # The role on the recorded call should be the investigator role.
    assert any(
        rec["role"] == PLAN_INVESTIGATOR_ROLE for rec in recorder.to_dict_list()
    )


def test_client_instance_id_uses_investigator_prefix() -> None:
    base_llm = _FakeBaseLLM(content='{"actions": []}')
    recorder = LLMCallRecorder(
        run_id="run_test", model="fake:test", provider="fake", max_calls=10
    )
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="fake:test",
        recorder=recorder,
    )
    client("test prompt")
    cids = recorder.evidence_summary()["client_instance_ids"]
    assert any(cid.startswith(PLAN_INVESTIGATOR_INSTANCE_PREFIX) for cid in cids)


def test_strict_structured_output_default_true() -> None:
    """Investigator should default to strict structured output so a
    provider that cannot deliver JSON fails closed.
    """
    base_llm = _FakeBaseLLM(content='{"actions": []}')
    # Construct with defaults; the strict_structured_output flag is
    # observable via the wrapped StructuredPatchLLMClient attribute.
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="fake:test",
    )
    # The underlying StructuredPatchLLMClient carries the flag.
    inner = getattr(client, "_inner", client)
    assert getattr(inner, "strict_structured_output", True) is True


def test_no_fake_fallback() -> None:
    """The investigator client never falls back to Fake when the provider
    fails.  A failure surfaces as an exception.
    """
    class _RaisingChat:
        def __init__(self) -> None:
            self.completions = type(
                "C", (),
                {"create": lambda self, **kw: (_ for _ in ()).throw(RuntimeError("boom"))},
            )()

    base_llm = type("LLM", (), {"chat": _RaisingChat()})()
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="fake:test",
    )
    with pytest.raises(Exception):
        client("test prompt")


def test_recorder_attributes_investigator_calls_separately() -> None:
    """Two roles (planning_patch + plan_investigator) produce two distinct
    role counts in the recorder evidence summary."""
    base_llm = _FakeBaseLLM(content='{"actions": []}')
    recorder = LLMCallRecorder(
        run_id="run_test", model="fake:test", provider="fake", max_calls=10
    )
    investigator = make_investigation_llm_client(
        base_llm=base_llm, model_name="fake:test", recorder=recorder,
    )
    # Wrap a planning client to simulate the patch LLM.
    def patch_client(prompt):
        return "patch"

    wrapped_patch = recorder.wrap_planning_client(patch_client, "patch_test")
    investigator("inv prompt")
    wrapped_patch("patch prompt")
    summary = recorder.evidence_summary()
    # Both roles recorded.
    roles = {rec["role"] for rec in recorder.to_dict_list()}
    assert "planning_patch" in roles
    assert PLAN_INVESTIGATOR_ROLE in roles


def test_generate_patch_json_is_recorded_by_wrapper() -> None:
    """The ``_PromptOnlyWrapper`` must intercept ``generate_patch_json``
    (the structured-output transaction kernel's preferred call path) and
    record it.  Without this, investigator calls bypass the recorder via
    ``__getattr__`` delegation and produce a false
    ``real_llm_not_verified`` truthfulness violation.
    """

    base_llm = _FakeBaseLLM(content='{"actions": []}')
    recorder = LLMCallRecorder(
        run_id="run_test", model="ds:deepseek-v4-flash", provider="ds", max_calls=10
    )
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="ds:deepseek-v4-flash",
        recorder=recorder,
    )
    # The structured-output transaction kernel calls generate_patch_json
    # when the inner client exposes it (StructuredPatchLLMClient does).
    assert hasattr(client, "generate_patch_json")
    client.generate_patch_json(
        prompt="inv prompt",
        patch_type="investigation_plan",
        json_schema={"type": "object"},
    )
    assert recorder.call_count == 1
    summary = recorder.evidence_summary()
    # The call must be attributed to the investigator role, not silently
    # dropped.
    assert summary["total_calls"] == 1
    roles = {rec["role"] for rec in recorder.to_dict_list()}
    assert PLAN_INVESTIGATOR_ROLE in roles
    # A non-fake provider with a successful call marks the network call
    # as verified so truthfulness audits do not flag it.
    assert summary["real_network_call_count"] == 1


def test_generate_patch_json_via_structured_output_transaction() -> None:
    """End-to-end: ``run_structured_output_transaction`` calling
    ``generate_patch_json`` on the investigator wrapper must record the
    call and return the parsed output.
    """
    from openmc_agent.structured_output import run_structured_output_transaction
    from openmc_agent.plan_investigation.agent import InvestigationPlan

    base_llm = _FakeBaseLLM(content='{"actions": []}')
    recorder = LLMCallRecorder(
        run_id="run_test", model="ds:deepseek-v4-flash", provider="ds", max_calls=10
    )
    client = make_investigation_llm_client(
        base_llm=base_llm,
        model_name="ds:deepseek-v4-flash",
        recorder=recorder,
    )

    def _planner_call(c: Any, current_prompt: str) -> Any:
        if hasattr(c, "generate_patch_json"):
            return c.generate_patch_json(
                prompt=current_prompt,
                patch_type="investigation_plan",
                json_schema=InvestigationPlan.model_json_schema(),
            )
        return c(current_prompt)

    transaction = run_structured_output_transaction(
        client=client,
        initial_prompt="test prompt",
        retry_prompt_builder=lambda raw, err: type(
            "R", (), {"prompt": "repair", "input_payload_hash": "x"}
        )(),
        output_model=InvestigationPlan,
        call=_planner_call,
        payload={"k": "v"},
        max_attempts=1,
        budget_available=lambda: True,
        charge_budget=lambda: None,
    )
    assert transaction.ok
    assert recorder.call_count == 1
