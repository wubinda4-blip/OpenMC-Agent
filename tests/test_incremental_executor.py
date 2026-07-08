"""Tests for the incremental executor (Phase 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.executor import (
    IncrementalExecutionResult,
    build_deterministic_settings_patch,
    build_generation_context_from_state,
    default_patch_task_order,
    required_patch_types_for_state,
    run_incremental_planning,
)
from openmc_agent.plan_builder.mode import should_use_incremental_planning
from openmc_agent.plan_builder.patches import (
    FactsPatch,
    parse_patch_content,
)
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.state import (
    PlanBuildState,
    PlanPatchEnvelope,
    initialize_plan_build_state,
)
from openmc_agent.plan_builder.validators import validate_patch
from openmc_agent.assembly3d_guard import validate_assembly3d_plan


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture_raw(variant: str) -> list[dict]:
    """Load raw patch dicts (not parsed) from fixture file."""
    raw = json.loads((_FIXTURE_DIR / f"vera3_{variant}_patches.json").read_text("utf-8"))
    return raw["patches"]


# ---------------------------------------------------------------------------
# Helper: VERA3 3B requirement text
# ---------------------------------------------------------------------------

_VERA3_3B_REQ = (
    "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, "
    "三维, 定位格架, Pyrex rods, thimble plugs, 17x17 lattice"
)


def _init_3b_state() -> PlanBuildState:
    decision = should_use_incremental_planning(_VERA3_3B_REQ)
    state = initialize_plan_build_state(
        requirement=_VERA3_3B_REQ,
        decision=decision,
        benchmark_id="VERA3",
        selected_variant="3B",
    )
    return state


# ---------------------------------------------------------------------------
# 1. Executor generates patches in dependency order
# ---------------------------------------------------------------------------


def test_executor_generates_in_dependency_order() -> None:
    """Minimal fake LLM that returns valid patches."""
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "TEST",
                     "lattice_size": [17, 17], "pin_pitch_cm": 1.26,
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.0},
            {"material_id": "water", "name": "Water", "role": "coolant", "density_g_cm3": 0.74},
            {"material_id": "clad", "name": "Zircaloy-4", "role": "cladding",
             "density_g_cm3": 6.56, "composition": {"Zr": 1.0},
             "composition_status": "approximate",
             "warnings": ["approximated as pure Zr"]},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fuel_pin", "kind": "fuel_pin", "cells": [
                {"id": "fuel", "role": "fuel", "material_id": "fuel"},
                {"id": "clad", "role": "cladding", "material_id": "clad"},
                {"id": "water", "role": "coolant", "material_id": "water"},
            ]},
            {"universe_id": "gt", "kind": "guide_tube", "cells": [
                {"id": "iw", "role": "coolant", "material_id": "water"},
                {"id": "wall", "role": "cladding", "material_id": "clad"},
                {"id": "ow", "role": "background", "material_id": "water"},
            ]},
        ]}),
        json.dumps({"patch_type": "pin_map", "lattice_size": [17, 17],
                     "default_universe_id": "fuel_pin",
                     "coordinate_convention": {"index_base": 0},
                     "guide_tube_coords": [[2, 2]]}),
        json.dumps({"patch_type": "axial_layers", "layers": [
            {"layer_id": "fuel", "role": "active_fuel", "z_min_cm": 0.0, "z_max_cm": 100.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ]}),
        json.dumps({"patch_type": "axial_overlays", "overlays": [
            {"overlay_id": "g1", "overlay_kind": "spacer_grid",
             "z_min_cm": 10.0, "z_max_cm": 12.0,
             "target_lattice_id": "assembly_lattice", "material_id": "clad",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ]}),
    ]
    fake = FakePatchLLM(responses)
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ,
        state=state,
        llm_client=fake,
        max_patch_attempts=1,
    )
    assert result.ok is True
    assert result.assembled_plan is not None

    # Check generation order: facts was the first LLM call.
    first_prompt = fake.prompts[0]
    assert "patch_type=\"facts\"" in first_prompt or "patch_type: facts" in first_prompt

    # Check valid patch types.
    valid_types = {e.patch_type for e in state.patches.values() if e.status == "valid"}
    assert "facts" in valid_types
    assert "materials" in valid_types
    assert "universes" in valid_types
    assert "pin_map" in valid_types
    assert "axial_layers" in valid_types
    assert "axial_overlays" in valid_types
    assert "settings" in valid_types


# ---------------------------------------------------------------------------
# 2. Executor skips already valid patch
# ---------------------------------------------------------------------------


def test_executor_skips_valid_patch() -> None:
    state = _init_3b_state()
    # Pre-add a valid facts patch.
    facts = FactsPatch(benchmark_id="VERA3", selected_variant="3B")
    env = PlanPatchEnvelope(
        patch_id="pre_facts", patch_type="facts",
        content=facts.model_dump(mode="json"), status="valid",
    )
    state.add_patch(env)

    responses = [
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.0},
            {"material_id": "water", "name": "Water", "role": "coolant", "density_g_cm3": 0.74},
            {"material_id": "clad", "name": "Zircaloy-4", "role": "cladding",
             "density_g_cm3": 6.56, "composition": {"Zr": 1.0},
             "composition_status": "approximate",
             "warnings": ["approximated as pure Zr"]},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fuel_pin", "kind": "fuel_pin", "cells": [
                {"id": "fuel", "role": "fuel", "material_id": "fuel"},
                {"id": "clad", "role": "cladding", "material_id": "clad"},
                {"id": "water", "role": "coolant", "material_id": "water"},
            ]},
            {"universe_id": "gt", "kind": "guide_tube", "cells": [
                {"id": "iw", "role": "coolant", "material_id": "water"},
                {"id": "wall", "role": "cladding", "material_id": "clad"},
                {"id": "ow", "role": "background", "material_id": "water"},
            ]},
        ]}),
        json.dumps({"patch_type": "pin_map", "lattice_size": [17, 17],
                     "default_universe_id": "fuel_pin",
                     "coordinate_convention": {"index_base": 0},
                     "guide_tube_coords": [[2, 2]]}),
        json.dumps({"patch_type": "axial_layers", "layers": [
            {"layer_id": "fuel", "role": "active_fuel", "z_min_cm": 0.0, "z_max_cm": 100.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ]}),
        json.dumps({"patch_type": "axial_overlays", "overlays": [
            {"overlay_id": "g1", "overlay_kind": "spacer_grid",
             "z_min_cm": 10.0, "z_max_cm": 12.0,
             "target_lattice_id": "assembly_lattice", "material_id": "clad",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ]}),
    ]
    fake = FakePatchLLM(responses)
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=1,
    )
    assert result.ok is True

    # The first LLM call should be for materials, NOT facts.
    first_prompt = fake.prompts[0]
    assert "patch_type=\"materials\"" in first_prompt or "patch_type: materials" in first_prompt

    # Event should record the skip.
    event_types = [e.event_type for e in state.build_log]
    assert "planning.patch_skipped_already_valid" in event_types


# ---------------------------------------------------------------------------
# 3. Context propagation from facts to pin_map
# ---------------------------------------------------------------------------


def test_context_propagation_facts_to_pin_map() -> None:
    state = _init_3b_state()
    facts = FactsPatch(
        benchmark_id="VERA3", selected_variant="3B",
        lattice_size=(17, 17),
        expected_pyrex_count=16, expected_thimble_plug_count=8,
        has_spacer_grids=True, has_special_pin_map=True,
    )
    env = PlanPatchEnvelope(
        patch_id="pre_facts", patch_type="facts",
        content=facts.model_dump(mode="json"), status="valid",
    )
    state.add_patch(env)

    ctx = build_generation_context_from_state(state, "pin_map")
    assert ctx.benchmark_id == "VERA3"
    assert ctx.selected_variant == "3B"
    assert ctx.expected_counts.get("expected_pyrex_count") == 16
    assert ctx.expected_counts.get("expected_thimble_plug_count") == 8


# ---------------------------------------------------------------------------
# 4. Materials context propagates to overlays
# ---------------------------------------------------------------------------


def test_context_materials_to_overlays() -> None:
    state = _init_3b_state()
    mat_content = {
        "patch_type": "materials",
        "materials": [
            {"material_id": "inconel718", "name": "Inconel-718", "role": "grid_inconel",
             "density_g_cm3": 8.19, "composition": {"Ni": 1.0},
             "composition_status": "approximate",
             "warnings": ["approximated as pure Ni"]},
        ],
    }
    env = PlanPatchEnvelope(
        patch_id="pre_mat", patch_type="materials",
        content=mat_content, status="valid",
    )
    state.add_patch(env)

    ctx = build_generation_context_from_state(state, "axial_overlays")
    assert "inconel718" in ctx.known_material_ids


# ---------------------------------------------------------------------------
# 5. Deterministic settings patch created
# ---------------------------------------------------------------------------


def test_deterministic_settings_patch() -> None:
    state = _init_3b_state()
    settings = build_deterministic_settings_patch(state)
    assert settings.source_strategy == "active_fuel_box"
    assert settings.plot_strategy == "full_assembly"
    assert settings.cross_sections_runtime_required is True
    assert settings.tallies_required_for_smoke_test is False


def test_executor_uses_deterministic_settings() -> None:
    """Settings should not need an LLM call."""
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "TEST",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.0},
            {"material_id": "water", "name": "Water", "role": "coolant", "density_g_cm3": 0.74},
            {"material_id": "clad", "name": "Zircaloy-4", "role": "cladding",
             "density_g_cm3": 6.56, "composition": {"Zr": 1.0},
             "composition_status": "approximate",
             "warnings": ["approx"]},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fuel_pin", "kind": "fuel_pin", "cells": [
                {"id": "fuel", "role": "fuel", "material_id": "fuel"},
                {"id": "clad", "role": "cladding", "material_id": "clad"},
                {"id": "water", "role": "coolant", "material_id": "water"},
            ]},
        ]}),
        json.dumps({"patch_type": "pin_map", "lattice_size": [17, 17],
                     "default_universe_id": "fuel_pin",
                     "coordinate_convention": {"index_base": 0}}),
        json.dumps({"patch_type": "axial_layers", "layers": [
            {"layer_id": "fuel", "role": "active_fuel", "z_min_cm": 0.0, "z_max_cm": 100.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ]}),
        json.dumps({"patch_type": "axial_overlays", "overlays": [
            {"overlay_id": "g1", "overlay_kind": "spacer_grid",
             "z_min_cm": 10.0, "z_max_cm": 12.0,
             "target_lattice_id": "assembly_lattice", "material_id": "clad",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ]}),
    ]
    fake = FakePatchLLM(responses)
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=1,
    )
    assert result.ok is True
    # Settings envelope should be source="deterministic"
    settings_env = next(
        e for e in state.patches.values() if e.patch_type == "settings"
    )
    assert settings_env.source == "deterministic"
    event_types = [e.event_type for e in state.build_log]
    assert "planning.deterministic_settings_patch_created" in event_types


# ---------------------------------------------------------------------------
# 6. Pin_map invalid first then retry
# ---------------------------------------------------------------------------


def test_pin_map_retry_only_current_patch() -> None:
    """PinMapPatch first has overlap, second is valid."""
    base_responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
            {"universe_id": "gt", "kind": "guide_tube", "cells": [
                {"id": "iw", "role": "coolant"}, {"id": "w", "role": "cladding"},
                {"id": "ow", "role": "background"},
            ]},
        ]}),
    ]
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    good_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
    })
    tail_responses = [
        json.dumps({"patch_type": "axial_layers", "layers": [
            {"layer_id": "f", "role": "active_fuel", "z_min_cm": 0.0, "z_max_cm": 100.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ]}),
        json.dumps({"patch_type": "axial_overlays", "overlays": [
            {"overlay_id": "g1", "overlay_kind": "spacer_grid",
             "z_min_cm": 10.0, "z_max_cm": 12.0,
             "target_lattice_id": "assembly_lattice", "material_id": "m",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ]}),
    ]
    fake = FakePatchLLM(base_responses + [bad_pin, good_pin] + tail_responses)
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=2,
    )
    assert result.ok is True
    # Earlier patches (facts/materials/universes) should each have 1 LLM call.
    # pin_map should have 2 LLM calls (bad + good).
    pin_map_prompts = [p for p in fake.prompts if "pin_map" in p]
    assert len(pin_map_prompts) >= 2


# ---------------------------------------------------------------------------
# 7. Invalid JSON in axial_layers retries only axial_layers
# ---------------------------------------------------------------------------


def test_axial_layers_invalid_json_retries_only_axial() -> None:
    base_responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
        json.dumps({"patch_type": "pin_map", "lattice_size": [17, 17],
                     "default_universe_id": "fp",
                     "coordinate_convention": {"index_base": 0}}),
    ]
    bad_json = '{"patch_type": "axial_layers", broken'
    good_json = json.dumps({"patch_type": "axial_layers", "layers": [
        {"layer_id": "f", "role": "active_fuel", "z_min_cm": 0.0, "z_max_cm": 100.0,
         "fill_type": "lattice", "fill_id": "assembly_lattice"},
    ]})
    tail = [
        json.dumps({"patch_type": "axial_overlays", "overlays": [
            {"overlay_id": "g1", "overlay_kind": "spacer_grid",
             "z_min_cm": 10.0, "z_max_cm": 12.0,
             "target_lattice_id": "assembly_lattice", "material_id": "m",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ]}),
    ]
    fake = FakePatchLLM(base_responses + [bad_json, good_json] + tail)
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=2,
    )
    assert result.ok is True
    # Earlier patches should be valid and unchanged.
    assert any(e.patch_type == "facts" and e.status == "valid" for e in state.patches.values())
    assert any(e.patch_type == "materials" and e.status == "valid" for e in state.patches.values())


# ---------------------------------------------------------------------------
# 8. Repeated pin_map failure stops executor
# ---------------------------------------------------------------------------


def test_repeated_pin_map_failure_stops() -> None:
    base_responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
    ]
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    fake = FakePatchLLM(base_responses + [bad_pin, bad_pin])
    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=2,
        reference_patch_policy="off",
    )
    assert result.ok is False
    # Earlier patches should still be valid.
    assert any(e.patch_type == "facts" and e.status == "valid" for e in state.patches.values())
    assert any(e.patch_type == "materials" and e.status == "valid" for e in state.patches.values())
    # No assembled plan.
    assert result.assembled_plan is None
    # Summary should report failed patch type.
    assert result.summary.get("failed_patch_type") == "pin_map"


# ---------------------------------------------------------------------------
# 9. Valid patch remains unchanged after later failure
# ---------------------------------------------------------------------------


def test_valid_patch_unchanged_after_later_failure() -> None:
    state = _init_3b_state()
    facts_env = PlanPatchEnvelope(
        patch_id="pre_facts", patch_type="facts",
        content=FactsPatch(benchmark_id="VERA3").model_dump(mode="json"),
        status="valid",
    )
    state.add_patch(facts_env)

    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    # Provide enough responses to get through materials/universes then fail pin_map.
    responses = [
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
        bad_pin, bad_pin,
    ]
    fake = FakePatchLLM(responses)
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ, state=state,
        llm_client=fake, max_patch_attempts=2,
        reference_patch_policy="off",
    )
    assert result.ok is False
    # The pre-existing facts patch should be unchanged.
    assert state.patches["pre_facts"].status == "valid"
    assert state.patches["pre_facts"].content.get("benchmark_id") == "VERA3"


# ---------------------------------------------------------------------------
# 13. VERA3 3B fake incremental execution (full)
# ---------------------------------------------------------------------------


def test_vera3_3b_full_incremental_execution() -> None:
    """Full VERA3 3B incremental execution with fake LLM."""
    raw_patches = _load_fixture_raw("3b")
    # Use first 6 patches as LLM responses (skip settings — deterministic).
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake = FakePatchLLM(llm_responses)

    state = _init_3b_state()
    result = run_incremental_planning(
        requirement=_VERA3_3B_REQ,
        state=state,
        llm_client=fake,
        max_patch_attempts=1,
    )
    assert result.ok is True
    assert result.assembled_plan is not None

    plan_data = result.assembled_plan
    cm = plan_data["complex_model"]

    # Lattice checks.
    lattice = cm["lattices"][0]
    pattern = lattice["universe_pattern"]
    assert len(pattern) == 17
    assert all(len(row) == 17 for row in pattern)
    flat = [uid for row in pattern for uid in row]
    assert sum(1 for u in flat if u == "pyrex_rod") == 16
    assert sum(1 for u in flat if u == "thimble_plug") == 8
    assert sum(1 for u in flat if u == "instrument_tube") == 1
    assert sum(1 for u in flat if u == "fuel_pin") == 264

    # Axial layers + overlays.
    assert len(cm["core"]["axial_layers"]) == 12
    assert len(cm["core"]["axial_overlays"]) == 8

    # assembly3d guard.
    from openmc_agent.schemas import SimulationPlan
    plan = SimulationPlan.model_validate(plan_data)
    issues = validate_assembly3d_plan(plan, requirement=_VERA3_3B_REQ)
    error_codes = [i.code for i in issues if i.severity == "error"]
    assert "assembly3d.axial_layers_required" not in error_codes
    assert "assembly3d.default_z_extent_for_axial_problem" not in error_codes
    assert "assembly3d.spacer_grid_material_slab" not in error_codes


# ---------------------------------------------------------------------------
# 14. VERA3 3B pin_map response size remains small
# ---------------------------------------------------------------------------


def test_vera3_3b_pin_map_response_small() -> None:
    raw_patches = _load_fixture_raw("3b")
    pin_map_raw = next(p for p in raw_patches if p["patch_type"] == "pin_map")
    pin_map_str = json.dumps(pin_map_raw)
    # Should be well under 25K.
    assert len(pin_map_str) < 2000, f"pin_map patch is {len(pin_map_str)} bytes"
    # Should NOT contain universe_pattern.
    assert "universe_pattern" not in pin_map_raw


# ---------------------------------------------------------------------------
# Default task order / required patches
# ---------------------------------------------------------------------------


def test_default_task_order_3d_assembly() -> None:
    state = _init_3b_state()
    order = default_patch_task_order(state)
    assert "facts" in order
    assert "axial_overlays" in order  # spacer grids expected
    assert order.index("facts") < order.index("materials")
    assert order.index("materials") < order.index("universes")


def test_required_patches_includes_overlays_for_spacer() -> None:
    state = _init_3b_state()
    required = required_patch_types_for_state(state)
    assert "axial_overlays" in required
    assert "pin_map" in required  # special pin map
    assert "axial_layers" in required
