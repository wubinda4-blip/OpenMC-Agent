"""Tests for LLM patch generator with fake LLM (Phase 4)."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import (
    FactsPatch,
    MaterialsPatch,
    PinMapPatch,
    parse_patch_content,
)
from openmc_agent.plan_builder.patch_generator import (
    FakePatchLLM,
    PatchGenerationContext,
    generate_patch,
    parse_llm_patch_json,
)
from openmc_agent.plan_builder.state import (
    PlanBuildState,
    PlanPatchEnvelope,
    add_validated_patch_to_state,
    generate_and_add_patch_to_state,
)
from openmc_agent.plan_builder.validators import validate_patch


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _patch_json(patch_type: str, **kwargs: object) -> str:
    """Build a minimal valid patch JSON string for testing."""
    base = {"patch_type": patch_type}
    base.update(kwargs)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# 2. FactsPatch generation success
# ---------------------------------------------------------------------------


def test_facts_generation_success() -> None:
    raw = json.dumps({
        "patch_type": "facts",
        "benchmark_id": "VERA3",
        "selected_variant": "3A",
        "lattice_size": [17, 17],
        "pin_pitch_cm": 1.26,
        "has_axial_geometry": True,
        "has_spacer_grids": True,
    })
    fake = FakePatchLLM([raw])
    result = generate_patch(
        patch_type="facts",
        requirement="VERA3 3A benchmark assembly",
        llm_client=fake,
        max_attempts=1,
    )
    assert result.ok is True
    assert result.envelope is not None
    assert result.envelope.status == "valid"
    assert result.envelope.patch_type == "facts"
    assert len(result.attempts) == 1
    assert result.attempts[0].parsed is True
    assert result.attempts[0].validated is True


# ---------------------------------------------------------------------------
# 3. PinMapPatch generation does not require full 17x17 map
# ---------------------------------------------------------------------------


def test_pin_map_generation_no_full_lattice() -> None:
    raw = json.dumps({
        "patch_type": "pin_map",
        "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        "guide_tube_coords": [[3, 6], [3, 9], [3, 12]],
    })
    fake = FakePatchLLM([raw])
    result = generate_patch(
        patch_type="pin_map",
        requirement="17x17 assembly",
        llm_client=fake,
        max_attempts=1,
    )
    assert result.ok is True
    assert result.parsed_patch is not None
    # Should only have 3 coords, not 289
    assert len(result.parsed_patch.get("guide_tube_coords", [])) == 3
    # Full lattice pattern must NOT be present
    assert "universe_pattern" not in result.parsed_patch


# ---------------------------------------------------------------------------
# 4. PinMapPatch overlap triggers retry only current patch
# ---------------------------------------------------------------------------


def test_pin_map_overlap_triggers_retry() -> None:
    bad_raw = json.dumps({
        "patch_type": "pin_map",
        "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 0, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    good_raw = json.dumps({
        "patch_type": "pin_map",
        "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 0, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[6, 6]],
    })
    fake = FakePatchLLM([bad_raw, good_raw])
    result = generate_patch(
        patch_type="pin_map",
        requirement="17x17 assembly",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is True
    assert len(result.attempts) == 2
    assert result.attempts[0].validated is False
    assert result.attempts[1].validated is True
    # First attempt should have coord_overlap issue
    first_codes = [i["code"] for i in result.attempts[0].issues]
    assert "patch.pin_map.coord_overlap" in first_codes


# ---------------------------------------------------------------------------
# 5. Invalid JSON retry
# ---------------------------------------------------------------------------


def test_invalid_json_retry() -> None:
    bad_raw = '{"patch_type": "materials", "materials": [{"material_id": "fuel", broken'
    good_raw = json.dumps({
        "patch_type": "materials",
        "materials": [
            {"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.0}
        ],
    })
    fake = FakePatchLLM([bad_raw, good_raw])
    result = generate_patch(
        patch_type="materials",
        requirement="UO2 fuel",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is True
    assert len(result.attempts) == 2
    assert result.attempts[0].parsed is False
    first_codes = [i["code"] for i in result.attempts[0].issues]
    assert "patch_generation.json_parse_error" in first_codes


# ---------------------------------------------------------------------------
# 6. Validation failure max attempts exceeded
# ---------------------------------------------------------------------------


def test_max_attempts_exceeded() -> None:
    # AxialLayersPatch with inverted z range (always fails validation with error)
    bad_raw = json.dumps({
        "patch_type": "axial_layers",
        "layers": [
            {"layer_id": "active_fuel", "role": "active_fuel",
             "z_min_cm": 100.0, "z_max_cm": 50.0,
             "fill_type": "lattice", "fill_id": "assembly_lattice"}
        ],
    })
    fake = FakePatchLLM([bad_raw, bad_raw])
    result = generate_patch(
        patch_type="axial_layers",
        requirement="3D assembly",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is False
    codes = [i["code"] for i in result.issues]
    assert "patch_generation.max_attempts_exceeded" in codes
    assert len(result.attempts) == 2


# ---------------------------------------------------------------------------
# 10. SettingsPatch treats cross sections as runtime
# ---------------------------------------------------------------------------


def test_settings_generation_ok() -> None:
    raw = json.dumps({"patch_type": "settings"})
    fake = FakePatchLLM([raw])
    result = generate_patch(
        patch_type="settings",
        requirement="assembly",
        llm_client=fake,
        max_attempts=1,
    )
    assert result.ok is True
    assert result.parsed_patch is not None
    assert result.parsed_patch["cross_sections_runtime_required"] is True
    assert result.parsed_patch["tallies_required_for_smoke_test"] is False


# ---------------------------------------------------------------------------
# 11. generate_and_add_patch_to_state success
# ---------------------------------------------------------------------------


def test_generate_and_add_to_state_success() -> None:
    state = PlanBuildState(state_id="test_gen", requirement_text="VERA3 3A")
    raw = json.dumps({"patch_type": "facts", "benchmark_id": "VERA3", "selected_variant": "3A"})
    fake = FakePatchLLM([raw])

    state = generate_and_add_patch_to_state(
        state, "facts", "VERA3 3A benchmark",
        llm_client=fake, max_attempts=1,
    )
    assert len(state.patches) == 1
    env = list(state.patches.values())[0]
    assert env.status == "valid"
    event_types = [e.event_type for e in state.build_log]
    assert "planning.patch_generation_started" in event_types
    assert "planning.patch_generated" in event_types


# ---------------------------------------------------------------------------
# 12. generate_and_add_patch_to_state failure preserves valid patches
# ---------------------------------------------------------------------------


def test_generation_failure_preserves_valid_patches() -> None:
    state = PlanBuildState(state_id="test_preserve", requirement_text="3D assembly")

    # First add a valid facts patch manually.
    facts = FactsPatch(benchmark_id="VERA3", selected_variant="3A")
    env = PlanPatchEnvelope(
        patch_id="facts_manual", patch_type="facts",
        content=facts.model_dump(mode="json"), status="valid",
    )
    state.add_patch(env)
    assert len(state.get_valid_patches()) == 1

    # Now try to generate axial_layers with a bad LLM.
    bad_raw = json.dumps({
        "patch_type": "axial_layers",
        "layers": [{"layer_id": "active_fuel", "role": "active_fuel",
                     "z_min_cm": 100.0, "z_max_cm": 50.0,
                     "fill_type": "lattice", "fill_id": "assembly_lattice"}],
    })
    fake = FakePatchLLM([bad_raw, bad_raw])
    state = generate_and_add_patch_to_state(
        state, "axial_layers", "3D assembly",
        llm_client=fake, max_attempts=2,
    )

    # The facts patch should still be valid.
    assert state.patches["facts_manual"].status == "valid"
    assert len(state.get_valid_patches()) == 1

    # Failure event should be recorded.
    event_types = [e.event_type for e in state.build_log]
    assert "planning.patch_generation_failed" in event_types


# ---------------------------------------------------------------------------
# Phase 7B: forbidden full-plan output detection
# ---------------------------------------------------------------------------


def test_facts_patch_rejects_full_plan_output() -> None:
    """LLM returns a full SimulationPlan instead of a FactsPatch → rejected."""
    full_plan_raw = json.dumps({
        "schema_version": "simulation_plan.v2",
        "complex_model": {"name": "VERA3", "kind": "assembly"},
        "capability_report": {"renderability": "none"},
    })
    fake = FakePatchLLM([full_plan_raw, full_plan_raw])
    result = generate_patch(
        patch_type="facts",
        requirement="VERA3 3B",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is False
    all_codes = [i["code"] for i in result.issues]
    assert "patch_generation.full_plan_output_forbidden" in all_codes


def test_retry_after_full_plan_succeeds() -> None:
    """Full plan first, then valid patch → success on second attempt."""
    full_plan_raw = json.dumps({
        "schema_version": "simulation_plan.v2",
        "complex_model": {"name": "VERA3", "kind": "assembly"},
    })
    valid_facts = json.dumps({
        "patch_type": "facts",
        "benchmark_id": "VERA3",
        "selected_variant": "3B",
        "lattice_size": [17, 17],
        "has_axial_geometry": True,
    })
    fake = FakePatchLLM([full_plan_raw, valid_facts])
    result = generate_patch(
        patch_type="facts",
        requirement="VERA3 3B",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is True
    assert len(result.attempts) == 2
    assert result.attempts[0].contains_full_plan_markers is True
    first_codes = [i["code"] for i in result.attempts[0].issues]
    assert "patch_generation.full_plan_output_forbidden" in first_codes


def test_pin_map_full_lattice_forbidden_error() -> None:
    """Pin_map with >80 coords → forbidden error (not warning)."""
    huge_coords = [[i, i] for i in range(100)]
    raw = json.dumps({
        "patch_type": "pin_map",
        "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": huge_coords,
    })
    fake = FakePatchLLM([raw, raw])
    result = generate_patch(
        patch_type="pin_map",
        requirement="17x17",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is False
    all_codes = [i["code"] for i in result.issues]
    assert "patch_generation.pin_map_full_lattice_forbidden" in all_codes


def test_non_json_natural_language_response() -> None:
    """Natural language response → json_parse_error."""
    fake = FakePatchLLM([
        "I cannot generate this patch because I need more information.",
        "I cannot generate this patch.",
    ])
    result = generate_patch(
        patch_type="facts",
        requirement="test",
        llm_client=fake,
        max_attempts=2,
    )
    assert result.ok is False
    all_codes = [i["code"] for i in result.issues]
    assert any("json_parse_error" in c for c in all_codes)


def test_prompt_has_output_contract() -> None:
    """Verify the hardened prompt contains the output contract."""
    from openmc_agent.plan_builder.patch_prompts import build_patch_prompt
    prompt = build_patch_prompt("facts", "test requirement", None)
    assert "CRITICAL OUTPUT CONTRACT" in prompt
    assert 'patch_type="facts"' in prompt
    assert "NOT generating a SimulationPlan" in prompt
    assert "complex_model" in prompt  # forbidden markers listed


def test_retry_prompt_for_full_plan_is_explicit() -> None:
    """Retry prompt for full_plan output should explicitly mention the issue."""
    from openmc_agent.plan_builder.patch_prompts import build_retry_prompt
    issues = [
        {"code": "patch_generation.full_plan_output_forbidden", "severity": "error", "message": "test"},
    ]
    prompt = build_retry_prompt("facts", "req", None, issues, 1)
    assert "REJECTED" in prompt or "rejected" in prompt
    assert "full SimulationPlan" in prompt or "full plan" in prompt.lower()


# ---------------------------------------------------------------------------
# 13. VERA3 3B pin_map patch generation
# ---------------------------------------------------------------------------


def test_vera3_3b_pin_map_generation() -> None:
    raw = json.dumps({
        "patch_type": "pin_map",
        "variant": "3B",
        "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        "instrument_tube_coords": [[9, 9]],
        "pyrex_rod_coords": [
            [3, 6], [4, 4], [6, 3], [3, 12], [4, 14], [6, 15], [12, 3], [14, 4],
            [15, 6], [12, 15], [14, 14], [15, 12], [9, 6], [9, 12], [6, 9], [12, 9],
        ],
        "thimble_plug_coords": [
            [3, 9], [6, 6], [6, 12], [9, 3], [9, 15], [12, 6], [12, 12], [15, 9],
        ],
    })
    fake = FakePatchLLM([raw])
    ctx = PatchGenerationContext(
        benchmark_id="VERA3", selected_variant="3B",
        expected_counts={"expected_pyrex_rod_count": 16, "expected_thimble_plug_count": 8},
        known_universe_ids=["fuel_pin", "pyrex_rod", "thimble_plug", "instrument_tube"],
    )
    result = generate_patch(
        patch_type="pin_map",
        requirement="VERA3 3B benchmark",
        context=ctx,
        llm_client=fake,
        max_attempts=1,
    )
    assert result.ok is True
    assert len(result.parsed_patch["pyrex_rod_coords"]) == 16
    assert len(result.parsed_patch["thimble_plug_coords"]) == 8
    # Total patch JSON should be small
    patch_bytes = len(json.dumps(result.parsed_patch))
    assert patch_bytes < 2000, f"pin_map patch is {patch_bytes} bytes"


# ---------------------------------------------------------------------------
# 14. VERA3 3B axial_overlays generation
# ---------------------------------------------------------------------------


def test_vera3_3b_axial_overlays_generation() -> None:
    grids = []
    for i in range(8):
        grids.append({
            "overlay_id": f"grid_{i}",
            "overlay_kind": "spacer_grid",
            "z_min_cm": 10.0 + i * 50,
            "z_max_cm": 12.0 + i * 50,
            "target_lattice_id": "assembly_lattice",
            "material_id": "grid_mat",
            "geometry_mode": "homogenized_open_region",
            "through_path_preserved": True,
        })
    raw = json.dumps({"patch_type": "axial_overlays", "overlays": grids})
    fake = FakePatchLLM([raw])
    result = generate_patch(
        patch_type="axial_overlays",
        requirement="spacer grids",
        llm_client=fake,
        max_attempts=1,
    )
    assert result.ok is True
    assert len(result.parsed_patch["overlays"]) == 8
    for ov in result.parsed_patch["overlays"]:
        assert ov["geometry_mode"] == "homogenized_open_region"
        assert ov["through_path_preserved"] is True


# ---------------------------------------------------------------------------
# 15. VERA3 3B patch generation + assembler smoke at unit level
# ---------------------------------------------------------------------------


def test_vera3_3b_generation_plus_assembler_smoke() -> None:
    """Generate facts/pin_map/axial_layers/axial_overlays via fake LLM,
    use fixture materials/universes/settings, assemble, check guard."""
    from tests.test_vera3_patch_fixtures import _load_fixture

    # Load fixture patches for materials/universes/settings.
    fixture_patches = _load_fixture("3b")
    materials_patch = next(p for p in fixture_patches if p.patch_type == "materials")
    universes_patch = next(p for p in fixture_patches if p.patch_type == "universes")
    settings_patch = next(p for p in fixture_patches if p.patch_type == "settings")

    # Generate facts/pin_map/axial_layers/axial_overlays via fake LLM.
    facts_raw = json.dumps({
        "patch_type": "facts",
        "benchmark_id": "VERA3", "selected_variant": "3B",
        "lattice_size": [17, 17], "pin_pitch_cm": 1.26,
        "assembly_pitch_cm": 21.50,
        "has_axial_geometry": True, "has_spacer_grids": True,
        "has_special_pin_map": True,
        "active_fuel_region_cm": [11.951, 377.711],
        "axial_domain_cm": [-55.0, 463.937],
        "expected_pyrex_count": 16, "expected_thimble_plug_count": 8,
    })
    pin_map_raw = json.dumps({
        "patch_type": "pin_map", "variant": "3B",
        "lattice_size": [17, 17], "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        "instrument_tube_coords": [[9, 9]],
        "pyrex_rod_coords": [
            [3,6],[4,4],[6,3],[3,12],[4,14],[6,15],[12,3],[14,4],
            [15,6],[12,15],[14,14],[15,12],[9,6],[9,12],[6,9],[12,9],
        ],
        "thimble_plug_coords": [
            [3,9],[6,6],[6,12],[9,3],[9,15],[12,6],[12,12],[15,9],
        ],
    })
    axial_layers_raw = json.dumps({
        "patch_type": "axial_layers",
        "axial_domain_cm": [-55.0, 463.937],
        "layers": [
            {"layer_id": "active_fuel", "role": "active_fuel",
             "z_min_cm": 11.951, "z_max_cm": 377.711,
             "fill_type": "lattice", "fill_id": "assembly_lattice"},
        ],
    })
    overlays_raw = json.dumps({
        "patch_type": "axial_overlays",
        "overlays": [
            {"overlay_id": "grid_0", "overlay_kind": "spacer_grid",
             "z_min_cm": 11.951, "z_max_cm": 15.817,
             "target_lattice_id": "assembly_lattice", "material_id": "inconel718",
             "geometry_mode": "homogenized_open_region", "through_path_preserved": True},
        ],
    })

    fake = FakePatchLLM([facts_raw, pin_map_raw, axial_layers_raw, overlays_raw])
    ctx = PatchGenerationContext(benchmark_id="VERA3", selected_variant="3B")

    generated: list = []
    for ptype in ("facts", "pin_map", "axial_layers", "axial_overlays"):
        result = generate_patch(
            patch_type=ptype, requirement="VERA3 3B",
            context=ctx, llm_client=fake, max_attempts=1,
        )
        assert result.ok, f"{ptype} generation failed: {[i['code'] for i in result.issues if i.get('severity') == 'error']}"
        parsed = parse_patch_content(ptype, result.parsed_patch)
        generated.append(parsed)

    # Combine generated + fixture patches.
    all_patches = generated + [materials_patch, universes_patch, settings_patch]

    # Assemble.
    asm_result = assemble_simulation_plan_from_patches(all_patches)
    assert asm_result.ok, [
        (i.code, i.message[:80]) for i in asm_result.issues if i.severity == "error"
    ]

    # Check assembly3d guard.
    from openmc_agent.assembly3d_guard import validate_assembly3d_plan
    issues = validate_assembly3d_plan(
        asm_result.plan,
        requirement="VERA3 3B benchmark: 3D assembly with axial layers, spacer grids",
    )
    error_codes = [i.code for i in issues if i.severity == "error"]
    assert "assembly3d.axial_layers_required" not in error_codes


# ---------------------------------------------------------------------------
# parse_llm_patch_json helper tests
# ---------------------------------------------------------------------------


def test_parse_json_pure() -> None:
    result = parse_llm_patch_json('{"patch_type": "facts"}', "facts")
    assert result["patch_type"] == "facts"


def test_parse_json_markdown_fenced() -> None:
    result = parse_llm_patch_json('```json\n{"patch_type": "facts"}\n```', "facts")
    assert result["patch_type"] == "facts"


def test_parse_json_with_preamble() -> None:
    result = parse_llm_patch_json(
        'Here is the patch:\n{"patch_type": "facts"}\nDone.', "facts"
    )
    assert result["patch_type"] == "facts"


def test_parse_json_empty_raises() -> None:
    from openmc_agent.plan_builder.patches import PatchParseError
    with pytest.raises(PatchParseError):
        parse_llm_patch_json("", "facts")
