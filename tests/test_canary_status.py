"""Tests for canary status gating logic."""

from __future__ import annotations

import pytest

from openmc_agent.campaign_eval.canary_status import (
    CanaryReport,
    PLANNING_CANARY_PASSED,
    FUEL_VARIANT_SUBCANARY_PASSED,
    FORBIDDEN_STATUSES,
    evaluate_planning_canary,
    evaluate_fuel_variant_subcanary,
)


# ---------------------------------------------------------------------------
# Planning canary
# ---------------------------------------------------------------------------

def test_planning_canary_all_conditions_met() -> None:
    report = evaluate_planning_canary(
        execution_result={"ok": True, "summary": {"valid_patch_types": ["facts", "materials"]}},
        assembly_result={"ok": True},
        simulation_plan={"complex_model": {}},
        valid_patch_types=["facts", "materials"],
        required_patch_types=["facts", "materials"],
    )
    assert report.planning_canary is True
    assert PLANNING_CANARY_PASSED in report.declared_statuses()


def test_planning_canary_blocked_by_missing_patch() -> None:
    report = evaluate_planning_canary(
        execution_result={"ok": False, "summary": {"valid_patch_types": ["facts"]}},
        assembly_result={"ok": False},
        simulation_plan=None,
        valid_patch_types=["facts"],
        required_patch_types=["facts", "materials", "axial_layers"],
        invalid_patch_types=["axial_layers"],
    )
    assert report.planning_canary is False
    assert PLANNING_CANARY_PASSED not in report.declared_statuses()
    assert "axial_layers" in report.missing_patch_types


def test_planning_canary_blocked_by_reference_patches() -> None:
    report = evaluate_planning_canary(
        execution_result={"ok": True, "summary": {}},
        assembly_result={"ok": True},
        simulation_plan={"x": 1},
        valid_patch_types=["facts"],
        required_patch_types=["facts"],
        reference_patches_used=["facts"],
    )
    assert report.planning_canary is False
    assert "reference" in report.detail.lower()


def test_planning_canary_blocked_by_monolithic_fallback() -> None:
    report = evaluate_planning_canary(
        execution_result={"ok": True, "summary": {}},
        assembly_result={"ok": True},
        simulation_plan={"x": 1},
        valid_patch_types=["facts"],
        required_patch_types=["facts"],
        monolithic_fallback_used=True,
    )
    assert report.planning_canary is False


def test_planning_canary_partial_pass_not_full() -> None:
    """5/8 patches valid does NOT declare full canary."""
    report = evaluate_planning_canary(
        execution_result={"ok": False, "summary": {}},
        assembly_result={"ok": False},
        simulation_plan=None,
        valid_patch_types=["facts", "materials", "universes", "assembly_catalog", "axial_layers"],
        required_patch_types=["facts", "materials", "universes", "assembly_catalog",
                              "axial_layers", "axial_overlays", "core_layout", "settings"],
    )
    assert report.planning_canary is False
    assert len(report.missing_patch_types) == 3


# ---------------------------------------------------------------------------
# Fuel variant subcanary
# ---------------------------------------------------------------------------

def test_fuel_variant_subcanary_passes() -> None:
    report = evaluate_fuel_variant_subcanary(
        valid_patch_types=["facts", "materials", "assembly_catalog"],
        fuel_variant_requirements=[{"variant_id": "v1"}, {"variant_id": "v2"}],
        assembly_fuel_binding_summaries=[{"assembly_type_id": "a", "fuel_variant_id": "v1"}],
    )
    assert report.fuel_variant_subcanary is True
    assert FUEL_VARIANT_SUBCANARY_PASSED in report.declared_statuses()


def test_fuel_variant_subcanary_blocked_without_requirements() -> None:
    report = evaluate_fuel_variant_subcanary(
        valid_patch_types=["facts", "materials"],
        fuel_variant_requirements=[],
        assembly_fuel_binding_summaries=[],
    )
    assert report.fuel_variant_subcanary is False


def test_fuel_variant_subcanary_distinct_from_full_canary() -> None:
    """Fuel variant subcanary can pass even when full canary is blocked."""
    sub = evaluate_fuel_variant_subcanary(
        valid_patch_types=["facts", "materials", "universes", "assembly_catalog"],
        fuel_variant_requirements=[{"variant_id": "v1"}],
        assembly_fuel_binding_summaries=[{"assembly_type_id": "a", "fuel_variant_id": "v1"}],
    )
    assert sub.fuel_variant_subcanary is True

    full = evaluate_planning_canary(
        execution_result={"ok": False, "summary": {}},
        assembly_result={"ok": False},
        simulation_plan=None,
        valid_patch_types=["facts", "materials", "universes", "assembly_catalog"],
        required_patch_types=["facts", "materials", "universes", "assembly_catalog",
                              "axial_layers", "axial_overlays", "core_layout", "settings"],
    )
    assert full.planning_canary is False
    assert sub.fuel_variant_subcanary != full.planning_canary


# ---------------------------------------------------------------------------
# Forbidden statuses
# ---------------------------------------------------------------------------

def test_no_forbidden_statuses_declared() -> None:
    """None of the forbidden statuses should ever be declared."""
    report = CanaryReport(
        fuel_variant_subcanary=True,
        planning_canary=True,
    )
    assert report.forbidden_present() == []


def test_forbidden_set_does_not_overlap() -> None:
    """Ensure forbidden statuses are not accidentally in the declared set."""
    from openmc_agent.campaign_eval.canary_status import (
        AXIAL_OVERLAY_SEMANTIC_CONTRACT_READY,
        ISSUE_SCOPED_PATCH_RETRY_READY,
        RETRY_DRIFT_GATE_READY,
    )
    all_declared = {
        PLANNING_CANARY_PASSED,
        FUEL_VARIANT_SUBCANARY_PASSED,
        AXIAL_OVERLAY_SEMANTIC_CONTRACT_READY,
        ISSUE_SCOPED_PATCH_RETRY_READY,
        RETRY_DRIFT_GATE_READY,
    }
    assert all_declared.isdisjoint(FORBIDDEN_STATUSES)
