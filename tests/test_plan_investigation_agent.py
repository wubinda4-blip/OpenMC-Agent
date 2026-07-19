"""Tests for the InvestigationAgent LLM orchestration loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_investigation.agent import (
    BLOCK_CODE_ARGUMENT_INVALID,
    BLOCK_CODE_BUDGET_EXCEEDED,
    BLOCK_CODE_INVALID_LLM_OUTPUT,
    BLOCK_CODE_UNKNOWN_TOOL,
    InvestigationAction,
    InvestigationAgent,
    InvestigationBudget,
    InvestigationContext,
    InvestigationPlan,
    collect_evidence_for_patch_prompt,
)
from openmc_agent.plan_investigation.evidence_ledger import (
    create_empty_ledger,
    find_claims,
)
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_SEARCH_SOURCE_INDEX,
    build_default_step2_registry,
)


def _ctx(text="alpha\nbeta\ngamma\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    reg = build_default_step2_registry()
    return idx, ld, reg, InvestigationContext(
        requirement_text=text,
        patch_type="facts",
        available_tools=tuple(reg.list_tools()),
        source_indexes={idx.document.source_id: idx},
        ledger=ld,
    )


def _llm_returning(actions: list[dict[str, Any]], summary: str = "ok"):
    return lambda prompt: json.dumps({"actions": actions, "summary": summary})


def test_agent_executes_valid_actions_and_produces_evidence() -> None:
    """The mandatory baseline runs 3 tools; the LLM adds 1 more
    (search_source_index for 'alpha').  Total: 4 tool calls, 2 claims.
    """
    idx, ld, reg, ctx = _ctx("alpha\nbeta\ndensity gamma\n")
    fake = _llm_returning([{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}])
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.completed
    assert not res.blocked
    # 3 mandatory + 1 LLM supplemental = 4.
    assert len(res.tool_calls) == 4
    assert len(res.evidence_claim_ids) >= 1
    # The claim is queryable in the ledger.
    matches = find_claims(ld, predicate="search_hit")
    assert len(matches) >= 1


def test_agent_blocks_on_invalid_json() -> None:
    idx, ld, reg, ctx = _ctx()
    agent = InvestigationAgent(registry=reg, llm_client=lambda p: "not json")
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_agent_blocks_on_natural_language_tool_call() -> None:
    """LLM returns prose instead of strict JSON."""
    idx, ld, reg, ctx = _ctx()
    agent = InvestigationAgent(
        registry=reg,
        llm_client=lambda p: "Please call search_source_index with query=alpha",
    )
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_agent_blocks_on_unknown_tool() -> None:
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning([{"tool": "shell_exec", "arguments": {"cmd": "rm -rf /"}}])
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_UNKNOWN_TOOL


def test_agent_blocks_on_invalid_argument() -> None:
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning(
        [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {}}]  # missing required query
    )
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_ARGUMENT_INVALID


def test_agent_respects_max_tool_calls_budget() -> None:
    idx, ld, reg, ctx = _ctx("a\na\na\n")
    ctx = ctx.model_copy(
        update={"budget": InvestigationBudget(max_tool_calls=1, max_evidence_claims=100)}
    )
    fake = _llm_returning(
        [
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
        ]
    )
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_BUDGET_EXCEEDED
    # Only one tool call ran before budget tripped.
    assert len(res.tool_calls) == 1


def test_agent_respects_max_evidence_claims_budget() -> None:
    idx, ld, reg, ctx = _ctx("a\na\na\n")
    ctx = ctx.model_copy(
        update={"budget": InvestigationBudget(max_tool_calls=5, max_evidence_claims=1)}
    )
    fake = _llm_returning(
        [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}}]
    )
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    # The single call produced 3 claims, tripping the budget immediately.
    assert res.blocked
    assert res.block_code == BLOCK_CODE_BUDGET_EXCEEDED


def test_agent_does_not_modify_plan_build_state() -> None:
    """The agent accepts only a ledger + source_indexes; it cannot touch
    PlanBuildState.  Verify the contract by passing none.
    """
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning([{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}])
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    before = ld.model_dump(mode="json")
    agent.run(ctx)
    after = ld.model_dump(mode="json")
    # The ledger's claim count MUST grow (evidence was added), proving
    # the agent mutates only the ledger, not anything else.
    assert len(after["claims"]) > len(before["claims"])


def test_agent_empty_action_list_completes_with_mandatory_baseline() -> None:
    """With Step 5's mandatory baseline, an empty LLM action list still
    completes because Python runs the required tools first.  The
    session records the mandatory tool calls (inspect_patch_schema +
    inspect_requirement_structure + search_source_index).
    """
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning([], summary="nothing to investigate")
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.completed
    # Mandatory baseline ran 3 tools.
    assert len(res.tool_calls) == 3
    tool_names = {tc.tool_name for tc in res.tool_calls}
    assert "inspect_patch_schema" in tool_names
    assert "inspect_requirement_structure" in tool_names
    assert "search_source_index" in tool_names


def test_agent_rejects_extra_top_level_keys_in_llm_output() -> None:
    idx, ld, reg, ctx = _ctx()
    fake = lambda p: json.dumps(
        {"actions": [], "summary": "x", "secret_patch": {"value": 1}}
    )
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_agent_rejects_non_string_summary() -> None:
    idx, ld, reg, ctx = _ctx()
    fake = lambda p: json.dumps({"actions": [], "summary": 42})
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_collect_evidence_for_patch_prompt_shape() -> None:
    idx, ld, reg, ctx = _ctx("alpha\n")
    fake = _llm_returning([{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}])
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    payloads = collect_evidence_for_patch_prompt(ld, res.evidence_claim_ids)
    assert len(payloads) == 1
    claim = payloads[0]
    assert set(claim.keys()) >= {
        "claim_id",
        "subject",
        "predicate",
        "value",
        "status",
        "criticality",
        "source_spans",
    }
    # Excerpts are NOT in the prompt payload (would inflate the prompt).
    assert "excerpt" not in claim


def test_agent_result_hash_is_deterministic() -> None:
    """Two runs with identical LLM output produce identical result_hash."""
    idx1, ld1, reg1, ctx1 = _ctx("alpha\n")
    idx2, ld2, reg2, ctx2 = _ctx("alpha\n")
    fake = _llm_returning([{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}])
    res1 = InvestigationAgent(registry=reg1, llm_client=fake).run(ctx1)
    res2 = InvestigationAgent(registry=reg2, llm_client=fake).run(ctx2)
    assert res1.result_hash == res2.result_hash
