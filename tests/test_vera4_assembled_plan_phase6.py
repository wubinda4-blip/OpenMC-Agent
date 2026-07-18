"""VERA4 Assembled Plan Phase-6 offline qualification."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_binding import build_assembled_plan_binding_view
from openmc_agent.plan_builder.closed_loop.assembled_plan_preflight import run_assembled_plan_preflight
from openmc_agent.plan_builder.closed_loop.assembled_plan_issue_policy import assembled_plan_issue_owner
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled")


def test_vera4_assembled_plan_core_model():
    plan = make_assembled_plan(model_kind="core", with_core=True)
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert view.model_kind == "core"


def test_vera4_assembled_plan_object_graph():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert len(view.object_graph.edges) > 0


def test_vera4_assembled_plan_renderer_capability():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert view.selected_renderer != ""


def test_vera4_assembled_plan_preflight():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    preflight = run_assembled_plan_preflight(state=state, policy=_policy(), plan=plan)
    assert "object_count" in preflight.summary


def test_vera4_assembled_plan_retry_routing():
    owner = assembled_plan_issue_owner("assembled.root_missing")
    assert owner is not None
    assert "facts" in owner.owner_patch_types
    assert PlanGateId.ASSEMBLED_PLAN in owner.gates_to_invalidate


def test_vera4_assembled_plan_execution_check():
    plan = make_assembled_plan(model_kind="assembly")
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    assert view.execution_check_record is not None
