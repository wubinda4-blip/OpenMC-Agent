"""Tests for the Phase 8A Step 4 executor wiring.

Covers:
- off mode is byte-identical to legacy (no LLM call, no tool call, no
  artifact, prompt unchanged).
- controlled mode runs investigation BEFORE Facts generate_patch and
  injects evidence into the patch prompt.
- blocked controlled investigation prevents the Facts patch LLM call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_investigation.executor_injection import (
    BLOCK_CODE_FACTS_BLOCKED,
    EVENT_INVESTIGATION_BLOCKED,
    EVENT_INVESTIGATION_COMPLETED,
    EVENT_INVESTIGATION_EVIDENCE_INJECTED,
)
from openmc_agent.plan_investigation.runner import (
    PlanInvestigationConfig,
    PlanInvestigationMode,
)
from openmc_agent.plan_investigation.agent import InvestigationBudget


CANARY_TEXT = """# Reactor Problem

The model represents a full 3 by 3 core.
The layout is a 3x3 lattice of assemblies.
Fuel enrichment is 3.5 wt%.
"""


def _state():
    return PlanBuildState.model_validate(
        {
            "state_id": "pbs_test",
            "requirement_text": CANARY_TEXT,
            "planning_mode": "incremental",
        }
    )


def _patch_llm_recorder():
    """Patch LLM that records each call and returns a minimal FactsPatch."""
    calls: list[str] = []

    def client(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(
            {
                "patch_type": "facts",
                "geometry_type": "lattice",
                "model_scope": "single_assembly",
            }
        )

    return client, calls


def _investigator(actions):
    """Build a fake investigator that returns the supplied action list."""
    def client(prompt: str) -> str:
        return json.dumps({"actions": actions, "summary": "ok"})

    return client


def _good_investigator_actions():
    return [
        {"tool": "inspect_requirement_structure", "arguments": {}},
        {"tool": "inspect_patch_schema", "arguments": {"patch_type": "facts"}},
        {"tool": "search_source_index", "arguments": {"query": "full core"}},
    ]


# ---------------------------------------------------------------------------
# A. Off mode (default) — zero impact
# ---------------------------------------------------------------------------


def test_off_mode_no_investigation_calls() -> None:
    """When mode=off (default), the investigator client is never invoked."""
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    investigator_invocations = []

    def investigator(prompt):
        investigator_invocations.append(prompt)
        return '{"actions": []}'

    run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.OFF),
        plan_investigation_client=investigator,
    )
    assert investigator_invocations == []


def test_off_mode_no_investigation_artifact() -> None:
    """off mode never writes investigation artifacts."""
    state = _state()
    patch_client, _ = _patch_llm_recorder()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        run_incremental_planning(
            requirement=CANARY_TEXT,
            state=state,
            llm_client=patch_client,
            task_order=["facts"],
            plan_loop_policy={"mode": "off"},
            plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.OFF),
            plan_investigation_client=lambda p: '{"actions": []}',
            plan_investigation_output_dir=td,
        )
        # No investigation_session.json should exist.
        assert not (Path(td) / "workflow" / "investigation" / "investigation_session.json").exists()


# ---------------------------------------------------------------------------
# B. Controlled — investigation runs BEFORE Facts generate_patch
# ---------------------------------------------------------------------------


def test_controlled_runs_investigation_before_facts_patch() -> None:
    """The investigator must run BEFORE the Facts patch LLM is invoked."""
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    investigator_calls: list[str] = []

    def investigator(prompt):
        investigator_calls.append(prompt)
        return json.dumps({"actions": _good_investigator_actions(), "summary": "ok"})

    run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=investigator,
    )
    # Investigator ran at least once.
    assert len(investigator_calls) >= 1
    # Patch LLM was called (Facts generation proceeded).
    assert len(patch_calls) >= 1
    # The event log records investigation completed + evidence injected.
    event_types = [e.event_type for e in state.build_log]
    assert EVENT_INVESTIGATION_COMPLETED in event_types
    assert EVENT_INVESTIGATION_EVIDENCE_INJECTED in event_types


def test_controlled_injects_evidence_into_patch_prompt() -> None:
    """The Facts patch prompt must contain the evidence section when
    investigation produced source-backed claims.
    """
    state = _state()
    patch_calls: list[str] = []

    def patch_client(prompt: str) -> str:
        patch_calls.append(prompt)
        return json.dumps(
            {
                "patch_type": "facts",
                "geometry_type": "lattice",
                "model_scope": "single_assembly",
            }
        )

    investigator = _investigator(_good_investigator_actions())
    run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=investigator,
    )
    assert len(patch_calls) >= 1
    facts_prompt = next((p for p in patch_calls if "patch_type" in p), patch_calls[0])
    assert "Evidence Claims" in facts_prompt
    assert "use as constraints" in facts_prompt.lower()


# ---------------------------------------------------------------------------
# C. Controlled barrier — blocked investigation prevents Facts patch LLM
# ---------------------------------------------------------------------------


def test_controlled_blocks_when_investigator_missing() -> None:
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    result = run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=None,
    )
    assert not result.ok
    # Facts patch LLM was NEVER called.
    assert patch_calls == []
    # The disposition is BLOCKED_BY_INVESTIGATION:facts.
    detail = result.plan_loop_outcome.get("detail", "")
    assert "BLOCKED_BY_INVESTIGATION:facts" in detail
    # Issue code surfaces in the summary.
    assert "planning.investigation_facts_blocked" in result.summary.get("issue_codes", [])


def test_controlled_blocks_on_invalid_llm_output() -> None:
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    result = run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=lambda p: "not json",
    )
    assert not result.ok
    assert patch_calls == []
    assert "BLOCKED_BY_INVESTIGATION:facts" in result.plan_loop_outcome.get("detail", "")


def test_controlled_blocks_on_unknown_tool() -> None:
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    result = run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=lambda p: json.dumps(
            {"actions": [{"tool": "shell_exec", "arguments": {"cmd": "rm -rf /"}}]}
        ),
    )
    assert not result.ok
    assert patch_calls == []


def test_controlled_blocks_on_insufficient_coverage() -> None:
    """With the Step 5 mandatory baseline, Python always runs the three
    required tools (inspect_patch_schema, inspect_requirement_structure,
    search_source_index) even when the LLM returns an empty action list.
    So an empty LLM action list no longer blocks — the mandatory
    baseline satisfies the coverage contract.  The Facts patch LLM
    proceeds.
    """
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    result = run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.CONTROLLED, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=lambda p: '{"actions": [], "summary": "noop"}',
    )
    # Mandatory baseline ran the required tools → Facts patch LLM called.
    assert len(patch_calls) >= 1
    # No investigation block.
    detail = (result.plan_loop_outcome or {}).get("detail", "")
    assert "BLOCKED_BY_INVESTIGATION" not in detail


# ---------------------------------------------------------------------------
# D. Advisory mode — failures are non-blocking
# ---------------------------------------------------------------------------


def test_advisory_continues_when_investigator_missing() -> None:
    state = _state()
    patch_client, patch_calls = _patch_llm_recorder()
    result = run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.ADVISORY, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=None,
    )
    # Advisory mode tolerates the missing investigator and proceeds to
    # the Facts patch LLM.
    assert len(patch_calls) >= 1
    # No investigation_block in the outcome (when present).
    outcome = result.plan_loop_outcome or {}
    assert "BLOCKED_BY_INVESTIGATION" not in (outcome.get("detail") or "")


def test_advisory_failure_does_not_mark_investigation_completed() -> None:
    state = _state()
    patch_client, _ = _patch_llm_recorder()
    run_incremental_planning(
        requirement=CANARY_TEXT,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.ADVISORY, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=lambda p: "not json",
    )
    # The build log records a warning (advisory investigation skipped /
    # failed) but NOT a completed event.
    event_types = [e.event_type for e in state.build_log]
    assert EVENT_INVESTIGATION_COMPLETED not in event_types


def test_advisory_empty_evidence_does_not_change_prompt() -> None:
    """When the advisory investigator returns no evidence AND the
    requirement has no source-backed claims from the mandatory
    baseline, the patch prompt must NOT contain an Evidence Claims
    section.  Note: with the Step 5 mandatory baseline, the baseline's
    search_source_index tool usually produces at least one claim for
    any non-trivial requirement, so this test uses a minimal
    requirement that produces no search hits.
    """
    state = _state()
    patch_calls: list[str] = []

    def patch_client(prompt: str) -> str:
        patch_calls.append(prompt)
        return json.dumps(
            {
                "patch_type": "facts",
                "geometry_type": "lattice",
                "model_scope": "single_assembly",
            }
        )

    # Use a requirement with no searchable keywords → mandatory baseline
    # search returns 0 hits → no evidence → no prompt change.
    minimal_req = "x"
    run_incremental_planning(
        requirement=minimal_req,
        state=state,
        llm_client=patch_client,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
        plan_investigation_config=PlanInvestigationConfig(mode=PlanInvestigationMode.ADVISORY, budget=InvestigationBudget(max_tool_calls=10)),
        plan_investigation_client=lambda p: '{"actions": [], "summary": "noop"}',
    )
    assert len(patch_calls) >= 1
    # The Facts prompt does not include the evidence section because no
    # evidence claims were produced (minimal requirement, no search hits).
    assert not any("Evidence Claims" in p for p in patch_calls)
