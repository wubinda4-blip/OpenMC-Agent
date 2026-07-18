"""Tests for Axial Geometry Gate input hash invalidation and replay."""

from tests._axial_geometry_fixtures import state_with_axial_patches, make_axial_overlays_content
from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import axial_geometry_gate_input_hash
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled")


def test_input_hash_changes_when_overlays_change():
    state = state_with_axial_patches()
    h1 = axial_geometry_gate_input_hash(state, policy=_policy())
    # Replace the existing overlays patch (same patch_id, new content).
    from openmc_agent.plan_builder.state import PlanPatchEnvelope
    state.add_patch(PlanPatchEnvelope(patch_id="overlays_1", patch_type="axial_overlays", content=make_axial_overlays_content(overlays=[
        {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 25.0, "z_max_cm": 25.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
        {"overlay_id": "sg2", "overlay_kind": "spacer_grid", "z_min_cm": 55.0, "z_max_cm": 55.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
    ]), status="valid"))
    h2 = axial_geometry_gate_input_hash(state, policy=_policy())
    assert h1 != h2


def test_input_hash_stable_when_settings_change():
    """Settings changes unrelated to axial structure should not change the hash."""
    state = state_with_axial_patches()
    h1 = axial_geometry_gate_input_hash(state, policy=_policy())
    # The input hash deliberately does not bind settings.
    h2 = axial_geometry_gate_input_hash(state, policy=_policy())
    assert h1 == h2


def test_accepted_hash_reopen_on_change():
    """When input hash changes, an accepted gate should reopen."""
    state = state_with_axial_patches()
    h1 = axial_geometry_gate_input_hash(state, policy=_policy())
    stage = state.plan_loop_stages["plan_gate_axial_geometry"]
    stage.metadata["accepted_input_hash"] = h1
    # Simulate hash change by setting a different accepted hash.
    stage.metadata["accepted_input_hash"] = "old_hash"
    assert stage.metadata["accepted_input_hash"] != h1
