"""Tests for the dependency-aware local retry router (Phase 5)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.executor import route_retry, RetryDecision
from openmc_agent.plan_builder.state import PlanBuildState


def _state_with_valid_patch(ptype: str = "materials") -> PlanBuildState:
    state = PlanBuildState(state_id="test", requirement_text="test")
    from openmc_agent.plan_builder.state import PlanPatchEnvelope
    state.add_patch(PlanPatchEnvelope(
        patch_id=f"valid_{ptype}",
        patch_type=ptype,
        content={},
        status="valid",
    ))
    return state


# ---------------------------------------------------------------------------
# 10. retry router parse error → retry_same_patch
# ---------------------------------------------------------------------------


def test_route_parse_error_retry_same() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="materials",
        issues=[{"code": "patch_generation.json_parse_error", "severity": "error"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"
    assert decision.patch_type == "materials"


# ---------------------------------------------------------------------------
# 11. retry router unresolved material → retry_dependency or retry_same
# ---------------------------------------------------------------------------


def test_route_unresolved_material_missing_dependency() -> None:
    """When material reference is missing AND materials patch doesn't exist."""
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="axial_overlays",
        issues=[{"code": "patch.axial_overlays.material_missing", "severity": "error"}],
        state=state,
    )
    # materials patch is not valid → retry_dependency_patch
    assert decision.action == "retry_dependency_patch"
    assert decision.dependency_patch_type == "materials"


def test_route_unresolved_material_dependency_valid() -> None:
    """When material reference is missing BUT materials patch is already valid."""
    state = _state_with_valid_patch("materials")
    decision = route_retry(
        failed_patch_type="axial_overlays",
        issues=[{"code": "patch.axial_overlays.material_missing", "severity": "error"}],
        state=state,
    )
    # materials patch IS valid → retry_same_patch (current patch has the issue)
    assert decision.action == "retry_same_patch"
    assert decision.patch_type == "axial_overlays"


# ---------------------------------------------------------------------------
# 12. retry router pin map count mismatch → retry_same_patch
# ---------------------------------------------------------------------------


def test_route_pin_map_count_mismatch() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="pin_map",
        issues=[{"code": "patch.pin_map.count_mismatch", "severity": "error"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"
    assert decision.patch_type == "pin_map"


def test_route_coord_overlap() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="pin_map",
        issues=[{"code": "patch.pin_map.coord_overlap", "severity": "error"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"


def test_route_schema_invalid() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="facts",
        issues=[{"code": "patch.schema_invalid", "severity": "error"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"


def test_route_axial_layers_error() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="axial_layers",
        issues=[{"code": "patch.axial_layers.invalid_range", "severity": "error"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"


def test_route_unroutable_fails() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="facts",
        issues=[{"code": "some_unknown_error_code", "severity": "error"}],
        state=state,
    )
    assert decision.action == "fail"


def test_route_only_warnings_retries() -> None:
    state = PlanBuildState(state_id="s", requirement_text="test")
    decision = route_retry(
        failed_patch_type="universes",
        issues=[{"code": "patch.universes.guide_tube_wall_missing", "severity": "warning"}],
        state=state,
    )
    assert decision.action == "retry_same_patch"
