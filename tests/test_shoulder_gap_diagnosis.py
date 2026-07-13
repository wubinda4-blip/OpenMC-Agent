"""Tests for component-profile slab diagnosis."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.component_profile_repair import diagnose_component_profile_slab
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _load_fixture_patches() -> list[dict]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text()
    )
    return raw["patches"]


def _broken_state_with_shoulder_slab(
    *, keep_moderator_only_universe: bool = True, keep_shoulder_loading: bool = False,
) -> tuple[PlanBuildState, "SimulationPlan"]:  # type: name-error
    """Build a state with shoulder_gap layers as material slabs."""
    patches = _load_fixture_patches()
    state = PlanBuildState(state_id="test-diag", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        if content["patch_type"] == "axial_layers":
            for layer in content["layers"]:
                if "shoulder" in layer["layer_id"]:
                    layer["fill_type"] = "material"
                    layer["fill_id"] = "borated_water_3a"
                    layer["loading_id"] = None
                    layer["loading_ids"] = []
                    layer["role"] = "shoulder_gap"
            if not keep_shoulder_loading:
                content["lattice_loadings"] = [
                    ll for ll in content["lattice_loadings"]
                    if ll["loading_id"] != "shoulder_water_loading"
                ]
        if content["patch_type"] == "universes":
            if not keep_moderator_only_universe:
                content["universes"] = [
                    u for u in content["universes"]
                    if u["universe_id"] != "moderator_only_pin"
                ]
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"],
            patch_type=content["patch_type"],
            content=content,
            status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches(
        [parse_patch_content(p.patch_type, p.content) for p in state.patches.values()],
        strict=True,
    )
    assert assembled.ok and assembled.plan is not None
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    return state, assembled.plan


def test_diagnosis_identifies_shoulder_gap_with_existing_universe() -> None:
    """When a moderator-only universe exists, diagnosis is deterministic."""
    state, plan = _broken_state_with_shoulder_slab(keep_moderator_only_universe=True)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None
    assert diagnosis.layer_id == "lower_shoulder_gap"
    assert diagnosis.current_fill_type == "material"
    assert diagnosis.current_fill_id == "borated_water_3a"
    assert diagnosis.base_default_universe_id == "fuel_pin"
    assert diagnosis.background_material_id == "borated_water_3a"
    assert "moderator_only_pin" in diagnosis.candidate_profile_universe_ids
    assert diagnosis.deterministic_repair_available is True


def test_diagnosis_identifies_need_to_derive_universe() -> None:
    """When no moderator-only universe exists, derivation is available."""
    state, plan = _broken_state_with_shoulder_slab(keep_moderator_only_universe=False)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None
    assert diagnosis.deterministic_repair_available is True
    assert diagnosis.repair_kind == "create_moderator_profile_bundle"


def test_diagnosis_reuses_existing_loading() -> None:
    """When an existing shoulder loading matches, it is reused."""
    state, plan = _broken_state_with_shoulder_slab(
        keep_moderator_only_universe=True, keep_shoulder_loading=True,
    )
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None
    assert diagnosis.repair_kind == "reuse_existing_loading"
    assert len(diagnosis.candidate_loading_ids) > 0


def test_diagnosis_returns_none_for_nonexistent_layer() -> None:
    state, plan = _broken_state_with_shoulder_slab()
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="nonexistent_layer",
    )
    assert diagnosis is None


def test_diagnosis_returns_none_for_non_profile_layer() -> None:
    """A reflector layer is not a component profile."""
    state, plan = _broken_state_with_shoulder_slab()
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_moderator_buffer",
    )
    assert diagnosis is None


def test_diagnosis_returns_none_for_lattice_layer() -> None:
    """A layer that already uses lattice fill is not a material slab."""
    state, plan = _broken_state_with_shoulder_slab()
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="active_fuel",
    )
    assert diagnosis is None


def test_diagnosis_guide_tube_and_instrument_counts() -> None:
    state, plan = _broken_state_with_shoulder_slab()
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None
    assert diagnosis.guide_tube_count == 24
    assert diagnosis.instrument_tube_count == 1


def test_diagnosis_for_solid_structure_is_ambiguous() -> None:
    """A nozzle layer with material fill is ambiguous (not a shoulder gap)."""
    state, plan = _broken_state_with_shoulder_slab()
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_nozzle",
    )
    # lower_nozzle is not in _COMPONENT_PROFILE_ROLES, so returns None
    assert diagnosis is None
