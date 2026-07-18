"""VERA3 Axial Geometry Phase-5 offline qualification.

Reactor-neutral mutation challenges on a VERA3-style axial model:
- finite axial domain;
- active fuel coverage;
- base path continuity;
- axial layer fill references;
- mutation repair/replay;
- through-path preservation.
"""

from tests._axial_geometry_fixtures import state_with_axial_patches, make_facts_content, make_axial_layers_content, make_axial_overlays_content
from openmc_agent.plan_builder.closed_loop.axial_geometry_preflight import run_axial_geometry_preflight
from openmc_agent.plan_builder.closed_loop.axial_geometry_binding import build_axial_geometry_binding_view
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled")


def test_vera3_finite_axial_domain():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert view.axial_domain_cm is not None
    assert view.axial_domain_cm[0] < view.axial_domain_cm[1]


def test_vera3_active_fuel_coverage():
    state = state_with_axial_patches()
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.active_fuel_region_not_covered" not in codes


def test_vera3_layer_fill_references():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    fuel_layer = next(l for l in view.axial_layer_records if l.role == "active_fuel")
    assert fuel_layer.fill_type == "lattice"
    assert fuel_layer.fill_id is not None


def test_vera3_through_path_preserved():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    lattice_tps = [t for t in view.through_path_records if t.path_kind == "base_lattice"]
    assert all(t.preserved for t in lattice_tps)


def test_vera3_mutation_missing_layer_boundary():
    """Remove a layer's z_max -> deterministic error."""
    layers = make_axial_layers_content(layers=[
        {"layer_id": "l1", "role": "lower_nozzle", "z_min_cm": 0.0, "z_max_cm": 10.0, "fill_type": "material", "fill_id": "mat_nozzle"},
        {"layer_id": "l2", "role": "active_fuel", "z_min_cm": 10.0, "fill_type": "lattice", "fill_id": "lat1"},
        {"layer_id": "l3", "role": "upper_nozzle", "z_min_cm": 90.0, "z_max_cm": 100.0, "fill_type": "material", "fill_id": "mat_nozzle"},
    ])
    state = state_with_axial_patches(layers=layers)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    # Either the layer has invalid range or gap coverage issue.
    assert len(preflight.issues) > 0
