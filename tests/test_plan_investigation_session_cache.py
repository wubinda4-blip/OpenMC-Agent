"""Tests for the Phase 8A Step 4 session cache and shared-state lifecycle."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_investigation.executor_injection import (
    EVENT_INVESTIGATION_CACHE_REUSED,
    EVENT_INVESTIGATION_COMPLETED,
    InvestigationSessionCache,
    SessionCacheKey,
    run_facts_investigation_stage,
)
from openmc_agent.plan_investigation.runner import (
    PlanInvestigationConfig,
    PlanInvestigationMode,
    build_investigation_ledger,
    build_investigation_source_index,
)


def _investigator_returning_valid_actions():
    def client(prompt):
        return json.dumps({
            "actions": [
                {"tool": "inspect_requirement_structure", "arguments": {}},
                {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
                {"tool": "search_source_index", "arguments": {"query": "x"}},
            ]
        })
    return client


def test_session_cache_skips_repeat_llm_call() -> None:
    """A second call with the same cache key reuses the cached payloads
    and does NOT invoke the LLM again.
    """
    requirement = "# Demo\nThe model represents a full core.\n"
    source_index = build_investigation_source_index(requirement)
    ledger = build_investigation_ledger(
        requirement_text=requirement,
        source_indexes={source_index.document.source_id: source_index},
    )
    cache = InvestigationSessionCache()
    llm_calls = []

    def counting_client(prompt):
        llm_calls.append(prompt)
        return json.dumps({
            "actions": [
                {"tool": "inspect_requirement_structure", "arguments": {}},
                {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
                {"tool": "search_source_index", "arguments": {"query": "core"}},
            ]
        })

    config = PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED)

    # First run.
    outcome1 = run_facts_investigation_stage(
        requirement=requirement,
        config=config,
        llm_client=counting_client,
        session_cache=cache,
        shared_source_index=source_index,
        shared_ledger=ledger,
    )
    assert outcome1.completed
    first_call_count = len(llm_calls)
    assert first_call_count >= 1

    # Second run with the SAME cache: must NOT call the LLM.
    outcome2 = run_facts_investigation_stage(
        requirement=requirement,
        config=config,
        llm_client=counting_client,
        session_cache=cache,
        shared_source_index=source_index,
        shared_ledger=ledger,
    )
    assert outcome2.cache_reused is True
    assert len(llm_calls) == first_call_count  # no new LLM call


def test_session_cache_invalidates_on_requirement_change() -> None:
    """A different requirement produces a different cache key, so the
    LLM is invoked again.
    """
    cache = InvestigationSessionCache()
    llm_calls = []

    def counting_client(prompt):
        llm_calls.append(prompt)
        return json.dumps({
            "actions": [
                {"tool": "inspect_requirement_structure", "arguments": {}},
                {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
                {"tool": "search_source_index", "arguments": {"query": "core"}},
            ]
        })

    config = PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED)
    req1 = "First requirement with full core."
    req2 = "Completely different second requirement."

    run_facts_investigation_stage(
        requirement=req1, config=config, llm_client=counting_client, session_cache=cache,
    )
    count_after_first = len(llm_calls)
    run_facts_investigation_stage(
        requirement=req2, config=config, llm_client=counting_client, session_cache=cache,
    )
    assert len(llm_calls) > count_after_first  # second run invoked the LLM


def test_session_cache_invalidates_on_mode_change() -> None:
    cache = InvestigationSessionCache()
    llm_calls = []

    def counting_client(prompt):
        llm_calls.append(prompt)
        return json.dumps({
            "actions": [
                {"tool": "inspect_requirement_structure", "arguments": {}},
                {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
                {"tool": "search_source_index", "arguments": {"query": "core"}},
            ]
        })

    req = "Same requirement."
    run_facts_investigation_stage(
        requirement=req,
        config=PlanInvestigationConfig(mode=PlanInvestigationMode.ADVISORY),
        llm_client=counting_client,
        session_cache=cache,
    )
    count_after_first = len(llm_calls)
    run_facts_investigation_stage(
        requirement=req,
        config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED),
        llm_client=counting_client,
        session_cache=cache,
    )
    assert len(llm_calls) > count_after_first


def test_session_cache_key_is_deterministic() -> None:
    """Same inputs → same cache key hash."""
    requirement = "demo"
    source_index = build_investigation_source_index(requirement)
    config = PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED)
    from openmc_agent.plan_investigation.tool_registry import build_default_step2_registry
    from openmc_agent.plan_investigation.policy import default_policy_registry

    reg = build_default_step2_registry()
    pol = default_policy_registry()

    # Build the same key twice via the internal helper.
    from openmc_agent.plan_investigation.executor_injection import _build_cache_key

    key1 = _build_cache_key(
        requirement=requirement, source_index=source_index, config=config,
        registry=reg, policy_registry=pol,
    )
    key2 = _build_cache_key(
        requirement=requirement, source_index=source_index, config=config,
        registry=reg, policy_registry=pol,
    )
    assert key1.to_hash() == key2.to_hash()


def test_cache_reuse_event_recorded() -> None:
    """When the cache hits, an investigation_cache_reused event is recorded."""
    requirement = "demo requirement with full core scope."
    source_index = build_investigation_source_index(requirement)
    ledger = build_investigation_ledger(
        requirement_text=requirement,
        source_indexes={source_index.document.source_id: source_index},
    )
    cache = InvestigationSessionCache()
    events: list[tuple[str, str, dict]] = []

    def add_event(evt, msg, data):
        events.append((evt, msg, data))

    def client(prompt):
        return json.dumps({
            "actions": [
                {"tool": "inspect_requirement_structure", "arguments": {}},
                {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
                {"tool": "search_source_index", "arguments": {"query": "core"}},
            ]
        })

    config = PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED)
    run_facts_investigation_stage(
        requirement=requirement, config=config, llm_client=client,
        session_cache=cache, shared_source_index=source_index,
        shared_ledger=ledger, add_event=add_event,
    )
    events.clear()
    run_facts_investigation_stage(
        requirement=requirement, config=config, llm_client=client,
        session_cache=cache, shared_source_index=source_index,
        shared_ledger=ledger, add_event=add_event,
    )
    event_types = [e[0] for e in events]
    assert EVENT_INVESTIGATION_CACHE_REUSED in event_types
