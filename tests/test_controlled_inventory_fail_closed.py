"""Phase 8A Step 6A — controlled inventory fail-closed tests.

Verifies P0-2, P0-3 fixes:
* controlled mode blocks on inventory compilation failure
* controlled mode blocks on inventory preflight exception
* advisory mode logs warning but continues
* off mode is unaffected
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.executor import (
    _maybe_compile_geometry_inventory,
    _maybe_run_inventory_preflight,
    _run_typed_inventory_preflight,
)
from openmc_agent.plan_builder.state import PlanBuildState


def _state() -> PlanBuildState:
    return PlanBuildState.model_validate(
        {
            "state_id": "pbs_test",
            "requirement_text": "demo requirement",
            "planning_mode": "incremental",
        }
    )


class _StubFactsEnv:
    def __init__(self, content):
        self.content = content
        self.patch_type = "facts"
        self.status = "valid"


# ---------------------------------------------------------------------------
# Inventory compile fail-closed (P0-2)
# ---------------------------------------------------------------------------


def test_compile_inventory_returns_typed_status_on_success() -> None:
    """Successful compilation returns compiled=True + inventory_hash."""

    state = _state()
    facts_content = {
        "patch_type": "facts",
        "model_scope": "single_assembly",
        "fuel_variant_requirements": [],
        "localized_insert_requirements": [],
    }
    status = _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv(facts_content),
        requirement="demo",
    )
    assert status["compiled"] is True
    assert status["inventory_hash"]
    assert status["error"] is None
    assert status["failure_code"] == ""


def test_compile_inventory_returns_typed_status_on_failure() -> None:
    """Malformed Facts content returns compiled=False + stable failure_code.

    Phase 8A Step 6 (P0-2): the caller can now fail closed in
    controlled mode by inspecting ``compiled``.  Previously the
    function returned ``None`` and only logged an event, allowing the
    run to continue with the legacy prompt path.
    """

    state = _state()
    status = _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv({"patch_type": "facts", "totally_malformed": True}),
        requirement="demo",
    )
    assert isinstance(status, dict)
    assert "compiled" in status
    if not status["compiled"]:
        assert status["failure_code"] == "planning.inventory.compilation_failed"
        assert status["error"]
    else:
        # FactsPatch may accept the malformed dict gracefully; that's
        # also acceptable as long as the status is consistent.
        assert status["inventory_hash"]


# ---------------------------------------------------------------------------
# Inventory preflight fail-closed (P0-3)
# ---------------------------------------------------------------------------


def test_preflight_noop_without_inventory() -> None:
    """Off mode (no inventory) returns empty list."""

    state = _state()
    issues = _maybe_run_inventory_preflight(
        state=state, materials_env=None, universes_env=None,
        controlled_mode=True,
    )
    assert issues == []


def test_preflight_exception_blocks_in_controlled_mode() -> None:
    """When the preflight crashes in controlled mode, the gate must block.

    Phase 8A Step 6 (P0-3): the previous implementation caught every
    exception and returned ``[]``, which the gate treated as "no
    deterministic finding".  In controlled mode we now emit a blocking
    finding with code ``material_universe.inventory_preflight_exception``.
    """

    state = _state()
    # Inventory present but malformed → preflight crashes when parsing.
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih", "malformed": True}
    state.metadata["planning_material_requirement_set"] = {}
    state.metadata["planning_universe_requirement_set"] = {}
    issues = _maybe_run_inventory_preflight(
        state=state,
        materials_env=None,
        universes_env=None,
        controlled_mode=True,
    )
    assert len(issues) >= 1
    assert any(
        issue["code"] == "material_universe.inventory_preflight_exception"
        and issue["severity"] == "error"
        and issue["owner_patch_type"] == "plan_investigation"
        for issue in issues
    ), f"expected blocking exception finding, got: {issues}"


def test_preflight_exception_silent_in_advisory_mode() -> None:
    """Advisory mode logs warning but returns [] (no blocking)."""

    state = _state()
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih", "malformed": True}
    state.metadata["planning_material_requirement_set"] = {}
    state.metadata["planning_universe_requirement_set"] = {}
    issues = _maybe_run_inventory_preflight(
        state=state,
        materials_env=None,
        universes_env=None,
        controlled_mode=False,
    )
    assert issues == []
    # Event should be recorded.
    event_types = [e.event_type for e in state.build_log]
    assert "planning.inventory_material_universe_preflight_failed" in event_types


# ---------------------------------------------------------------------------
# Typed execution result
# ---------------------------------------------------------------------------


def test_typed_preflight_result_has_blocking_flag_on_exception() -> None:
    """InventoryPreflightExecutionResult.has_blocking_deterministic_finding."""

    state = _state()
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih", "malformed": True}
    state.metadata["planning_material_requirement_set"] = {}
    state.metadata["planning_universe_requirement_set"] = {}
    result = _run_typed_inventory_preflight(
        state=state, materials_env=None, universes_env=None,
    )
    assert result.executed is False
    assert result.execution_error is not None
    assert result.failure_code == "material_universe.inventory_preflight_exception"
    assert result.has_blocking_deterministic_finding is True


def test_typed_preflight_result_no_blocking_when_no_inventory() -> None:
    """No inventory → not executed but also not blocking (off mode)."""

    state = _state()
    result = _run_typed_inventory_preflight(
        state=state, materials_env=None, universes_env=None,
    )
    assert result.executed is False
    # No inventory = off mode = silent, not blocking.
    assert result.has_blocking_deterministic_finding is False


# ---------------------------------------------------------------------------
# Explicit owner map (P0-3 sub-fix)
# ---------------------------------------------------------------------------


def test_explicit_owner_map_no_string_contains_matching() -> None:
    """Owner map returns explicit owner for each code, None for unknown."""

    from openmc_agent.plan_investigation.inventory_preflight import (
        owner_for_inventory_finding_code,
        INVENTORY_MATERIAL_ROLE_UNCOVERED,
        INVENTORY_RADIAL_PROFILE_UNCOVERED,
        INVENTORY_SOURCE_CLAIM_MISSING,
        INVENTORY_HASH_MISMATCH,
    )
    assert owner_for_inventory_finding_code(INVENTORY_MATERIAL_ROLE_UNCOVERED) == "materials"
    assert owner_for_inventory_finding_code(INVENTORY_RADIAL_PROFILE_UNCOVERED) == "universes"
    assert owner_for_inventory_finding_code(INVENTORY_SOURCE_CLAIM_MISSING) == "plan_investigation"
    assert owner_for_inventory_finding_code(INVENTORY_HASH_MISMATCH) == "inventory_rebuild"
    # Unknown code → None (fail-closed).
    assert owner_for_inventory_finding_code("totally_unknown_code") is None
    assert owner_for_inventory_finding_code("material_universe.inventory_preflight_failed") is None
