"""Tests for PlanBuildState lifecycle (Phase 1)."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.mode import should_use_incremental_planning
from openmc_agent.plan_builder.state import (
    BuildEvent,
    PlanBuildState,
    PlanComponentTask,
    PlanPatchEnvelope,
    create_initial_component_tasks,
    initialize_plan_build_state,
    EVENT_BUILD_STATE_INITIALIZED,
    EVENT_COMPONENT_TASKS_INITIALIZED,
    EVENT_PLANNING_MODE_SELECTED,
)


# ---------------------------------------------------------------------------
# 8. PlanBuildState serializes to JSON
# ---------------------------------------------------------------------------


def test_plan_build_state_serializes_to_json() -> None:
    decision = should_use_incremental_planning("3D axial assembly with spacer grids")
    state = initialize_plan_build_state(
        requirement="3D axial assembly with spacer grids",
        decision=decision,
    )
    # model_dump works
    payload = state.model_dump(mode="json")
    assert payload["state_id"] == state.state_id
    assert payload["planning_mode"] == "incremental"
    assert "requirement_text" in payload
    # JSON serialization works
    json_str = json.dumps(payload, ensure_ascii=False)
    restored = json.loads(json_str)
    assert restored["state_id"] == state.state_id
    # Required fields present
    assert "component_tasks" in restored
    assert "patches" in restored
    assert "build_log" in restored
    assert "metadata" in restored


# ---------------------------------------------------------------------------
# 9. PlanBuildState patch lifecycle
# ---------------------------------------------------------------------------


def test_plan_build_state_patch_lifecycle() -> None:
    state = PlanBuildState(
        state_id="test_state_001",
        requirement_text="test requirement",
    )
    # Add a pending patch
    patch = PlanPatchEnvelope(
        patch_id="patch_materials_01",
        patch_type="materials",
        content={"materials": [{"id": "fuel", "name": "UO2"}]},
        source="llm",
        status="pending",
    )
    state.add_patch(patch)
    assert state.patches["patch_materials_01"].status == "pending"
    assert state.patch_status["patch_materials_01"] == "pending"

    # Mark valid
    state.mark_patch_status("patch_materials_01", "valid")
    assert state.patches["patch_materials_01"].status == "valid"
    assert state.patch_status["patch_materials_01"] == "valid"

    # Get valid patches
    valid = state.get_valid_patches()
    assert len(valid) == 1
    assert valid[0].patch_id == "patch_materials_01"

    # Filter by type
    valid_materials = state.get_valid_patches(patch_type="materials")
    assert len(valid_materials) == 1
    valid_settings = state.get_valid_patches(patch_type="settings")
    assert len(valid_settings) == 0

    # Mark invalid with issues
    state.mark_patch_status(
        "patch_materials_01",
        "invalid",
        issues=[{"code": "material.density_missing"}],
    )
    assert state.patches["patch_materials_01"].status == "invalid"
    assert state.get_valid_patches() == []


# ---------------------------------------------------------------------------
# 10. Initial component tasks for 3D spacer grid case
# ---------------------------------------------------------------------------


def test_initial_component_tasks_for_3d_spacer_grid_case() -> None:
    feature_summary = {
        "has_axial_geometry": True,
        "has_spacer_grid": True,
        "has_special_pin_map": True,
        "has_explicit_z_ranges": True,
        "has_axial_components": True,
        "has_benchmark_variant": True,
        "large_lattice_dimension": 21,
    }
    tasks = create_initial_component_tasks(feature_summary)
    task_types = [t.patch_type for t in tasks]
    assert "facts" in task_types
    assert "materials" in task_types
    assert "universes" in task_types
    assert "pin_map" in task_types
    assert "axial_layers" in task_types
    assert "axial_overlays" in task_types
    assert "settings" in task_types

    # All tasks should be pending
    assert all(t.status == "pending" for t in tasks)

    # Dependencies should be set
    materials_task = next(t for t in tasks if t.patch_type == "materials")
    assert "task_facts" in materials_task.dependencies

    axial_overlays_task = next(t for t in tasks if t.patch_type == "axial_overlays")
    assert "task_axial_layers" in axial_overlays_task.dependencies


# ---------------------------------------------------------------------------
# 10b. Component tasks for simple case (no special features) should be empty
# ---------------------------------------------------------------------------


def test_component_tasks_empty_for_simple_case() -> None:
    feature_summary = {
        "has_axial_geometry": False,
        "has_spacer_grid": False,
        "has_special_pin_map": False,
        "has_benchmark_variant": False,
        "large_lattice_dimension": None,
    }
    tasks = create_initial_component_tasks(feature_summary)
    assert tasks == []


# ---------------------------------------------------------------------------
# 10c. Component tasks skip pin_map when no special pins
# ---------------------------------------------------------------------------


def test_component_tasks_skip_pin_map_without_special_pins() -> None:
    feature_summary = {
        "has_axial_geometry": True,
        "has_spacer_grid": True,
        "has_special_pin_map": False,
        "large_lattice_dimension": None,
    }
    tasks = create_initial_component_tasks(feature_summary)
    task_types = [t.patch_type for t in tasks]
    assert "pin_map" not in task_types
    assert "axial_layers" in task_types
    assert "axial_overlays" in task_types


# ---------------------------------------------------------------------------
# 11. Transcript summary includes planning mode decision
# ---------------------------------------------------------------------------


def test_transcript_summary_includes_planning_mode_decision() -> None:
    requirement = "3D axial assembly with spacer grids, Pyrex rods, 21x21 lattice"
    decision = should_use_incremental_planning(requirement)
    state = initialize_plan_build_state(
        requirement=requirement,
        decision=decision,
        benchmark_id="VERA3",
        selected_variant="3B",
    )

    summary = state.to_summary()
    assert summary["state_id"] == state.state_id
    assert summary["planning_mode"] == "incremental"
    assert summary["benchmark_id"] == "VERA3"
    assert summary["selected_variant"] == "3B"
    assert summary["task_count"] > 0
    assert summary["patch_count"] == 0
    assert summary["valid_patch_count"] == 0

    # Build log should have mode-selected and state-initialized events
    event_types = [e.event_type for e in state.build_log]
    assert EVENT_PLANNING_MODE_SELECTED in event_types
    assert EVENT_BUILD_STATE_INITIALIZED in event_types
    assert EVENT_COMPONENT_TASKS_INITIALIZED in event_types

    # Metadata should contain the planning mode decision
    metadata = state.metadata
    assert "planning_mode_decision" in metadata
    pmd = metadata["planning_mode_decision"]
    assert pmd["mode"] == "incremental"
    assert len(pmd["triggers"]) > 0
    assert len(pmd["reasons"]) > 0


# ---------------------------------------------------------------------------
# 12. add_event appends to build_log with timestamp
# ---------------------------------------------------------------------------


def test_add_event_appends_with_timestamp() -> None:
    state = PlanBuildState(
        state_id="test_state_002",
        requirement_text="test",
    )
    assert len(state.build_log) == 0
    state.add_event("test.event", "test message", {"key": "value"})
    assert len(state.build_log) == 1
    event = state.build_log[0]
    assert event.event_type == "test.event"
    assert event.message == "test message"
    assert event.data == {"key": "value"}
    assert event.timestamp is not None


# ---------------------------------------------------------------------------
# 13. add_task replaces existing task by task_id
# ---------------------------------------------------------------------------


def test_add_task_replaces_existing() -> None:
    state = PlanBuildState(
        state_id="test_state_003",
        requirement_text="test",
    )
    task1 = PlanComponentTask(
        task_id="task_materials",
        patch_type="materials",
        status="pending",
    )
    state.add_task(task1)
    assert len(state.component_tasks) == 1

    task2 = PlanComponentTask(
        task_id="task_materials",
        patch_type="materials",
        status="valid",
    )
    state.add_task(task2)
    assert len(state.component_tasks) == 1
    assert state.component_tasks[0].status == "valid"


# ---------------------------------------------------------------------------
# 14. PlanBuildState from incremental decision with fallback event
# ---------------------------------------------------------------------------


def test_incremental_fallback_event_recorded() -> None:
    """When Phase 0 falls back to monolithic, the build state should record it."""
    decision = should_use_incremental_planning(
        "3D assembly with axial layers and spacer grids"
    )
    state = initialize_plan_build_state(
        requirement="3D assembly with axial layers and spacer grids",
        decision=decision,
    )
    # Simulate the fallback event that graph.py adds
    state.add_event(
        event_type="planning.incremental_recommended_but_not_executed",
        message="incremental planning recommended but executor is not yet implemented",
        data={"fallback_reason": "incremental_executor_not_implemented"},
    )
    event_types = [e.event_type for e in state.build_log]
    assert "planning.incremental_recommended_but_not_executed" in event_types
    fallback_event = next(
        e for e in state.build_log
        if e.event_type == "planning.incremental_recommended_but_not_executed"
    )
    assert fallback_event.data["fallback_reason"] == "incremental_executor_not_implemented"


# ---------------------------------------------------------------------------
# 15. Multiple patches lifecycle
# ---------------------------------------------------------------------------


def test_multiple_patches_lifecycle() -> None:
    state = PlanBuildState(
        state_id="test_state_004",
        requirement_text="test",
    )
    for i in range(3):
        state.add_patch(
            PlanPatchEnvelope(
                patch_id=f"patch_{i}",
                patch_type=["materials", "universes", "pin_map"][i],
                status="pending",
            )
        )
    assert len(state.patches) == 3
    assert len(state.get_valid_patches()) == 0

    state.mark_patch_status("patch_0", "valid")
    state.mark_patch_status("patch_1", "valid")
    state.mark_patch_status("patch_2", "invalid")
    assert len(state.get_valid_patches()) == 2
    assert len(state.get_valid_patches(patch_type="materials")) == 1
    assert len(state.get_valid_patches(patch_type="universes")) == 1
