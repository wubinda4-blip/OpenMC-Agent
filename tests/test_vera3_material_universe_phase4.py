"""Phase 4: VERA3 Material-Universe offline qualification."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_binding import build_material_universe_binding_view
from openmc_agent.plan_builder.closed_loop.material_universe_preflight import run_material_universe_preflight
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _vera3_state() -> PlanBuildState:
    state = PlanBuildState(state_id="vera3-mu", requirement_text="VERA3", benchmark_id="VERA3")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly", "fuel_variant_requirements": [{"variant_id": "v_31", "enrichment_wt_percent": 3.1}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [
        {"material_id": "fuel_31", "name": "fuel 3.1", "role": "fuel", "density_g_cm3": 10.25, "source_variant_id": "v_31"},
        {"material_id": "zircaloy4", "name": "Zircaloy-4", "role": "cladding", "density_g_cm3": 6.56},
        {"material_id": "water", "name": "moderator", "role": "coolant", "density_g_cm3": 0.99},
    ]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [
        {"universe_id": "fuel_pin_3a", "kind": "fuel_pin", "cells": [
            {"id": "pellet", "role": "fuel", "material_id": "fuel_31", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4096},
            {"id": "clad", "role": "clad", "material_id": "zircaloy4", "region_kind": "annulus", "r_min_cm": 0.4096, "r_max_cm": 0.4740},
            {"id": "moderator", "role": "background", "material_id": "water", "region_kind": "background"},
        ]},
    ]}, status="valid"))
    return state


def test_vera3_fuel_coolant_cladding_bindings_present() -> None:
    state = _vera3_state()
    view = build_material_universe_binding_view(state=state)
    roles = {m.role for m in view.material_records}
    assert "fuel" in roles
    assert "coolant" in roles
    assert "cladding" in roles


def test_vera3_fuel_universe_active_fuel_reachability() -> None:
    state = _vera3_state()
    view = build_material_universe_binding_view(state=state)
    fuel_universe = next(u for u in view.universe_records if u.kind == "fuel_pin")
    assert "fuel_31" in fuel_universe.material_ids


def test_vera3_baseline_preflight_passes() -> None:
    state = _vera3_state()
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={"facts": True, "material_universe": True})
    result = run_material_universe_preflight(state=state, policy=policy)
    error_codes = {i["code"] for i in result.issues if i.get("severity") == "error"}
    assert not error_codes, f"VERA3 baseline preflight errors: {error_codes}"


def test_vera3_fuel_material_mutation_detected() -> None:
    state = _vera3_state()
    # Remove the fuel material.
    materials = next(item for item in state.patches.values() if item.patch_type == "materials")
    materials.content = dict(materials.content)
    materials.content["materials"] = [m for m in materials.content["materials"] if m["material_id"] != "fuel_31"]
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={"facts": True, "material_universe": True})
    result = run_material_universe_preflight(state=state, policy=policy)
    codes = {i["code"] for i in result.issues}
    assert "material_universe.required_fuel_variant_material_missing" in codes or "material_universe.material_reference_missing" in codes
