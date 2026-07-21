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
    planner_calls = {"count": 0}

    def client(_prompt: str) -> str:
        planner_calls["count"] += 1
        return json.dumps({"actions": []})

    result = InvestigationAgent(registry=registry, llm_client=client).run(context)
    assert result.completed
    assert not result.blocked
    assert planner_calls["count"] == 0
    assert result.planner_calls == 0
    assert result.skipped_actions == ("planner",)
    assert len(result.tool_calls) == 3  # mandatory baseline only
    assert result.skipped_action_reason == "skipped_after_coverage_complete"