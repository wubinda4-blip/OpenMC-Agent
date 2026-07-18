"""Tests for AssembledPlanEvidencePack."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_evidence import (
    assembled_plan_gate_applicable,
    assembled_plan_gate_ready,
    assembled_plan_gate_input_hash,
    build_assembled_plan_evidence_pack,
)
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled")


def test_applicable_with_assembled_plan():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    assert assembled_plan_gate_applicable(state) is True


def test_ready_with_assembled_plan():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    assert assembled_plan_gate_ready(state) is True


def test_input_hash_stable():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    h1 = assembled_plan_gate_input_hash(state, policy=_policy())
    h2 = assembled_plan_gate_input_hash(state, policy=_policy())
    assert h1 == h2


def test_evidence_pack_has_items():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    pack = build_assembled_plan_evidence_pack(state=state, policy=_policy(), plan=plan)
    assert pack.gate_id.value == "assembled_plan"
    assert len(pack.evidence_items) > 0
    assert pack.input_hash
