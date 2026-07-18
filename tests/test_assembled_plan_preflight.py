"""Tests for run_assembled_plan_preflight."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_preflight import run_assembled_plan_preflight
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled")


def test_valid_plan_preflight():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    preflight = run_assembled_plan_preflight(state=state, policy=_policy(), plan=plan)
    assert preflight.binding_view is not None
    assert len(preflight.binding_view.object_graph.nodes) > 0


def test_preflight_issues_have_codes():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    preflight = run_assembled_plan_preflight(state=state, policy=_policy(), plan=plan)
    for issue in preflight.issues:
        assert "code" in issue
        assert "severity" in issue


def test_preflight_summary():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    preflight = run_assembled_plan_preflight(state=state, policy=_policy(), plan=plan)
    assert "object_count" in preflight.summary
    assert "selected_renderer" in preflight.summary
