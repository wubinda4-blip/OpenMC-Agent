"""Tests for the run supervisor executor, fake client, and prompt."""

from __future__ import annotations

import json
import pytest

from openmc_agent.run_supervisor import (
    FakeRunSupervisorClient,
    RunSupervisorAction,
    RunSupervisorDecision,
    RunSupervisorInput,
    RunSupervisorMode,
    RunSupervisorResult,
    SupervisorEvidence,
    run_supervisor_decision,
    write_run_supervisor_artifacts,
)
from openmc_agent.run_supervisor_prompts import build_run_supervisor_prompt
from openmc_agent.run_supervisor_policy import (
    compute_allowed_supervisor_actions,
    compute_supervisor_state_fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input(
    *,
    blocking: list[str] | None = None,
    schema_valid: bool | None = True,
    human_confirmation: bool = False,
    renderability: str = "exportable",
    failed_patch_type: str | None = None,
    required_patches: list[str] | None = None,
    patch_status: dict[str, str] | None = None,
    planning_mode: str = "incremental",
    allowed_retry: list[str] | None = None,
    retry_budget_by_patch: dict[str, int] | None = None,
    recent_actions: list[dict] | None = None,
) -> RunSupervisorInput:
    si = RunSupervisorInput(
        decision_id="exec_001",
        current_stage="post_validation",
        planning_mode=planning_mode,
        schema_valid=schema_valid,
        blocking_issue_codes=blocking or [],
        warning_issue_codes=[],
        patch_status=patch_status or {},
        required_patch_types=required_patches or [],
        failed_patch_type=failed_patch_type,
        semantic_findings=[],
        repair_decisions=[],
        renderability=renderability,
        supported_renderer="assembly",
        capability_summary={},
        human_confirmation_required=human_confirmation,
        unresolved_fact_gaps=[],
        allowed_actions=[RunSupervisorAction.STOP],
        allowed_retry_patch_types=allowed_retry or [],
        retry_budget_remaining=2,
        retry_budget_by_patch=retry_budget_by_patch or {},
        recent_actions=recent_actions or [],
        state_fingerprint="",
    )
    si.state_fingerprint = compute_supervisor_state_fingerprint(si)
    si.allowed_actions, _ = compute_allowed_supervisor_actions(si)
    return si


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------

class TestPrompt:

    def test_prompt_contains_safety_constraints(self):
        si = _make_input()
        prompt = build_run_supervisor_prompt(si)
        assert "read-only" in prompt.lower()
        assert "do not modify" in prompt.lower()
        assert "do not generate code" in prompt.lower()
        assert "do not execute tools" in prompt.lower()
        assert "do not invent benchmark facts" in prompt.lower()
        assert "allowed_actions" in prompt

    def test_prompt_lists_allowed_actions(self):
        si = _make_input()
        prompt = build_run_supervisor_prompt(si)
        for a in si.allowed_actions:
            assert a.value in prompt

    def test_prompt_includes_state_summary(self):
        si = _make_input(failed_patch_type="pin_map")
        prompt = build_run_supervisor_prompt(si)
        assert "pin_map" in prompt


# ---------------------------------------------------------------------------
# Fake client tests
# ---------------------------------------------------------------------------

class TestFakeClient:

    def test_fake_clean_plan_chooses_render(self):
        si = _make_input()
        client = FakeRunSupervisorClient()
        result = client.decide(si, prompt="", json_schema={})
        assert result["action"] == "continue_to_render"

    def test_fake_human_confirmation_chooses_escalation(self):
        si = _make_input(human_confirmation=True)
        client = FakeRunSupervisorClient()
        result = client.decide(si, prompt="", json_schema={})
        assert result["action"] == "request_human_confirmation"

    def test_fake_failed_patch_chooses_retry(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
        )
        client = FakeRunSupervisorClient()
        result = client.decide(si, prompt="", json_schema={})
        assert result["action"] == "retry_patch"
        assert result["target_patch_type"] == "pin_map"

    def test_fake_deterministic_no_llm(self):
        si = _make_input()
        client = FakeRunSupervisorClient()
        result = client.decide(si, prompt="", json_schema={})
        assert isinstance(result, dict)
        assert "action" in result


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------

class TestRunSupervisorDecision:

    def test_off_mode_returns_empty(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.OFF)
        assert result.final_action is None
        assert result.accepted is False
        assert result.proposed_decision is None

    def test_advisory_mode_executes_supervisor(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        assert result.proposed_decision is not None
        assert result.final_action is not None
        assert result.accepted is True
        assert result.executed is False  # advisory doesn't execute

    def test_controlled_route_executes(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.CONTROLLED_ROUTE)
        assert result.executed is True

    def test_fallback_when_client_is_none(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        assert result.fallback_used is True
        assert result.supervisor == "deterministic"

    def test_malicious_render_vetoed(self):
        """Client proposes continue_to_render despite blockers — must be vetoed."""
        si = _make_input(blocking=["fatal.error"])

        class MaliciousClient:
            def decide(self, supervisor_input, *, prompt, json_schema):
                return {
                    "decision_id": supervisor_input.decision_id,
                    "action": "continue_to_render",
                    "rationale": "ignoring blockers",
                    "confidence": 0.9,
                }

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.CONTROLLED_ROUTE,
            client=MaliciousClient(),
        )
        assert result.vetoed is True
        assert any("render_with_blocking" in v for v in result.veto_reasons)
        # Fallback should be used.
        assert result.fallback_used is True

    def test_invalid_json_triggers_fallback(self):
        si = _make_input()

        class BadJsonClient:
            def decide(self, supervisor_input, *, prompt, json_schema):
                return "not valid json {{{"

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.ADVISORY,
            client=BadJsonClient(),
        )
        assert result.fallback_used is True
        assert result.final_action is not None

    def test_connection_failure_triggers_fallback(self):
        si = _make_input()

        class FailingClient:
            def decide(self, supervisor_input, *, prompt, json_schema):
                raise ConnectionError("network down")

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.ADVISORY,
            client=FailingClient(),
        )
        assert result.fallback_used is True

    def test_unknown_action_triggers_fallback(self):
        si = _make_input()

        class UnknownActionClient:
            def decide(self, supervisor_input, *, prompt, json_schema):
                return {
                    "decision_id": supervisor_input.decision_id,
                    "action": "teleport_to_mars",
                    "rationale": "unknown",
                    "confidence": 0.5,
                }

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.ADVISORY,
            client=UnknownActionClient(),
        )
        # Unknown action fails schema validation, triggering fallback.
        assert result.fallback_used is True
        assert result.final_action is not None

    def test_no_fallback_when_disabled(self):
        si = _make_input()

        class BadJsonClient:
            def decide(self, supervisor_input, *, prompt, json_schema):
                return "{{invalid"

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.ADVISORY,
            client=BadJsonClient(),
            allow_fallback=False,
        )
        assert result.fallback_used is False
        assert result.proposed_decision is None

    def test_loop_detection_vetoes(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
            recent_actions=[
                {"state_fingerprint": "", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
                {"state_fingerprint": "", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
            ],
        )
        # Fix fingerprint in recent_actions to match current.
        for ra in si.recent_actions:
            ra["state_fingerprint"] = si.state_fingerprint

        result = run_supervisor_decision(
            si,
            mode=RunSupervisorMode.ADVISORY,
        )
        # The fake client will propose retry_patch, but loop detection should veto.
        assert result.vetoed is True or result.final_action != RunSupervisorAction.RETRY_PATCH

    def test_result_has_duration(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    def test_advisory_not_executed(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        assert result.executed is False

    def test_controlled_route_accepted_and_executed(self):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.CONTROLLED_ROUTE)
        assert result.accepted is True
        assert result.executed is True


# ---------------------------------------------------------------------------
# Artifact tests
# ---------------------------------------------------------------------------

class TestArtifacts:

    def test_artifacts_written(self, tmp_path):
        si = _make_input()
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        write_run_supervisor_artifacts(str(tmp_path / "sup"), si, result)
        import os
        assert os.path.exists(tmp_path / "sup" / "input.json")
        assert os.path.exists(tmp_path / "sup" / "result.json")
        assert os.path.exists(tmp_path / "sup" / "action_history.json")

    def test_multi_round_artifacts(self, tmp_path):
        si = _make_input(recent_actions=[{"state_fingerprint": "x", "proposed_action": "stop"}])
        result = run_supervisor_decision(si, mode=RunSupervisorMode.ADVISORY)
        write_run_supervisor_artifacts(str(tmp_path / "sup"), si, result)
        import os
        assert os.path.exists(tmp_path / "sup" / "decisions" / "001" / "input.json")
