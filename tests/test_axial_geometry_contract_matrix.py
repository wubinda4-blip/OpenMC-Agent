"""Tests for AxialGeometryContractMatrix construction."""

from tests._axial_geometry_fixtures import state_with_axial_patches
from openmc_agent.plan_builder.closed_loop.axial_geometry_binding import build_axial_geometry_binding_view
from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import build_axial_geometry_contract_matrix


def test_matrix_has_all_nine_row_kinds():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    matrix = build_axial_geometry_contract_matrix(view)
    kinds = {r.row_kind for r in matrix.rows}
    assert "source_domain_coverage" in kinds
    assert "active_fuel_coverage" in kinds
    assert "layer_fill_binding" in kinds
    assert "loading_attachment" in kinds
    assert "overlay_binding" in kinds
    assert "through_path_preservation" in kinds
    assert "spacer_grid_structural_count" in kinds


def test_matrix_layer_fill_binding_passes():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    matrix = build_axial_geometry_contract_matrix(view)
    layer_rows = [r for r in matrix.rows if r.row_kind == "layer_fill_binding"]
    assert len(layer_rows) == 3
    assert all(r.coverage_status == "pass" for r in layer_rows)


def test_matrix_loading_attachment_detected():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    matrix = build_axial_geometry_contract_matrix(view)
    loading_rows = [r for r in matrix.rows if r.row_kind == "loading_attachment"]
    assert len(loading_rows) >= 1
    assert all(r.coverage_status == "pass" for r in loading_rows)


def test_matrix_spacer_grid_count_matches():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    matrix = build_axial_geometry_contract_matrix(view)
    sg_rows = [r for r in matrix.rows if r.row_kind == "spacer_grid_structural_count"]
    assert len(sg_rows) == 1
    assert sg_rows[0].expected_count == 2
    assert sg_rows[0].actual_count == 2
    assert sg_rows[0].coverage_status == "pass"


def test_matrix_has_input_hash():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    matrix = build_axial_geometry_contract_matrix(view)
    assert matrix.input_hash
    assert len(matrix.input_hash) > 0
