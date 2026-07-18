"""Tests for run_axial_geometry_preflight."""

from tests._axial_geometry_fixtures import state_with_axial_patches, make_facts_content, make_axial_layers_content, make_axial_overlays_content
from openmc_agent.plan_builder.closed_loop.axial_geometry_preflight import run_axial_geometry_preflight
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled")


def test_valid_preflight_passes():
    state = state_with_axial_patches()
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    blocking = [i for i in preflight.issues if i.get("severity") == "error"]
    assert len(blocking) == 0


def test_domain_missing():
    state = state_with_axial_patches(facts=make_facts_content(axial_domain=None, active_fuel=None))
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.domain_missing" in codes


def test_domain_reversed():
    state = state_with_axial_patches(facts=make_facts_content(axial_domain=(100.0, 0.0)))
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.domain_invalid" in codes


def test_layer_zero_thickness():
    layers = make_axial_layers_content(layers=[
        {"layer_id": "l1", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 10.0, "fill_type": "lattice", "fill_id": "lat1"},
    ])
    state = state_with_axial_patches(layers=layers)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.layer_zero_thickness" in codes


def test_layer_overlap():
    layers = make_axial_layers_content(layers=[
        {"layer_id": "l1", "role": "lower_nozzle", "z_min_cm": 0.0, "z_max_cm": 20.0, "fill_type": "material", "fill_id": "mat_nozzle"},
        {"layer_id": "l2", "role": "active_fuel", "z_min_cm": 15.0, "z_max_cm": 90.0, "fill_type": "lattice", "fill_id": "lat1"},
        {"layer_id": "l3", "role": "upper_nozzle", "z_min_cm": 90.0, "z_max_cm": 100.0, "fill_type": "material", "fill_id": "mat_nozzle"},
    ])
    state = state_with_axial_patches(layers=layers)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.layer_overlap" in codes


def test_loading_unattached():
    layers = make_axial_layers_content(layers=[
        {"layer_id": "l1", "role": "lower_nozzle", "z_min_cm": 0.0, "z_max_cm": 10.0, "fill_type": "material", "fill_id": "mat_nozzle"},
        {"layer_id": "l2", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 90.0, "fill_type": "lattice", "fill_id": "lat1"},
        {"layer_id": "l3", "role": "upper_nozzle", "z_min_cm": 90.0, "z_max_cm": 100.0, "fill_type": "material", "fill_id": "mat_nozzle"},
    ], loadings=[
        {"loading_id": "ld_orphan", "base_lattice_id": "lat1"},
        {"loading_id": "ld1", "base_lattice_id": "lat1"},
    ])
    state = state_with_axial_patches(layers=layers)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.loading_unattached" in codes


def test_overlay_outside_domain():
    overlays = make_axial_overlays_content(overlays=[
        {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 200.0, "z_max_cm": 200.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
    ])
    state = state_with_axial_patches(overlays=overlays)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.overlay_outside_domain" in codes


def test_overlay_density_required():
    overlays = make_axial_overlays_content(overlays=[
        {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 20.0, "z_max_cm": 20.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 0.0, "through_path_preserved": True, "total_mass_g": 100.0},
    ])
    state = state_with_axial_patches(overlays=overlays)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.overlay_density_required" in codes


def test_overlay_material_missing():
    overlays = make_axial_overlays_content(overlays=[
        {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 20.0, "z_max_cm": 20.5, "target_lattice_id": "lat1", "material_id": "mat_nonexistent", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
    ])
    state = state_with_axial_patches(overlays=overlays)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.overlay_material_missing" in codes


def test_source_grid_count_mismatch():
    overlays = make_axial_overlays_content(overlays=[
        {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 20.0, "z_max_cm": 20.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
    ])
    state = state_with_axial_patches(overlays=overlays)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.overlay_source_count_mismatch" in codes


def test_active_fuel_not_covered():
    layers = make_axial_layers_content(layers=[
        {"layer_id": "l1", "role": "lower_nozzle", "z_min_cm": 0.0, "z_max_cm": 10.0, "fill_type": "material", "fill_id": "mat_nozzle"},
        {"layer_id": "l2", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 50.0, "fill_type": "lattice", "fill_id": "lat1"},
        {"layer_id": "l3", "role": "upper_nozzle", "z_min_cm": 50.0, "z_max_cm": 100.0, "fill_type": "material", "fill_id": "mat_nozzle"},
    ])
    state = state_with_axial_patches(layers=layers)
    preflight = run_axial_geometry_preflight(state=state, policy=_policy())
    codes = [i["code"] for i in preflight.issues]
    assert "axial.active_fuel_region_not_covered" in codes
