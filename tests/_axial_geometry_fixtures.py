"""Shared test fixtures for Phase-5 Axial Geometry Gate tests."""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.closed_loop.models import (
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanStageState,
    PlanStageStatus,
)
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def make_axial_layers_content(
    *,
    domain: tuple[float, float] = (0.0, 100.0),
    layers: list[dict[str, Any]] | None = None,
    loadings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if layers is None:
        layers = [
            {"layer_id": "l1", "role": "lower_nozzle", "z_min_cm": 0.0, "z_max_cm": 10.0, "fill_type": "material", "fill_id": "mat_nozzle"},
            {"layer_id": "l2", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 90.0, "fill_type": "lattice", "fill_id": "lat1", "loading_id": "ld1"},
            {"layer_id": "l3", "role": "upper_nozzle", "z_min_cm": 90.0, "z_max_cm": 100.0, "fill_type": "material", "fill_id": "mat_nozzle"},
        ]
    if loadings is None:
        loadings = [{"loading_id": "ld1", "base_lattice_id": "lat1"}]
    return {"patch_type": "axial_layers", "axial_domain_cm": list(domain), "layers": layers, "lattice_loadings": loadings}


def make_axial_overlays_content(overlays: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if overlays is None:
        overlays = [
            {"overlay_id": "sg1", "overlay_kind": "spacer_grid", "z_min_cm": 20.0, "z_max_cm": 20.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
            {"overlay_id": "sg2", "overlay_kind": "spacer_grid", "z_min_cm": 50.0, "z_max_cm": 50.5, "target_lattice_id": "lat1", "material_id": "mat_grid", "geometry_mode": "mass_conserving_outer_frame", "effective_density_g_cm3": 7.8, "through_path_preserved": True, "total_mass_g": 100.0},
        ]
    return {"patch_type": "axial_overlays", "overlays": overlays}


def make_facts_content(
    *,
    axial_domain: tuple[float, float] | None = (0.0, 100.0),
    active_fuel: tuple[float, float] | None = (10.0, 90.0),
    spacer_grids: int | None = 2,
) -> dict[str, Any]:
    return {
        "patch_type": "facts",
        "model_scope": "single_assembly",
        "has_axial_geometry": True,
        "has_spacer_grids": spacer_grids is not None and spacer_grids > 0,
        "axial_domain_cm": list(axial_domain) if axial_domain else None,
        "active_fuel_region_cm": list(active_fuel) if active_fuel else None,
        "expected_spacer_grid_count": spacer_grids,
    }


def make_materials_content() -> dict[str, Any]:
    return {
        "patch_type": "materials",
        "materials": [
            {"material_id": "mat_nozzle", "name": "nozzle steel", "role": "structural", "density_g_cm3": 7.8},
            {"material_id": "mat_grid", "name": "grid steel", "role": "structural", "density_g_cm3": 7.8},
            {"material_id": "mat_fuel", "name": "fuel", "role": "fuel", "density_g_cm3": 10.0},
        ],
    }


def make_universes_content() -> dict[str, Any]:
    return {
        "patch_type": "universes",
        "universes": [
            {"universe_id": "u_fuel", "kind": "fuel_pin", "cells": [{"id": "c1", "role": "fuel", "material_id": "mat_fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]},
        ],
    }


def state_with_axial_patches(
    *,
    facts: dict[str, Any] | None = None,
    layers: dict[str, Any] | None = None,
    overlays: dict[str, Any] | None = None,
    materials: dict[str, Any] | None = None,
    universes: dict[str, Any] | None = None,
    include_profiles: bool = False,
) -> PlanBuildState:
    state = PlanBuildState(state_id="axial_test", requirement_text="test reactor")
    state.add_patch(PlanPatchEnvelope(patch_id="facts_1", patch_type="facts", content=facts or make_facts_content(), status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="mat_1", patch_type="materials", content=materials or make_materials_content(), status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="uni_1", patch_type="universes", content=universes or make_universes_content(), status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="layers_1", patch_type="axial_layers", content=layers or make_axial_layers_content(), status="valid"))
    if overlays is not None:
        state.add_patch(PlanPatchEnvelope(patch_id="overlays_1", patch_type="axial_overlays", content=overlays, status="valid"))
    elif overlays is None:
        state.add_patch(PlanPatchEnvelope(patch_id="overlays_1", patch_type="axial_overlays", content=make_axial_overlays_content(), status="valid"))
    if include_profiles:
        state.add_patch(PlanPatchEnvelope(patch_id="profiles_1", patch_type="base_path_axial_profiles", content={"patch_type": "base_path_axial_profiles", "profiles": []}, status="valid"))
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True, PlanGateId.PLACEMENT: True, PlanGateId.AXIAL_GEOMETRY: True},
        axial_geometry_review_mode="controlled",
    )
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes", "axial_layers", "axial_overlays"])
    # Mark upstream gates as accepted so the axial gate can proceed.
    for stage_key in ("plan_gate_facts", "plan_gate_material_universe", "plan_gate_placement"):
        stage = state.plan_loop_stages.get(stage_key)
        if stage is not None:
            stage.status = PlanStageStatus.ACCEPTED
            stage.metadata["accepted_input_hash"] = "upstream_hash_001"
    return state
