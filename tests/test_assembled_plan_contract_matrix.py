"""Tests for AssembledPlanContractMatrix."""

from tests._assembled_plan_fixtures import state_with_assembled_plan, make_assembled_plan
from openmc_agent.plan_builder.closed_loop.assembled_plan_binding import build_assembled_plan_binding_view
from openmc_agent.plan_builder.closed_loop.assembled_plan_evidence import build_assembled_plan_contract_matrix


def test_matrix_has_all_seven_kinds():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    matrix = build_assembled_plan_contract_matrix(view)
    kinds = {r.row_kind for r in matrix.rows}
    assert "root_selection" in kinds
    assert "root_reachability" in kinds
    assert "renderer_capability" in kinds
    assert "static_source_feasibility" in kinds
    assert "execution_check" in kinds


def test_matrix_has_input_hash():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    matrix = build_assembled_plan_contract_matrix(view)
    assert matrix.input_hash


def test_matrix_renderer_capability_row():
    plan = make_assembled_plan()
    state = state_with_assembled_plan(plan=plan)
    view = build_assembled_plan_binding_view(state=state, plan=plan)
    matrix = build_assembled_plan_contract_matrix(view)
    rc = [r for r in matrix.rows if r.row_kind == "renderer_capability"]
    assert len(rc) == 1
