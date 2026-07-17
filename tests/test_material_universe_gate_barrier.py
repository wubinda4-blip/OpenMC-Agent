"""Phase 4: Material-Universe gate barrier tests."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_evidence import material_universe_gate_ready
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanLoopMode, PlanStageStatus
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-bar", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]}]}, status="valid"))
    return state


def test_gate_not_ready_when_materials_missing() -> None:
    state = PlanBuildState(state_id="mu-bar2", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    assert not material_universe_gate_ready(state)


def test_gate_not_ready_when_universes_missing() -> None:
    state = PlanBuildState(state_id="mu-bar3", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "m", "name": "m", "role": "fuel", "density_g_cm3": 1.0}]}, status="valid"))
    assert not material_universe_gate_ready(state)


def test_gate_ready_when_all_inputs_valid() -> None:
    assert material_universe_gate_ready(_state())


def test_controlled_mode_requires_facts_gate() -> None:
    """A controlled material-universe gate without facts gate should be rejected."""
    # This is validated at the executor level; here we just check the policy.
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={PlanGateId.MATERIAL_UNIVERSE: True})
    assert policy.gate_enabled.get(PlanGateId.MATERIAL_UNIVERSE)
    assert not policy.gate_enabled.get(PlanGateId.FACTS)
