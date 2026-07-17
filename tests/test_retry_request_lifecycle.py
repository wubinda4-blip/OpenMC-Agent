"""Phase 3B: retry request lifecycle states."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import (
    RetryRequestLifecycle,
    RetryTriggerOrigin,
    TERMINAL_RETRY_LIFECYCLE_STATES,
)
from openmc_agent.plan_builder.state import PlanBuildState


def test_fresh_request_starts_in_pending_lifecycle() -> None:
    state = PlanBuildState(state_id="lifecycle", requirement_text="r")
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.lifecycle is RetryRequestLifecycle.PENDING


def test_terminal_states_form_expected_set() -> None:
    assert "resolved" in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "superseded" in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "no_progress" in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "blocked" in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "failed" in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "pending" not in TERMINAL_RETRY_LIFECYCLE_STATES
    assert "executing" not in TERMINAL_RETRY_LIFECYCLE_STATES


def test_lifecycle_enum_has_all_expected_states() -> None:
    expected = {"pending", "executing", "awaiting_human", "owner_committed", "rebuilding", "replaying", "resolved", "superseded", "no_progress", "blocked", "failed"}
    actual = {item.value for item in RetryRequestLifecycle}
    assert expected == actual
