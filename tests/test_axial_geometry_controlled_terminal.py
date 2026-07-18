"""Tests for controlled Gate terminal protection (91e8dcc generalization).

The graph-level ``_incremental_gate_outcome_is_terminal`` check is already
generic (it checks ANY stage in blocked/awaiting_human).  These tests confirm
that the Axial Geometry gate stages are covered by that check.
"""

from openmc_agent.graph import _incremental_gate_outcome_is_terminal


def test_axial_geometry_blocked_is_terminal():
    state = {
        "plan_loop_outcome": {"status": "blocked", "active_gate_id": "axial_geometry"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_axial_geometry_awaiting_human_is_terminal():
    state = {
        "plan_loop_outcome": {"status": "awaiting_human", "active_gate_id": "axial_geometry"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_axial_geometry_stage_blocked_is_terminal():
    state = {
        "plan_build_state": {
            "plan_loop_mode": "controlled",
            "plan_loop_stages": {
                "plan_gate_axial_geometry": {"status": "blocked"},
            },
        },
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_axial_geometry_stage_awaiting_human_is_terminal():
    state = {
        "plan_build_state": {
            "plan_loop_mode": "controlled",
            "plan_loop_stages": {
                "plan_gate_axial_geometry": {"status": "awaiting_human"},
            },
        },
    }
    assert _incremental_gate_outcome_is_terminal(state) is True


def test_no_active_gate_not_terminal():
    state = {
        "plan_loop_outcome": {"status": "progressed"},
    }
    assert _incremental_gate_outcome_is_terminal(state) is False


def test_off_mode_not_terminal():
    state = {
        "plan_build_state": {
            "plan_loop_mode": "off",
            "plan_loop_stages": {},
        },
    }
    assert _incremental_gate_outcome_is_terminal(state) is False
