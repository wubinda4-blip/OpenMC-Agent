"""Phase 4: Material-Universe evidence pack."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_evidence import build_material_universe_evidence_pack, material_universe_gate_applicable, material_universe_gate_input_hash, material_universe_gate_ready
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-ev", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]}]}, status="valid"))
    return state


def test_evidence_pack_has_typed_refs() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="advisory")
    pack = build_material_universe_evidence_pack(state=state, policy=policy)
    ref_prefixes = {item.ref_id[0] for item in pack.evidence_items}
    assert "F" in ref_prefixes or "M" in ref_prefixes or "U" in ref_prefixes


def test_applicable_returns_true_when_materials_and_universes_exist() -> None:
    assert material_universe_gate_applicable(_state())


def test_ready_returns_false_when_universes_missing() -> None:
    state = PlanBuildState(state_id="mu-ev2", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "m", "name": "m", "role": "fuel", "density_g_cm3": 1.0}]}, status="valid"))
    assert not material_universe_gate_ready(state)


def test_input_hash_is_stable() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="controlled")
    h1 = material_universe_gate_input_hash(state, policy=policy)
    h2 = material_universe_gate_input_hash(state, policy=policy)
    assert h1 == h2
