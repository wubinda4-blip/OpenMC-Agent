from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_preflight import run_material_universe_preflight
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state(materials, universes, facts=None):
    state = PlanBuildState(state_id="mu-step4", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content=facts or {"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content=materials, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content=universes, status="valid"))
    return state


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", gate_enabled={"facts": True, "material_universe": True})


def test_density_missing_is_deterministic():
    result = run_material_universe_preflight(
        state=_state({"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel"}]}, {"patch_type": "universes", "universes": []}),
        policy=_policy(),
    )
    assert "material_universe.material_density_missing" in {item["code"] for item in result.issues}


def test_compound_isotope_unresolved_is_deterministic():
    result = run_material_universe_preflight(
        state=_state({"patch_type": "materials", "materials": [{"material_id": "poison", "name": "p", "role": "poison", "density_g_cm3": 2.0, "compound_components": [{"formula": "B2O3", "fraction": 1.0, "fraction_basis": "weight_frac"}]}]}, {"patch_type": "universes", "universes": []}),
        policy=_policy(),
    )
    assert "material_universe.compound_isotope_unresolved" in {item["code"] for item in result.issues}


def test_missing_insert_universe_and_protected_path_are_deterministic():
    facts = {"patch_type": "facts", "model_scope": "single_assembly", "localized_insert_requirements": [{"requirement_id": "insert-1", "insert_kind": "control_rod", "host_kind": "guide_tube", "expected_insert_universe_ids": ["insert-u"]}]}
    base_materials = {"patch_type": "materials", "materials": [{"material_id": "absorber", "name": "a", "role": "absorber", "density_g_cm3": 1.0}]}
    missing = run_material_universe_preflight(state=_state(base_materials, {"patch_type": "universes", "universes": []}, facts), policy=_policy())
    assert "material_universe.localized_insert_universe_missing" in {item["code"] for item in missing.issues}
    facts["localized_insert_requirements"][0]["required_profile_id"] = "profile-insert"
    state = _state(base_materials, {"patch_type": "universes", "universes": [{"universe_id": "insert-u", "kind": "control_rod", "cells": [{"id": "a", "role": "absorber", "material_id": "absorber", "region_kind": "cylinder"}]}]}, facts)
    state.metadata["planning_geometry_inventory"] = {"radial_profiles": [{"profile_id": "profile-insert", "protected_through_path_roles": ["coolant"]}]}
    no_path = run_material_universe_preflight(state=state, policy=_policy())
    assert "material_universe.protected_path_missing" in {item["code"] for item in no_path.issues}


def test_mu_failure_does_not_change_accepted_facts():
    facts = {"patch_type": "facts", "model_scope": "single_assembly", "fuel_variant_requirements": [{"variant_id": "v1", "enrichment_wt_percent": 3.0}]}
    state = _state(
        {"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel"}]},
        {"patch_type": "universes", "universes": []}, facts,
    )
    before = state.patches["facts"].content.copy()
    result = run_material_universe_preflight(state=state, policy=_policy())
    assert not result.ok
    assert state.patches["facts"].content == before


def test_segment_role_coverage_accepts_alternative_universe_id():
    """When Facts declares expected_insert_universe_ids=['u_a','u_b'] but a
    single generated universe of the matching kind covers all required_segment_roles,
    the preflight should NOT report missing universes."""
    facts = {"patch_type": "facts", "model_scope": "single_assembly", "localized_insert_requirements": [{"requirement_id": "rcca", "insert_kind": "control_rod", "host_kind": "guide_tube", "expected_insert_universe_ids": ["rcca_aic_univ", "rcca_b4c_univ"], "required_segment_roles": ["aic_absorber", "b4c_absorber"]}]}
    materials = {"patch_type": "materials", "materials": [{"material_id": "absorber", "name": "a", "role": "absorber", "density_g_cm3": 1.0}]}
    universes = {"patch_type": "universes", "universes": [{"universe_id": "localized_insert_rcca", "kind": "control_rod", "cells": [{"id": "aic", "role": "aic_absorber", "material_id": "absorber", "region_kind": "cylinder"}, {"id": "b4c", "role": "b4c_absorber", "material_id": "absorber", "region_kind": "cylinder"}]}]}
    result = run_material_universe_preflight(state=_state(materials, universes, facts), policy=_policy())
    codes = {item["code"] for item in result.issues if item.get("severity") == "error"}
    assert "material_universe.localized_insert_universe_missing" not in codes
