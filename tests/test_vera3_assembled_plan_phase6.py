"""VERA3 Assembled Plan Phase-6 offline qualification."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_binding import build_assembled_plan_binding_view
from openmc_agent.plan_builder.closed_loop.assembled_plan_preflight import run_assembled_plan_preflight
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled")


def test_vera3_assembled_plan_object_graph():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert len(view.object_graph.nodes) > 0


def test_vera3_assembled_plan_root_selected():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert len(view.selected_roots) >= 1


def test_vera3_assembled_plan_renderer_matrix():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert len(view.renderer_capability_matrix) > 0


def test_vera3_assembled_plan_preflight_completes():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    preflight = run_assembled_plan_preflight(state=state, policy=_policy(), plan=plan)
    assert preflight.binding_view is not None


def test_vera3_assembled_plan_source_feasibility():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    sf = view.static_source_feasibility
    assert sf.source_strategy != "unknown"
