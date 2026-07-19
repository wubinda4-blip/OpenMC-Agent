"""Tests for the Phase 8A Step 5 inventory truthfulness auditor."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.inventory_truthfulness import (
    INVENTORY_TRUTH_VIOLATIONS,
    TV_FABRICATED_GEOMETRY_VALUE,
    TV_INVENTORY_ARTIFACT_MISSING,
    TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT,
    TV_INVENTORY_HASH_MISMATCH,
    TV_INVENTORY_REASONING_CONTENT_PERSISTED,
    TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE,
    TV_MATERIAL_REQUIREMENT_NOT_COVERED,
    TV_MATERIALS_GENERATED_WITHOUT_INVENTORY,
    TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE,
    TV_UNIVERSE_REQUIREMENT_NOT_COVERED,
    inventory_truth_violations_for_run,
)


def test_off_mode_no_violations() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "off"},
    )
    assert violations == []


def test_controlled_materials_without_inventory() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "materials_patch_generated": True,
        },
        inventory_summary={"inventory_compiled": False},
    )
    assert TV_MATERIALS_GENERATED_WITHOUT_INVENTORY in violations


def test_controlled_universes_without_inventory() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "universes_patch_generated": True,
        },
        inventory_summary={"inventory_compiled": False},
    )
    assert TV_UNIVERSE_REQUIREMENT_NOT_COVERED not in violations  # this is a different code
    # universes_generated_without_inventory should fire
    assert any("universes_generated_without_inventory" in v for v in violations)


def test_inventory_hash_mismatch_detected() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "inventory_hash_mismatch": True,
        },
    )
    assert TV_INVENTORY_HASH_MISMATCH in violations


def test_unsupported_implicit_component_in_controlled() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "unsupported_implicit_component_count": 2,
        },
    )
    assert TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT in violations


def test_material_requirement_not_covered() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "material_requirement_uncovered_count": 1,
        },
    )
    assert TV_MATERIAL_REQUIREMENT_NOT_COVERED in violations


def test_legacy_auxiliary_fallback_in_controlled() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "legacy_auxiliary_fallback_used": True,
        },
    )
    assert TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE in violations


def test_fabricated_geometry_value_detected() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "fabricated_geometry_value_count": 1,
        },
    )
    assert TV_FABRICATED_GEOMETRY_VALUE in violations


def test_artifact_missing_in_controlled() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={
            "inventory_compiled": True,
            "inventory_artifact_written": False,
        },
    )
    assert TV_INVENTORY_ARTIFACT_MISSING in violations


def test_reasoning_content_persisted_in_artifact() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "controlled"},
        inventory_summary={"inventory_compiled": True},
        artifact_text_snapshot="some text with reasoning_content leak",
    )
    assert TV_INVENTORY_REASONING_CONTENT_PERSISTED in violations


def test_mu_gate_accepted_with_preflight_failure() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "material_universe_gate_accepted": True,
        },
        inventory_summary={
            "inventory_compiled": True,
            "inventory_preflight_passed": False,
        },
    )
    assert TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE in violations


def test_no_violations_for_clean_controlled_run() -> None:
    violations = inventory_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "material_universe_gate_accepted": True,
        },
        inventory_summary={
            "inventory_compiled": True,
            "inventory_artifact_written": True,
            "inventory_preflight_passed": True,
        },
        artifact_text_snapshot="clean audit text only",
    )
    assert violations == []


def test_all_violation_codes_are_stable_strings() -> None:
    for code in INVENTORY_TRUTH_VIOLATIONS:
        assert isinstance(code, str)
        assert not code.startswith("plan_investigation.")  # namespace is bare
