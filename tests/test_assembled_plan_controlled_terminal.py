"""Tests for controlled Gate terminal protection for assembled-plan gate."""

from openmc_agent.graph import _incremental_gate_outcome_is_terminal


def test_assembled_plan_blocked_is_terminal():
    state = {
        "plan_loop_outcome": {"status": "blocked", "active_gate_id": "assembled_plan"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_assembled_plan_awaiting_human_is_terminal():
    state = {
        "plan_loop_outcome": {"status": "awaiting_human", "active_gate_id": "assembled_plan"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_assembled_plan_stage_blocked_is_terminal():
    state = {
        "plan_build_state": {
            "plan_loop_mode": "controlled",
            "plan_loop_stages": {
                "plan_gate_assembled_plan": {"status": "blocked"},
            },
        },
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_progressed_not_terminal():
    state = {
        "plan_loop_outcome": {"status": "progressed"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is False
