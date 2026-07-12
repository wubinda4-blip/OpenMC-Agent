"""Tests for expert-feedback routing priority (P0-D5A Sections 3, 11)."""
from __future__ import annotations

from openmc_agent.run_supervisor import RunSupervisorAction
from openmc_agent.run_supervisor_policy import (
    RunSupervisorInput,
    determine_deterministic_supervisor_action,
)


def _input(**overrides) -> RunSupervisorInput:
    base = dict(
        decision_id="t",
        current_stage="assess",
        schema_valid=True,
        planning_mode="monolithic",
        blocking_issue_codes=[],
        warning_issue_codes=[],
        patch_status={},
        required_patch_types=[],
        human_confirmation_required=False,
        renderability="runnable",
        semantic_findings=[],
        repair_decisions=[],
        capability_summary={},
        unresolved_fact_gaps=[],
        allowed_actions=list(RunSupervisorAction),
        allowed_retry_patch_types=[],
        retry_budget_remaining=2,
        retry_budget_by_patch={},
        recent_actions=[],
        state_fingerprint="fp",
        metadata={},
    )
    base.update(overrides)
    return RunSupervisorInput(**base)  # type: ignore[arg-type]


def test_structural_blocker_routes_to_repair_or_skeleton_not_human_confirmation() -> None:
    """When a structural blocker + material assumptions coexist, the supervisor
    does NOT request human confirmation; it routes to repair/skeleton."""
    inp = _input(
        blocking_issue_codes=["lattice_transform.replacement_universe_missing"],
        human_confirmation_required=True,
        renderability="skeleton",
    )
    decision = determine_deterministic_supervisor_action(inp)
    assert decision.action != RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION
    assert "structural_blocker_precedes_human_confirmation" in decision.rationale


def test_request_human_confirmation_cannot_override_structural_blocker() -> None:
    inp = _input(
        blocking_issue_codes=["lattice.universe_ref_missing", "cell.material_ref_missing"],
        human_confirmation_required=True,
    )
    decision = determine_deterministic_supervisor_action(inp)
    assert decision.action != RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION


def test_structural_with_pending_patches_routes_to_repair() -> None:
    """Structural blocker + pending required patches -> continue_patch_generation."""
    inp = _input(
        blocking_issue_codes=["lattice.pin_count_mismatch"],
        required_patch_types=["lattice_pattern"],
        patch_status={"lattice_pattern": "failed"},
        planning_mode="incremental",
    )
    decision = determine_deterministic_supervisor_action(inp)
    assert decision.action == RunSupervisorAction.CONTINUE_PATCH_GENERATION
    assert "structural_blocker_precedes_human_confirmation" in decision.rationale


def test_material_only_still_requests_human_confirmation() -> None:
    """No structural blocker -> material assumptions still escalate normally."""
    inp = _input(human_confirmation_required=True, blocking_issue_codes=[])
    decision = determine_deterministic_supervisor_action(inp)
    assert decision.action == RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION


def test_environment_blocker_can_route_to_skeleton() -> None:
    """An environment blocker is not masked by material assumptions either."""
    inp = _input(
        blocking_issue_codes=["runtime.cross_sections_missing"],
        human_confirmation_required=True,
        renderability="skeleton",
    )
    decision = determine_deterministic_supervisor_action(inp)
    # runtime.cross_sections_missing is ask_expert in the catalog (not a
    # structural code), so it does not trigger the structural override; but it
    # is not a plain material assumption either. The supervisor must not blindly
    # continue to render a skeleton.
    assert decision.action in {
        RunSupervisorAction.REQUEST_HUMAN_CONFIRMATION,
        RunSupervisorAction.DOWNGRADE_TO_SKELETON,
        RunSupervisorAction.STOP,
    }


def test_no_structural_blocker_runs_continue_to_render() -> None:
    inp = _input(blocking_issue_codes=[], human_confirmation_required=False)
    decision = determine_deterministic_supervisor_action(inp)
    assert decision.action == RunSupervisorAction.CONTINUE_TO_RENDER
