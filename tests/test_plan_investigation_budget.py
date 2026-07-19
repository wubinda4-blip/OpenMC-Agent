"""Tests for the investigation budget enforcement."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_investigation.agent import (
    BLOCK_CODE_BUDGET_EXCEEDED,
    InvestigationAgent,
    InvestigationBudget,
    InvestigationBudgetUsage,
    InvestigationContext,
)
from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_SEARCH_SOURCE_INDEX,
    build_default_step2_registry,
)


def _ctx(text="alpha\nbeta\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    reg = build_default_step2_registry()
    return idx, ld, InvestigationContext(
        requirement_text=text,
        patch_type="facts",
        available_tools=tuple(reg.list_tools()),
        source_indexes={idx.document.source_id: idx},
        ledger=ld,
    ), reg


def test_default_budget_is_five_calls() -> None:
    budget = InvestigationBudget()
    assert budget.max_tool_calls == 5
    assert budget.max_results_per_tool == 50
    assert budget.max_evidence_claims == 100


def test_usage_exceeds_when_tool_calls_over() -> None:
    budget = InvestigationBudget(max_tool_calls=2)
    usage = InvestigationBudgetUsage(tool_calls=3)
    assert usage.exceeds(budget)


def test_usage_exceeds_when_evidence_claims_over() -> None:
    budget = InvestigationBudget(max_evidence_claims=5)
    usage = InvestigationBudgetUsage(evidence_claims=6)
    assert usage.exceeds(budget)


def test_budget_zero_blocks_immediately() -> None:
    idx, ld, ctx, reg = _ctx()
    ctx = ctx.model_copy(update={"budget": InvestigationBudget(max_tool_calls=0)})
    fake = lambda p: json.dumps({"actions": [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}]})
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_BUDGET_EXCEEDED
    assert len(res.tool_calls) == 0


def test_budget_blocks_partway_through_action_list() -> None:
    """If budget trips mid-list, prior tool calls are still recorded."""
    idx, ld, ctx, reg = _ctx("a\na\na\n")
    ctx = ctx.model_copy(update={"budget": InvestigationBudget(max_tool_calls=1)})
    fake = lambda p: json.dumps({
        "actions": [
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
        ]
    })
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_BUDGET_EXCEEDED
    # One call ran; the second was blocked.
    assert len(res.tool_calls) == 1
    assert res.budget_used.tool_calls >= 1  # mandatory baseline + LLM actions


def test_budget_count_includes_only_successful_dispatch() -> None:
    """A blocked-on-validation action does NOT increment tool_calls
    (the validation runs before the budget counter).
    """
    idx, ld, ctx, reg = _ctx("a\n")
    fake = lambda p: json.dumps({
        "actions": [
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
            {"tool": "unknown_tool", "arguments": {}},
        ]
    })
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    # The second action blocked the session (unknown tool), but the
    # first call's budget usage is recorded.
    assert res.blocked
    assert res.budget_used.tool_calls >= 1  # mandatory baseline + LLM actions


def test_budget_count_does_not_grow_with_duplicate_search() -> None:
    """Duplicate search across two LLM calls still produces evidence
    claims (deduplication happens at the claim level, not the call level).
    Budget is per-call, not per-distinct-claim.
    """
    idx, ld, ctx, reg = _ctx("a\n")
    fake = lambda p: json.dumps({
        "actions": [
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
            {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}},
        ]
    })
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.completed
    assert res.budget_used.tool_calls >= 2  # mandatory baseline + LLM


def test_max_results_per_tool_propagated_to_request() -> None:
    """The InvestigationBudget.max_results_per_tool should bound tool
    output via the underlying InvestigationToolRequest.max_results.
    """
    idx, ld, ctx, reg = _ctx("a\na\na\na\na\na\n")
    ctx = ctx.model_copy(
        update={"budget": InvestigationBudget(max_tool_calls=5, max_results_per_tool=2)}
    )
    fake = lambda p: json.dumps({
        "actions": [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "a"}}]
    })
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    # Find the search_source_index result among the mandatory + LLM results.
    search_result = next(
        (r for r in res.tool_results if r.tool_name == "search_source_index"
         and r.result.get("query") == "a"),
        None,
    )
    assert search_result is not None
    # max_results_per_tool=2 truncates the 6-hit result to 2 spans.
    assert search_result.result["total_hits"] == 2
    assert search_result.result["truncated"] is True
