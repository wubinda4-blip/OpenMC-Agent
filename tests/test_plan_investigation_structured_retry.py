"""Integration coverage for investigation planner structured retries."""

from __future__ import annotations

import json

from openmc_agent.plan_investigation.agent import InvestigationAgent, InvestigationContext
from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_registry import build_default_step2_registry


def _context() -> tuple[InvestigationContext, object]:
    index = build_source_index(
        text="full core\n3x3 lattice\n", title="t", source_kind=SourceKind.USER_REQUIREMENT
    )
    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[index])
    registry = build_default_step2_registry()
    return InvestigationContext(
        requirement_text="full core\n3x3 lattice\n",
        patch_type="facts",
        available_tools=tuple(registry.list_tools()),
        source_indexes={index.document.source_id: index},
        ledger=ledger,
    ), registry


def test_investigation_planner_retries_non_json_and_records_hash() -> None:
    context, registry = _context()
    plan_responses = iter(["not json", json.dumps({"actions": [], "summary": "repaired"})])

    def llm(prompt: str) -> str:
        if "Facts extraction agent" in prompt:
            return json.dumps({"claims": []})
        return next(plan_responses)

    agent = InvestigationAgent(registry=registry, llm_client=llm)
    result = agent.run(context)
    assert result.completed
    assert not result.blocked
    # The plan transaction used 2 calls (initial + retry).
    # The synthesis transaction may add more; we only assert the plan
    # portion here.  planner_calls now includes synthesis calls, so we
    # check it is at least 2.
    assert result.planner_calls >= 2
    assert result.schema_retries == 1
    assert result.planner_input_payload_hash
