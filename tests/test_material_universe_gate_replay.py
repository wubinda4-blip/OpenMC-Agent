"""Phase 4: Material-Universe gate replay and input hash invalidation."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_evidence import material_universe_gate_input_hash
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-rep", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]}]}, status="valid"))
    return state


def test_materials_change_invalidates_input_hash() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="controlled")
    h1 = material_universe_gate_input_hash(state, policy=policy)
    # Mutate materials.
    materials = next(item for item in state.patches.values() if item.patch_type == "materials")
    materials.content = dict(materials.content)
    materials.content["materials"][0]["density_g_cm3"] = 11.0
    h2 = material_universe_gate_input_hash(state, policy=policy)
    assert h1 != h2


def test_universes_change_invalidates_input_hash() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="controlled")
    h1 = material_universe_gate_input_hash(state, policy=policy)
    universes = next(item for item in state.patches.values() if item.patch_type == "universes")
    universes.content = dict(universes.content)
    universes.content["universes"][0]["universe_id"] = "fuel_v2"
    h2 = material_universe_gate_input_hash(state, policy=policy)
    assert h1 != h2


def test_settings_change_does_not_invalidate_input_hash() -> None:
    state = _state()
    state.add_patch(PlanPatchEnvelope(patch_id="settings", patch_type="settings", content={"patch_type": "settings", "batches": 10}, status="valid"))
    policy = PlanClosedLoopPolicy(mode="controlled")
    h1 = material_universe_gate_input_hash(state, policy=policy)
    settings = next(item for item in state.patches.values() if item.patch_type == "settings")
    settings.content = dict(settings.content)
    settings.content["batches"] = 20
    h2 = material_universe_gate_input_hash(state, policy=policy)
    assert h1 == h2
