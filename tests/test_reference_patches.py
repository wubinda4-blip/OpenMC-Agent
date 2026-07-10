"""Tests for reference-backed deterministic patches (Phase 7D)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.reference_patches import (
    REFERENCE_PATCH_TYPES,
    benchmark_ids_match,
    build_reference_patch,
    load_benchmark_reference,
    normalize_benchmark_id,
)
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_builder.mode import should_use_incremental_planning
from openmc_agent.plan_builder.state import initialize_plan_build_state


_VERA3_3B_REQ = (
    "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, "
    "三维, 定位格架, Pyrex rods, thimble plugs, 17x17 lattice"
)


def _init_3b_state() -> PlanBuildState:
    decision = should_use_incremental_planning(_VERA3_3B_REQ)
    return initialize_plan_build_state(
        requirement=_VERA3_3B_REQ, decision=decision,
        benchmark_id="VERA3", selected_variant="3B",
    )


# ---------------------------------------------------------------------------
# Reference loader
# ---------------------------------------------------------------------------


def test_load_vera3_3b_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    assert ref is not None
    patches = ref.get("patches", [])
    assert len(patches) == 7
    assert ref.get("_reference_match_status") == "exact"


@pytest.mark.parametrize(
    "requested_id",
    ["VERA3", "VERA_PROBLEM_3", "VERA Problem 3", "VERA_Problem3"],
)
def test_normalized_benchmark_id_matches_vera3_reference(requested_id: str) -> None:
    assert benchmark_ids_match(requested_id, "VERA3")
    ref = load_benchmark_reference(benchmark_id=requested_id, variant="3B")
    assert ref is not None
    assert ref.get("_reference_benchmark_id") == "VERA3"


def test_normalized_benchmark_id_removes_generic_noise() -> None:
    assert normalize_benchmark_id("Core Physics Benchmark Problem 3") == "3"


def test_reference_loader_wrong_variant_returns_none() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA_PROBLEM_3", variant="WRONG")
    assert ref is None


def test_load_vera3_3a_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3A")
    assert ref is not None


def test_load_unknown_benchmark_returns_none() -> None:
    ref = load_benchmark_reference(benchmark_id="UNKNOWN", variant="X")
    assert ref is None


def test_load_explicit_path(tmp_path: Path) -> None:
    ref_file = tmp_path / "ref.json"
    ref_file.write_text(json.dumps({"patches": [{"patch_type": "settings"}]}))
    ref = load_benchmark_reference(reference_path=ref_file)
    assert ref is not None
    assert len(ref["patches"]) == 1


# ---------------------------------------------------------------------------
# Reference patch building
# ---------------------------------------------------------------------------


def test_build_pin_map_from_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    patch = build_reference_patch(patch_type="pin_map", reference=ref, variant="3B")
    assert patch is not None
    assert len(patch.guide_tube_coords) == 24
    assert len(patch.pyrex_rod_coords) == 0
    assert len(patch.thimble_plug_coords) == 0
    assert len(patch.instrument_tube_coords) == 1
    # Should NOT have a full 289-entry pattern.
    assert not hasattr(patch, "universe_pattern")


def test_build_axial_layers_from_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    patch = build_reference_patch(patch_type="axial_layers", reference=ref, variant="3B")
    assert patch is not None
    assert len(patch.layers) == 16  # upper plenum split into 3 segments
    assert len(patch.lattice_loadings) == 5  # end_plug + plenum + pyrex + thimble + shoulder water
    has_fuel = any(l.role == "active_fuel" for l in patch.layers)
    assert has_fuel


def test_build_axial_overlays_from_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    patch = build_reference_patch(patch_type="axial_overlays", reference=ref, variant="3B")
    assert patch is not None
    assert len(patch.overlays) == 8
    assert all(o.geometry_mode == "homogenized_open_region" for o in patch.overlays)
    assert all(o.through_path_preserved is True for o in patch.overlays)


def test_build_settings_from_reference() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    patch = build_reference_patch(patch_type="settings", reference=ref, variant="3B")
    assert patch is not None


def test_build_unsupported_patch_type_returns_none() -> None:
    ref = load_benchmark_reference(benchmark_id="VERA3", variant="3B")
    patch = build_reference_patch(patch_type="facts", reference=ref, variant="3B")
    assert patch is None  # facts is not in REFERENCE_PATCH_TYPES


# ---------------------------------------------------------------------------
# reference_only_for_structural policy
# ---------------------------------------------------------------------------


def test_reference_only_structural_bypasses_llm() -> None:
    """LLM only generates facts/materials/universes; structural from reference."""
    from tests.test_vera3_patch_fixtures import _load_fixture
    fixture_patches = _load_fixture("3b")
    materials_patch = next(p for p in fixture_patches if p.patch_type == "materials")
    universes_patch = next(p for p in fixture_patches if p.patch_type == "universes")

    # Fake LLM responses for facts only (materials/universes from fixture).
    facts_raw = json.dumps({
        "patch_type": "facts", "benchmark_id": "VERA3", "selected_variant": "3B",
        "lattice_size": [17, 17], "pin_pitch_cm": 1.26,
        "has_axial_geometry": True, "has_spacer_grids": True,
        "has_special_pin_map": True,
    })
    materials_raw = json.dumps(materials_patch.model_dump(mode="json"))
    universes_raw = json.dumps(universes_patch.model_dump(mode="json"))
    fake = FakePatchLLM([facts_raw, materials_raw, universes_raw])

    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ,
        state=state,
        llm_client=fake,
        max_patch_attempts=1,
        reference_patch_policy="reference_only_for_structural",
    )
    assert result.ok is True
    assert result.assembled_plan is not None
    # Structural patches should be from reference (source=fixture).
    for ptype in ("pin_map", "axial_layers", "axial_overlays", "settings"):
        env = next(e for e in state.patches.values() if e.patch_type == ptype)
        assert env.source == "fixture"


# ---------------------------------------------------------------------------
# fallback_after_llm_failure policy
# ---------------------------------------------------------------------------


def test_fallback_after_llm_failure_uses_reference() -> None:
    """LLM fails pin_map → reference fallback used."""
    from tests.test_vera3_patch_fixtures import _load_fixture
    fixture_patches = _load_fixture("3b")
    materials_patch = next(p for p in fixture_patches if p.patch_type == "materials")
    universes_patch = next(p for p in fixture_patches if p.patch_type == "universes")

    facts_raw = json.dumps({
        "patch_type": "facts", "benchmark_id": "VERA3", "selected_variant": "3B",
        "lattice_size": [17, 17], "pin_pitch_cm": 1.26,
        "has_axial_geometry": True, "has_spacer_grids": True,
        "has_special_pin_map": True,
    })
    materials_raw = json.dumps(materials_patch.model_dump(mode="json"))
    universes_raw = json.dumps(universes_patch.model_dump(mode="json"))
    # Pin_map always fails (overlap).
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]], "pyrex_rod_coords": [[5, 5]],
    })
    fake = FakePatchLLM([facts_raw, materials_raw, universes_raw, bad_pin, bad_pin])

    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ,
        state=state,
        llm_client=fake,
        max_patch_attempts=2,
        reference_patch_policy="fallback_after_llm_failure",
    )
    assert result.ok is True
    # Pin_map should be from reference fallback.
    pin_env = next(e for e in state.patches.values() if e.patch_type == "pin_map")
    assert pin_env.source == "fixture"
    # Build log should have fallback event.
    event_types = [e.event_type for e in state.build_log]
    assert "reference_patch.fallback_after_llm_failure" in event_types


# ---------------------------------------------------------------------------
# Failure summary enhancement
# ---------------------------------------------------------------------------


def test_failure_summary_includes_failed_patch_type() -> None:
    """When executor fails, summary must have failed_patch_type."""
    # pin_map with overlap → repaired, passes. Then axial_layers fails.
    overlap_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    bad_axial = json.dumps({
        "patch_type": "axial_layers", "layers": [
            {"layer_id": "f", "role": "active_fuel", "z_min_cm": 100.0, "z_max_cm": 50.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ],
    })
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0}]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]}]}),
        overlap_pin,      # pin_map: overlap repaired → passes
        bad_axial, bad_axial,  # axial_layers: always fails
    ]
    fake = FakePatchLLM(responses)
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=2,
        reference_patch_policy="off",
    )
    assert result.ok is False
    summary = result.summary
    # Should fail at axial_layers now (pin_map overlap was repaired)
    assert summary.get("failed_patch_type") == "axial_layers"
    assert "facts" in summary.get("valid_patch_types", [])
    assert "materials" in summary.get("valid_patch_types", [])
    assert "universes" in summary.get("valid_patch_types", [])
    assert "axial_layers" in summary.get("invalid_patch_types", [])
    assert summary.get("monolithic_fallback_attempted") is False


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_skips_valid_patches(tmp_path: Path) -> None:
    """Resume from state with valid facts/materials/universes."""
    from openmc_agent.plan_builder.state import save_plan_build_state, load_plan_build_state

    # Create initial state with 3 valid patches.
    state = _init_3b_state()
    from openmc_agent.plan_builder.state import PlanPatchEnvelope

    for ptype, content in [
        ("facts", {"patch_type": "facts", "benchmark_id": "VERA3", "selected_variant": "3B"}),
        ("materials", {"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
            {"material_id": "zircaloy4", "name": "Zircaloy-4", "role": "cladding",
             "density_g_cm3": 6.56, "composition": {"Zr": 1.0},
             "composition_status": "approximate",
             "warnings": ["Zircaloy-4 approximated as pure Zr"]},
            {"material_id": "inconel718", "name": "Inconel-718", "role": "grid_inconel",
             "density_g_cm3": 8.19, "composition": {"Ni": 1.0},
             "composition_status": "approximate",
             "warnings": ["Inconel-718 approximated as pure Ni"]}]}),
        ("universes", {"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]}]}),
    ]:
        env = PlanPatchEnvelope(
            patch_id=f"pre_{ptype}", patch_type=ptype,
            content=content, status="valid",
        )
        state.add_patch(env)

    # Save state.
    state_path = tmp_path / "plan_build_state.json"
    save_plan_build_state(state, state_path)

    # Load state.
    loaded = load_plan_build_state(state_path)
    assert len(loaded.get_valid_patches()) == 3

    # Run with reference structural patches (no LLM needed for structural).
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ,
        state=loaded,
        llm_client=FakePatchLLM([]),  # no LLM responses needed
        max_patch_attempts=1,
        reference_patch_policy="reference_only_for_structural",
    )
    assert result.ok is True
    # Valid patches from before should be preserved.
    assert loaded.patches["pre_facts"].status == "valid"
