"""Phase 4: Material-Universe gate mode behaviour (off/advisory/controlled)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.controller import initialize_gate_stage, initialize_plan_loop_state, transition_stage
from openmc_agent.plan_builder.closed_loop.material_universe_evidence import material_universe_gate_applicable
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanLoopMode, PlanStageStatus
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state_with_patches() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-modes", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]}]}, status="valid"))
    return state


def test_off_mode_does_not_create_stage() -> None:
    state = _state_with_patches()
    policy = PlanClosedLoopPolicy(mode="off")
    initialize_plan_loop_state(state, policy, [])
    assert "plan_gate_material_universe" not in state.plan_loop_stages


def test_off_mode_applicability_still_works_but_no_gate() -> None:
    state = _state_with_patches()
    assert material_universe_gate_applicable(state)
    # No stage exists, so the gate is never invoked.


def test_advisory_mode_creates_stage() -> None:
    state = _state_with_patches()
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.MATERIAL_UNIVERSE: True})
    initialize_plan_loop_state(state, policy, ["materials", "universes"])
    assert "plan_gate_material_universe" in state.plan_loop_stages


def test_controlled_mode_requires_accepted_facts() -> None:
    state = _state_with_patches()
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True})
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes"])
    mu_stage = state.plan_loop_stages["plan_gate_material_universe"]
    # Facts not yet accepted → material-universe should remain pending.
    assert mu_stage.status is PlanStageStatus.PENDING


def test_contract_version_is_0_8() -> None:
    state = _state_with_patches()
    policy = PlanClosedLoopPolicy(mode="advisory")
    initialize_plan_loop_state(state, policy, [])
    assert state.plan_loop_contract_version == "0.8"
