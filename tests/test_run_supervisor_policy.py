"""Tests for run supervisor schemas, policy, fingerprint, loop detection."""

from __future__ import annotations

import pytest

from openmc_agent.run_supervisor import (
    RunSupervisorAction,
    RunSupervisorDecision,
    RunSupervisorInput,
    RunSupervisorMode,
    RunSupervisorResult,
    SupervisorEvidence,
)
from openmc_agent.run_supervisor_policy import (
    compute_allowed_supervisor_actions,
    compute_supervisor_state_fingerprint,
    detect_no_progress,
    detect_supervisor_loop,
    determine_deterministic_supervisor_action,
    validate_supervisor_decision,
    build_run_supervisor_input,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input(
    *,
    allowed_actions: list[RunSupervisorAction] | None = None,
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
    semantic_findings: list[dict] | None = None,
    repair_decisions: list[dict] | None = None,
) -> RunSupervisorInput:
    if allowed_actions is None:
        si = _make_input(allowed_actions=[RunSupervisorAction.STOP], **{
            k: v for k, v in locals().items() if k != "allowed_actions" and v is not None
        })
        si.allowed_actions, _ = compute_allowed_supervisor_actions(si)
        return si
    si = RunSupervisorInput(
        decision_id="test_001",
        current_stage="post_validation",
        planning_mode=planning_mode,
        schema_valid=schema_valid,
        blocking_issue_codes=blocking or [],
        warning_issue_codes=[],
        patch_status=patch_status or {},
        required_patch_types=required_patches or [],
        failed_patch_type=failed_patch_type,
        semantic_findings=semantic_findings or [],
        repair_decisions=repair_decisions or [],
        renderability=renderability,
        supported_renderer="assembly",
        capability_summary={},
        human_confirmation_required=human_confirmation,
        unresolved_fact_gaps=[],
        allowed_actions=allowed_actions,
        allowed_retry_patch_types=allowed_retry or [],
        retry_budget_remaining=2,
        retry_budget_by_patch=retry_budget_by_patch or {},
        recent_actions=recent_actions or [],
        state_fingerprint="abc123",
    )
    si.state_fingerprint = compute_supervisor_state_fingerprint(si)
    si.allowed_actions, _ = compute_allowed_supervisor_actions(si)
    return si


def _make_decision(
    action: RunSupervisorAction = RunSupervisorAction.STOP,
    target: str | None = None,
    decision_id: str = "test_001",
    confidence: float = 0.8,
) -> RunSupervisorDecision:
    return RunSupervisorDecision(
        decision_id=decision_id,
        action=action,
        target_patch_type=target,
        rationale="test",
        evidence=[SupervisorEvidence(source_type="workflow_history", summary="test")],
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestRunSupervisorSchemas:

    def test_action_enum_values(self):
        assert RunSupervisorAction.CONTINUE_TO_RENDER.value == "continue_to_render"
        assert RunSupervisorAction.CONTINUE_PATCH_GENERATION.value == "continue_patch_generation"
        assert RunSupervisorAction.RETRY_PATCH.value == "retry_patch"
        assert RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION.value == "request_human_confirmation"
        assert RunSupervisorAction.DOWNGRADE_TO_SKELETON.value == "downgrade_to_skeleton"
        assert RunSupervisorAction.STOP.value == "stop"

    def test_mode_enum_values(self):
        assert RunSupervisorMode.OFF.value == "off"
        assert RunSupervisorMode.ADVISORY.value == "advisory"
        assert RunSupervisorMode.CONTROLLED_ROUTE.value == "controlled_route"

    def test_input_serializes_correctly(self):
        si = RunSupervisorInput(
            decision_id="x",
            current_stage="test",
            allowed_actions=[RunSupervisorAction.STOP],
            retry_budget_remaining=0,
            state_fingerprint="fp",
        )
        assert si.decision_id == "x"
        assert si.allowed_actions == [RunSupervisorAction.STOP]

    def test_decision_confidence_bounds(self):
        with pytest.raises(Exception):
            _make_decision(confidence=1.5)
        with pytest.raises(Exception):
            _make_decision(confidence=-0.1)

    def test_result_defaults(self):
        r = RunSupervisorResult(
            decision_id="x",
            mode=RunSupervisorMode.OFF,
            state_fingerprint="fp",
        )
        assert r.accepted is False
        assert r.vetoed is False
        assert r.fallback_used is False

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            RunSupervisorDecision(
                decision_id="x",
                action=RunSupervisorAction.STOP,
                rationale="x",
                confidence=0.5,
                unexpected_field=True,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Allowed action computation tests
# ---------------------------------------------------------------------------

class TestComputeAllowedActions:

    def test_clean_exportable_plan_allows_render_and_stop(self):
        si = _make_input()
        assert RunSupervisorAction.CONTINUE_TO_RENDER in si.allowed_actions
        assert RunSupervisorAction.STOP in si.allowed_actions

    def test_blocking_issue_prevents_render(self):
        si = _make_input(blocking=["some.error"])
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions
        assert RunSupervisorAction.STOP in si.allowed_actions

    def test_human_confirmation_prevents_render(self):
        si = _make_input(human_confirmation=True)
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions
        assert RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION in si.allowed_actions

    def test_renderability_none_prevents_render(self):
        si = _make_input(renderability="none")
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions

    def test_schema_invalid_prevents_render(self):
        si = _make_input(schema_valid=False)
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions

    def test_required_patch_pending_prevents_render(self):
        si = _make_input(
            required_patches=["pin_map", "axial_layers"],
            patch_status={"pin_map": "valid"},
        )
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions
        assert RunSupervisorAction.CONTINUE_PATCH_GENERATION in si.allowed_actions

    def test_all_required_patches_done_allows_render(self):
        si = _make_input(
            required_patches=["pin_map"],
            patch_status={"pin_map": "valid"},
        )
        assert RunSupervisorAction.CONTINUE_TO_RENDER in si.allowed_actions

    def test_retry_allowed_when_failed_and_budget(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
        )
        assert RunSupervisorAction.RETRY_PATCH in si.allowed_actions

    def test_retry_blocked_when_budget_zero(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=[],
            retry_budget_by_patch={},
            blocking=["patch.pin_map.error"],
        )
        assert RunSupervisorAction.RETRY_PATCH not in si.allowed_actions

    def test_skeleton_allowed_when_unsupported(self):
        si = _make_input(renderability="skeleton")
        assert RunSupervisorAction.DOWNGRADE_TO_SKELETON in si.allowed_actions

    def test_stop_always_allowed(self):
        for renderability in ["exportable", "skeleton", "none"]:
            si = _make_input(renderability=renderability)
            assert RunSupervisorAction.STOP in si.allowed_actions

    def test_non_incremental_blocks_patch_generation(self):
        si = _make_input(planning_mode="monolithic", required_patches=["pin_map"])
        assert RunSupervisorAction.CONTINUE_PATCH_GENERATION not in si.allowed_actions


# ---------------------------------------------------------------------------
# Veto tests
# ---------------------------------------------------------------------------

class TestValidateDecision:

    def test_clean_decision_accepted(self):
        si = _make_input()
        d = _make_decision(RunSupervisorAction.CONTINUE_TO_RENDER)
        vetoes = validate_supervisor_decision(d, si)
        assert vetoes == []

    def test_render_with_blocker_vetoed(self):
        si = _make_input(blocking=["some.error"])
        d = _make_decision(RunSupervisorAction.CONTINUE_TO_RENDER)
        vetoes = validate_supervisor_decision(d, si)
        assert any("render_with_blocking" in v for v in vetoes)

    def test_human_confirmation_bypass_vetoed(self):
        si = _make_input(human_confirmation=True)
        d = _make_decision(RunSupervisorAction.CONTINUE_TO_RENDER)
        vetoes = validate_supervisor_decision(d, si)
        assert any("human_confirmation_bypass" in v for v in vetoes)

    def test_action_not_in_allowed_vetoed(self):
        si = _make_input(human_confirmation=True)
        d = _make_decision(RunSupervisorAction.CONTINUE_TO_RENDER)
        vetoes = validate_supervisor_decision(d, si)
        assert any("action_not_in_allowed" in v for v in vetoes)

    def test_retry_without_target_vetoed(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
        )
        d = _make_decision(RunSupervisorAction.RETRY_PATCH, target=None)
        vetoes = validate_supervisor_decision(d, si)
        assert any("no_target_patch_type" in v for v in vetoes)

    def test_retry_wrong_target_vetoed(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
        )
        d = _make_decision(RunSupervisorAction.RETRY_PATCH, target="wrong_patch")
        vetoes = validate_supervisor_decision(d, si)
        assert any("invalid_patch_target" in v for v in vetoes)

    def test_retry_budget_exhausted_vetoed(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=[],
            retry_budget_by_patch={},
            blocking=["patch.pin_map.error"],
        )
        d = _make_decision(RunSupervisorAction.RETRY_PATCH, target="pin_map")
        vetoes = validate_supervisor_decision(d, si)
        assert any("retry_budget_exhausted" in v for v in vetoes)

    def test_decision_id_mismatch_vetoed(self):
        si = _make_input()
        d = _make_decision(RunSupervisorAction.STOP, decision_id="wrong_id")
        vetoes = validate_supervisor_decision(d, si)
        assert any("decision_id_mismatch" in v for v in vetoes)


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------

class TestStateFingerprint:

    def test_fingerprint_stable(self):
        si1 = _make_input()
        si2 = _make_input()
        # Same state → same fingerprint.
        assert si1.state_fingerprint == si2.state_fingerprint

    def test_fingerprint_changes_with_state(self):
        si1 = _make_input()
        si2 = _make_input(blocking=["new_error"])
        assert si1.state_fingerprint != si2.state_fingerprint

    def test_fingerprint_ignores_recent_actions(self):
        si1 = _make_input()
        si2 = _make_input(recent_actions=[{"action": "stop"}])
        assert si1.state_fingerprint == si2.state_fingerprint

    def test_fingerprint_length(self):
        si = _make_input()
        assert len(si.state_fingerprint) == 16


# ---------------------------------------------------------------------------
# Loop detection tests
# ---------------------------------------------------------------------------

class TestLoopDetection:

    def test_no_loop_on_first_action(self):
        assert detect_supervisor_loop(
            fingerprint="fp1",
            proposed_action=RunSupervisorAction.RETRY_PATCH,
            target_patch_type="pin_map",
            history=[],
        ) is False

    def test_loop_after_max_repeats(self):
        history = [
            {"state_fingerprint": "fp1", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
            {"state_fingerprint": "fp1", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
        ]
        assert detect_supervisor_loop(
            fingerprint="fp1",
            proposed_action=RunSupervisorAction.RETRY_PATCH,
            target_patch_type="pin_map",
            history=history,
        ) is True

    def test_different_fingerprint_no_loop(self):
        history = [
            {"state_fingerprint": "fp1", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
            {"state_fingerprint": "fp2", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
        ]
        assert detect_supervisor_loop(
            fingerprint="fp1",
            proposed_action=RunSupervisorAction.RETRY_PATCH,
            target_patch_type="pin_map",
            history=history,
        ) is False

    def test_different_target_no_loop(self):
        history = [
            {"state_fingerprint": "fp1", "proposed_action": "retry_patch", "target_patch_type": "pin_map"},
            {"state_fingerprint": "fp1", "proposed_action": "retry_patch", "target_patch_type": "axial_layers"},
        ]
        assert detect_supervisor_loop(
            fingerprint="fp1",
            proposed_action=RunSupervisorAction.RETRY_PATCH,
            target_patch_type="pin_map",
            history=history,
        ) is False

    def test_no_progress_detection(self):
        history = [
            {"state_fingerprint": "fp2"},
            {"state_fingerprint": "fp1"},
            {"state_fingerprint": "fp1"},
        ]
        assert detect_no_progress("fp1", history) == 2
        assert detect_no_progress("fp2", history) == 0


# ---------------------------------------------------------------------------
# Deterministic fallback tests
# ---------------------------------------------------------------------------

class TestDeterministicFallback:

    def test_clean_plan_chooses_render(self):
        si = _make_input()
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.CONTINUE_TO_RENDER

    def test_human_confirmation_chooses_escalation(self):
        si = _make_input(human_confirmation=True)
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION

    def test_failed_patch_chooses_retry(self):
        si = _make_input(
            failed_patch_type="pin_map",
            allowed_retry=["pin_map"],
            retry_budget_by_patch={"pin_map": 2},
            blocking=["patch.pin_map.error"],
        )
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.RETRY_PATCH
        assert d.target_patch_type == "pin_map"

    def test_pending_patches_chooses_continue(self):
        si = _make_input(
            required_patches=["pin_map", "axial_layers"],
            patch_status={"pin_map": "valid"},
        )
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.CONTINUE_PATCH_GENERATION

    def test_unsupported_chooses_skeleton(self):
        si = _make_input(renderability="skeleton")
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.DOWNGRADE_TO_SKELETON

    def test_dead_end_chooses_stop(self):
        si = _make_input(
            schema_valid=False,
            renderability="none",
            blocking=["fatal_error"],
        )
        d = determine_deterministic_supervisor_action(si)
        assert d.action == RunSupervisorAction.STOP


# ---------------------------------------------------------------------------
# build_run_supervisor_input tests
# ---------------------------------------------------------------------------

class TestBuildSupervisorInput:

    def test_from_clean_workflow_state(self):
        state = {
            "validation_report": {"is_valid": True, "issues": []},
            "simulation_plan": {
                "capability_report": {
                    "renderability": "exportable",
                    "supported_renderer": "assembly",
                    "is_executable": True,
                },
            },
            "planning_mode_decision": {"mode": "incremental"},
            "plan_build_state": {
                "patch_status": {"pin_map": "valid"},
                "component_tasks": [{"patch_type": "pin_map"}],
            },
            "incremental_execution_result": {"ok": True, "summary": {}},
        }
        si = build_run_supervisor_input(state)
        assert si.schema_valid is True
        assert si.renderability == "exportable"
        assert si.planning_mode == "incremental"
        assert RunSupervisorAction.CONTINUE_TO_RENDER in si.allowed_actions

    def test_from_blocked_workflow_state(self):
        state = {
            "validation_report": {
                "is_valid": False,
                "issues": [
                    {"severity": "error", "code": "assembly.missing_patch"},
                ],
            },
            "simulation_plan": {
                "capability_report": {
                    "renderability": "exportable",
                    "supported_renderer": "assembly",
                },
            },
            "planning_mode_decision": {"mode": "incremental"},
            "plan_build_state": {},
            "incremental_execution_result": {"ok": False, "summary": {"failed_patch_type": "pin_map"}},
        }
        si = build_run_supervisor_input(state)
        assert si.schema_valid is False
        assert "assembly.missing_patch" in si.blocking_issue_codes
        assert RunSupervisorAction.CONTINUE_TO_RENDER not in si.allowed_actions

    def test_compact_input_excludes_secrets(self):
        state = {
            "validation_report": {"is_valid": True, "issues": []},
            "simulation_plan": {
                "capability_report": {"renderability": "exportable"},
                "settings": {"cross_sections": "/secret/path"},
            },
            "planning_mode_decision": {"mode": "incremental"},
            "plan_build_state": {},
            "incremental_execution_result": {"ok": True, "summary": {}},
        }
        si = build_run_supervisor_input(state)
        dumped = si.model_dump(mode="json")
        dumped_str = str(dumped)
        assert "/secret/path" not in dumped_str
        assert "cross_sections" not in dumped_str
