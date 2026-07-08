"""Tests for incremental executor graph integration (Phase 6).

Verifies that when ``should_use_incremental_planning`` returns
``mode="incremental"``, the graph routes to the incremental patch executor
instead of the monolithic full-plan LLM call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.graph import build_plan_graph
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.schemas import SimulationPlan
from openmc_agent.tools import ToolResult
from openmc_agent.plan_builder.patch_generator import FakePatchLLM


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture_raw(variant: str) -> list[dict]:
    raw = json.loads((_FIXTURE_DIR / f"vera3_{variant}_patches.json").read_text("utf-8"))
    return raw["patches"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_generate_plan_should_not_be_called(*, requirement: str, schema, model: str):
    """Fail if the monolithic planner is called in incremental mode."""
    raise AssertionError(
        "monolithic generate_plan should NOT be called in incremental mode"
    )


def _fake_generate_plan_monolithic(*, requirement: str, schema, model: str):
    """Return a minimal valid SimulationPlan (for monolithic fallback tests)."""
    from tests.test_graph import make_simulation_plan
    return StructuredOutputResult(ok=True, value=make_simulation_plan())


def _fake_export_xml(model_path):
    return ToolResult(name="export_xml", ok=True, returncode=0, artifacts=[])


def _fake_plot(run_dir):
    return ToolResult(name="run_geometry_plots", ok=True, returncode=0)


def _fake_smoke(run_dir, plan):
    return ToolResult(name="run_smoke_test", ok=True, returncode=0)


_VERA3_3B_REQ = (
    "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, "
    "三维, 定位格架, Pyrex rods, thimble plugs, 17x17 lattice"
)


_SIMPLE_2D_REQ = (
    "Build a UO2 fuel pin cell with cladding and water moderator, "
    "reflective radial boundary"
)


# ---------------------------------------------------------------------------
# 1. Simple 2D case still uses monolithic path
# ---------------------------------------------------------------------------


def test_simple_2d_uses_monolithic(tmp_path: Path) -> None:
    """Simple 2D requirement should NOT trigger the incremental executor."""
    monolithic_called = {"count": 0}

    def fake_generate_plan(*, requirement, schema, model):
        monolithic_called["count"] += 1
        from tests.test_graph import make_simulation_plan
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    # patch_llm_client that should NOT be called
    def patch_client_should_not_be_called(prompt: str) -> str:
        raise AssertionError("patch LLM should NOT be called for simple 2D")

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=patch_client_should_not_be_called,
    )
    state = graph.invoke({
        "requirement": _SIMPLE_2D_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })
    assert monolithic_called["count"] >= 1
    pmd = state.get("planning_mode_decision", {})
    assert pmd.get("mode") == "monolithic"


# ---------------------------------------------------------------------------
# 2. VERA3 3B mode calls incremental executor
# ---------------------------------------------------------------------------


def test_vera3_3b_calls_incremental_executor(tmp_path: Path) -> None:
    """VERA3 3B should trigger incremental executor, not monolithic."""
    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake_llm = FakePatchLLM(llm_responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    # Planning mode should be incremental.
    pmd = state.get("planning_mode_decision", {})
    assert pmd.get("mode") == "incremental"

    # Incremental execution result should be present and ok.
    inc_result = state.get("incremental_execution_result", {})
    assert inc_result.get("ok") is True

    # Simulation plan should be assembled.
    assert state.get("simulation_plan") is not None

    # Plan build state should have valid patches.
    pbs = state.get("plan_build_state", {})
    patches = pbs.get("patches", {})
    valid_types = [
        v["patch_type"] for v in patches.values()
        if v.get("status") == "valid"
    ]
    assert "facts" in valid_types
    assert "materials" in valid_types
    assert "universes" in valid_types
    assert "pin_map" in valid_types
    assert "axial_layers" in valid_types
    assert "axial_overlays" in valid_types


# ---------------------------------------------------------------------------
# 3. Incremental success continues to existing validation
# ---------------------------------------------------------------------------


def test_incremental_success_validates_assembled_plan(tmp_path: Path) -> None:
    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake_llm = FakePatchLLM(llm_responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    # Assembled plan should pass validation (structure is valid).
    report = state.get("validation_report")
    assert report is not None

    # The assembled plan should have the expected structure.
    plan = state.get("simulation_plan")
    if plan is not None:
        if isinstance(plan, dict):
            cm = plan.get("complex_model", {})
        else:
            cm = plan.complex_model.model_dump(mode="json") if hasattr(plan, "complex_model") else {}
        core = cm.get("core", {})
        assert len(core.get("axial_layers", [])) == 12
        assert len(core.get("axial_overlays", [])) == 8


# ---------------------------------------------------------------------------
# 4. Incremental failure does NOT fallback to monolithic by default
# ---------------------------------------------------------------------------


def test_incremental_failure_no_fallback(tmp_path: Path) -> None:
    """Pin_map always fails → graph should NOT call monolithic planner.

    Uses a non-VERA3 requirement so reference patches are not available.
    """
    _NON_BENCHMARK_REQ = (
        "3D assembly with axial layers, spacer grids, "
        "三维, 定位格架, special pin map with guide tubes, "
        "17x17 lattice"
    )
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    # Need responses for facts, materials, universes first.
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
        bad_pin, bad_pin,
    ]
    fake_llm = FakePatchLLM(responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
        reference_patch_policy="off",
    )
    state = graph.invoke({
        "requirement": _NON_BENCHMARK_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    inc_result = state.get("incremental_execution_result", {})
    assert inc_result.get("ok") is False
    # Should have an error.
    assert state.get("error") or not state.get("simulation_plan")


# ---------------------------------------------------------------------------
# 5. Optional monolithic fallback flag
# ---------------------------------------------------------------------------


def test_monolithic_fallback_when_enabled(tmp_path: Path) -> None:
    """When allow_monolithic_fallback=True, failed incremental falls back.

    Uses a non-VERA3 requirement so reference patches are not available.
    """
    _NON_BENCHMARK_REQ = (
        "3D assembly with axial layers, spacer grids, "
        "三维, 定位格架, special pin map with guide tubes, "
        "17x17 lattice"
    )
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
        bad_pin, bad_pin,
    ]
    fake_llm = FakePatchLLM(responses)

    monolithic_called = {"count": 0}

    def fake_monolithic(*, requirement, schema, model):
        monolithic_called["count"] += 1
        from tests.test_graph import make_simulation_plan
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_monolithic,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
        allow_monolithic_fallback_for_incremental_failure=True,
    )
    state = graph.invoke({
        "requirement": _NON_BENCHMARK_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })
    assert monolithic_called["count"] >= 1


# ---------------------------------------------------------------------------
# 6. Full-plan repair NOT called for patch JSON failure
# ---------------------------------------------------------------------------


def test_patch_json_failure_handled_locally(tmp_path: Path) -> None:
    """Bad JSON for axial_layers first attempt, then valid — no global repair."""
    base = [
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
    fake_llm = FakePatchLLM(base + [bad_json, good_json] + tail)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
        reference_patch_policy="off",
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    inc_result = state.get("incremental_execution_result", {})
    assert inc_result.get("ok") is True


# ---------------------------------------------------------------------------
# 7. Valid patches preserved after later failure
# ---------------------------------------------------------------------------


def test_valid_patches_preserved_after_failure(tmp_path: Path) -> None:
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]},
        ]}),
        bad_pin, bad_pin,
    ]
    fake_llm = FakePatchLLM(responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    pbs = state.get("plan_build_state", {})
    patches = pbs.get("patches", {})
    # Facts/materials/universes should still be valid.
    valid_types = [
        v["patch_type"] for v in patches.values()
        if v.get("status") == "valid"
    ]
    assert "facts" in valid_types
    assert "materials" in valid_types
    assert "universes" in valid_types


# ---------------------------------------------------------------------------
# 9. VERA3 3B graph integration end-to-end
# ---------------------------------------------------------------------------


def test_vera3_3b_graph_end_to_end(tmp_path: Path) -> None:
    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake_llm = FakePatchLLM(llm_responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    # Planning mode.
    pmd = state.get("planning_mode_decision", {})
    assert pmd.get("mode") == "incremental"

    # Incremental execution succeeded.
    inc_result = state.get("incremental_execution_result", {})
    assert inc_result.get("ok") is True

    # Check assembled plan structure.
    pbs = state.get("plan_build_state", {})
    assembled = pbs.get("assembled_plan")
    if assembled is not None:
        cm = assembled.get("complex_model", {})
        lattices = cm.get("lattices", [])
        if lattices:
            pattern = lattices[0].get("universe_pattern", [])
            assert len(pattern) == 17
            flat = [uid for row in pattern for uid in row]
            assert sum(1 for u in flat if u == "pyrex_rod") == 16
            assert sum(1 for u in flat if u == "thimble_plug") == 8
        core = cm.get("core", {})
        assert len(core.get("axial_layers", [])) == 12
        assert len(core.get("axial_overlays", [])) == 8


# ---------------------------------------------------------------------------
# 10. Transcript includes plan_build_state summary
# ---------------------------------------------------------------------------


def test_transcript_has_incremental_summary(tmp_path: Path) -> None:
    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake_llm = FakePatchLLM(llm_responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    trace = state.get("trace", {})
    events = trace.get("events", [])
    plan_generated_events = [
        e for e in events if e.get("event_type") == "plan_generated"
    ]
    assert len(plan_generated_events) >= 1
    metadata = plan_generated_events[-1].get("metadata", {})
    assert metadata.get("planning_mode") == "incremental"
    assert metadata.get("success") is True


def test_vera3_3b_default_no_monolithic_reflect_artifacts(tmp_path: Path) -> None:
    stale_valid_dir = tmp_path / "incremental" / "valid_patches"
    stale_valid_dir.mkdir(parents=True)
    stale_patch = stale_valid_dir / "stale_pin_map.json"
    stale_patch.write_text('{"stale": true}', encoding="utf-8")

    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake_llm = FakePatchLLM(llm_responses)

    graph = build_plan_graph(
        generate_plan=_fake_generate_plan_should_not_be_called,
        export_xml_tool=_fake_export_xml,
        plot_tool=_fake_plot,
        smoke_test_tool=_fake_smoke,
        patch_llm_client=fake_llm,
    )
    state = graph.invoke({
        "requirement": _VERA3_3B_REQ,
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    inc_result = state.get("incremental_execution_result", {})
    assert inc_result.get("ok") is True
    assert inc_result.get("planning_mode") == "incremental"
    assert inc_result.get("reference_patch_policy") == "off"
    assert inc_result.get("monolithic_reflect_plan_allowed") is False
    summary = inc_result.get("summary", {})
    assert summary.get("reference_patches_used") == []
    assert summary.get("reference_match_status") == "off"
    assert "actual_pin_counts" in summary
    assert "material_aliases_applied" in summary

    artifact_names = [Path(p).name for p in state.get("plan_artifacts", [])]
    assert not any("reflect_plan" in name for name in artifact_names)
    assert not any("repair_plan_format" in name for name in artifact_names)
    assert not stale_patch.exists()
