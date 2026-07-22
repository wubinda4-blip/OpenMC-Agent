"""Tests for the InvestigationAgent LLM orchestration loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_investigation.agent import (
    BLOCK_CODE_BUDGET_EXCEEDED,
    BLOCK_CODE_INVALID_LLM_OUTPUT,
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
from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import CampaignCheckpointStore
from openmc_agent.plan_builder.closed_loop.state_snapshot import make_facts_action_callback


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


def test_action_checkpoint_resume_reuses_completed_planner_and_tools(tmp_path) -> None:
    """An interruption after the normalized planner result is durable must
    not repeat the planner or already-completed deterministic tool actions.
    """

    store = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    durable_callback = make_facts_action_callback(store)
    interrupted = [False]

    def crash_after_planner(**kwargs) -> None:
        durable_callback(**kwargs)
        if kwargs["tool_name"] == "planner" and not interrupted[0]:
            interrupted[0] = True
            raise RuntimeError("simulated process interruption")

    setattr(crash_after_planner, "restore_action", durable_callback.restore_action)
    planner_calls = [0]

    def fake_llm(prompt: str) -> str:
        if "Facts extraction agent" in prompt:
            return json.dumps({"claims": []})
        planner_calls[0] += 1
        return json.dumps({"actions": []})

    _, _, registry, first_context = _ctx("assembly alpha\n")
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        InvestigationAgent(
            registry=registry,
            llm_client=fake_llm,
            action_callback=crash_after_planner,
        ).run(first_context)
    assert planner_calls == [1]

    # A fresh process reconstructs the source index and ledger.  Completed
    # baseline actions and the normalized planner plan are restored from the
    # checkpoint rather than invoking their original work again.
    _, _, resumed_registry, resumed_context = _ctx("assembly alpha\n")
    result = InvestigationAgent(
        registry=resumed_registry,
        llm_client=fake_llm,
        action_callback=make_facts_action_callback(
            CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
        ),
    ).run(resumed_context)
    assert result.completed
    assert planner_calls == [1]
    assert len(result.tool_calls) == 3


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
    """When the LLM keeps returning an unknown tool even after the repair
    prompt, the transaction exhausts retries and the session blocks with
    ``BLOCK_CODE_INVALID_LLM_OUTPUT`` (the argument/tool validation is now
    handled inside the structured-output transaction's repair loop, not in
    the main execution loop).
    """
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning([{"tool": "shell_exec", "arguments": {"cmd": "rm -rf /"}}])
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_agent_blocks_on_invalid_argument() -> None:
    """When the LLM keeps returning invalid arguments even after the repair
    prompt, the transaction exhausts retries and blocks with
    ``BLOCK_CODE_INVALID_LLM_OUTPUT``.
    """
    idx, ld, reg, ctx = _ctx()
    fake = _llm_returning(
        [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {}}]  # missing required query
    )
    agent = InvestigationAgent(registry=reg, llm_client=fake)
    res = agent.run(ctx)
    assert res.blocked
    assert res.block_code == BLOCK_CODE_INVALID_LLM_OUTPUT


def test_agent_repairs_invalid_arguments_on_second_attempt() -> None:
    """When the LLM returns invalid arguments on the first attempt but
    valid arguments on the repair attempt, the investigation should
    complete successfully (the ``_normalize`` validation error feeds
    into the transaction's repair prompt and the LLM fixes the issue).
    """

    plan_call_count = [0]

    def fake_llm(prompt: str) -> str:
        if "Facts extraction agent" in prompt:
            return json.dumps({"claims": []})
        plan_call_count[0] += 1
        if plan_call_count[0] == 1:
            return json.dumps(
                {"actions": [{"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {}}]}
            )
        return json.dumps(
            {
                "actions": [
                    {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}
                ]
            }
        )

    idx, ld, reg, ctx = _ctx("alpha\nbeta\n")
    agent = InvestigationAgent(registry=reg, llm_client=fake_llm)
    res = agent.run(ctx)
    assert res.completed
    assert not res.blocked
    # The plan LLM was called twice (initial + repair).
    assert plan_call_count[0] == 2


def test_agent_repairs_unknown_tool_on_second_attempt() -> None:
    """When the LLM returns an unknown tool on the first attempt but a
    valid tool on the repair attempt, the investigation should complete.
    """

    plan_call_count = [0]

    def fake_llm(prompt: str) -> str:
        if "Facts extraction agent" in prompt:
            return json.dumps({"claims": []})
        plan_call_count[0] += 1
        if plan_call_count[0] == 1:
            return json.dumps(
                {"actions": [{"tool": "shell_exec", "arguments": {"cmd": "ls"}}]}
            )
        return json.dumps(
            {
                "actions": [
                    {"tool": TOOL_NAME_SEARCH_SOURCE_INDEX, "arguments": {"query": "alpha"}}
                ]
            }
        )

    idx, ld, reg, ctx = _ctx("alpha\nbeta\n")
    agent = InvestigationAgent(registry=reg, llm_client=fake_llm)
    res = agent.run(ctx)
    assert res.completed
    assert not res.blocked
    assert plan_call_count[0] == 2


def test_facts_synthesis_produces_semantic_claims() -> None:
    """The Facts synthesis step should produce claims whose predicates
    match the semantic coverage targets (model_scope, fuel_variant, …)
    so that ``compile_semantic_coverage`` can mark targets as covered.
    """

    # Capture a real span_id from the baseline search so the synthesis
    # output can reference it.
    captured_span_ids: list[str] = []

    def fake_llm(prompt: str) -> str:
        if "Facts extraction agent" in prompt:
            # Extract span_ids from the prompt (they are listed in the
            # "Available source spans" section).
            import re
            span_ids = re.findall(r"span_id=(\S+)", prompt)
            return json.dumps(
                {
                    "claims": [
                        {
                            "predicate": "model_scope",
                            "value": "single_assembly",
                            "source_span_ids": span_ids[:1] if span_ids else [],
                            "subject": "model_scope",
                        }
                    ]
                }
            )
        return json.dumps({"actions": []})

    idx, ld, reg, ctx = _ctx("alpha core beta\n")
    agent = InvestigationAgent(registry=reg, llm_client=fake_llm)
    res = agent.run(ctx)
    assert res.completed
    assert not res.blocked
    semantic_kinds = {
        "model_scope",
        "assembly_count",
        "fuel_variant",
        "has_spacer_grids",
        "localized_insert",
        "core_lattice_size",
        "assembly_type_counts",
    }
    found_semantic = False
    for claim in ld.claims.values():
        if claim.predicate in semantic_kinds:
            found_semantic = True
            break
    assert found_semantic, "synthesis did not produce any semantic-predicate claims"


def test_facts_synthesis_skipped_when_no_spans() -> None:
    """When the tool loop produces no source spans (e.g. empty plan),
    the synthesis step is skipped gracefully without blocking.
    """

    def fake_llm(prompt: str) -> str:
        return json.dumps({"actions": []})

    idx, ld, reg, ctx = _ctx("alpha\nbeta\n")
    agent = InvestigationAgent(registry=reg, llm_client=fake_llm)
    res = agent.run(ctx)
    assert res.completed
    assert not res.blocked


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
