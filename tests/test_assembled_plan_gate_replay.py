"""Tests for Assembled Plan Gate replay (input hash invalidation)."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_evidence import assembled_plan_gate_input_hash
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled")


def test_input_hash_stable():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    h1 = assembled_plan_gate_input_hash(state, policy=_policy())
    h2 = assembled_plan_gate_input_hash(state, policy=_policy())
    assert h1 == h2


def test_accepted_hash_reopen_on_change():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    h1 = assembled_plan_gate_input_hash(state, policy=_policy())
    stage = state.plan_loop_stages["plan_gate_assembled_plan"]
    stage.metadata["accepted_input_hash"] = "old_hash"
    assert stage.metadata["accepted_input_hash"] != h1
