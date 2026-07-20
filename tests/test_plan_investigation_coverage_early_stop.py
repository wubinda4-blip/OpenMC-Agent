"""Coverage-complete investigation plans stop before redundant tools."""

from __future__ import annotations

import json

from openmc_agent.plan_investigation import agent as agent_module
from openmc_agent.plan_investigation.agent import InvestigationAgent, InvestigationBudget
from openmc_agent.plan_investigation.semantic_coverage import SemanticCoverage
from tests.test_plan_investigation_agent import _ctx


def test_coverage_complete_skips_remaining_actions(monkeypatch) -> None:
    _idx, _ledger, registry, context = _ctx()
    context = context.model_copy(
        update={"budget": InvestigationBudget(max_tool_calls=3, max_evidence_claims=100)}
    )
    monkeypatch.setattr(
        agent_module,
        "compile_semantic_coverage",
        lambda **_kwargs: SemanticCoverage(
            patch_type="facts",
            total_targets=1,
            covered_targets=1,
            source_backed_targets=1,
            coverage_complete=True,
        ),
    )
    client = lambda _prompt: json.dumps(
        {
            "actions": [
                {"tool": "search_source_index", "arguments": {"query": "unused"}},
                {"tool": "search_source_index", "arguments": {"query": "also_unused"}},
            ]
        }
    )
    result = InvestigationAgent(registry=registry, llm_client=client).run(context)
    assert result.completed
    assert not result.blocked
    assert result.skipped_actions == ("search_source_index", "search_source_index")
    assert len(result.tool_calls) == 3  # mandatory baseline only

    assert result.skipped_action_reason == "skipped_after_coverage_complete"