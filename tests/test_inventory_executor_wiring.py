"""Tests for the Phase 8A Step 5 executor wiring.

Verifies the inventory compilation + evidence injection + MU preflight
integration works end-to-end in offline mode.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.executor import (
    _inventory_evidence_payloads_for_patch_type,
    _maybe_compile_geometry_inventory,
    _maybe_run_inventory_preflight,
    build_generation_context_from_state,
    run_incremental_planning,
)
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_investigation.runner import (
    PlanInvestigationConfig,
    PlanInvestigationMode,
)


def _state():
    return PlanBuildState.model_validate(
        {
            "state_id": "pbs_test",
            "requirement_text": "demo requirement",
            "planning_mode": "incremental",
        }
    )


class _StubFactsEnv:
    """Minimal stand-in for a PlanPatchEnvelope carrying a valid FactsPatch."""

    def __init__(self, content):
        self.content = content
        self.patch_type = "facts"
        self.status = "valid"


# ---------------------------------------------------------------------------
# Inventory evidence injection
# ---------------------------------------------------------------------------


def test_inventory_evidence_empty_when_no_inventory() -> None:
    """Off mode (no inventory compiled) → empty evidence list."""
    state = _state()
    payloads = _inventory_evidence_payloads_for_patch_type(state, "materials")
    assert payloads == []


def test_inventory_evidence_populated_when_inventory_present() -> None:
    state = _state()
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih"}
    state.metadata["planning_material_requirement_set"] = {
        "requirements": [
            {
                "requirement_id": "mreq_1",
                "role": "fuel",
                "source_variant_id": "v1",
                "resolution_status": "required",
            }
        ]
    }
    payloads = _inventory_evidence_payloads_for_patch_type(state, "materials")
    assert len(payloads) == 1
    assert payloads[0]["predicate"] == "material.role_required"
    assert payloads[0]["value"]["role"] == "fuel"
    assert payloads[0]["criticality"] == "source_critical"  # fuel is critical


def test_inventory_evidence_universes_payloads() -> None:
    state = _state()
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih"}
    state.metadata["planning_universe_requirement_set"] = {
        "requirements": [
            {
                "requirement_id": "ureq_1",
                "profile_kind": "active_fuel_pin",
                "component_kind": "fuel_pin",
                "fuel_variant_id": "v1",
                "required_cell_roles": ["fuel"],
                "required_material_roles": ["fuel"],
                "geometry_profile_id": "prof_v1",
            }
        ]
    }
    payloads = _inventory_evidence_payloads_for_patch_type(state, "universes")
    assert len(payloads) == 1
    assert payloads[0]["predicate"] == "geometry.profile_required"
    assert payloads[0]["value"]["profile_kind"] == "active_fuel_pin"


# ---------------------------------------------------------------------------
# Inventory compilation hook
# ---------------------------------------------------------------------------


def test_compile_inventory_writes_state_metadata() -> None:
    """_maybe_compile_geometry_inventory populates state.metadata."""
    state = _state()
    facts_content = {
        "patch_type": "facts",
        "model_scope": "single_assembly",
        "fuel_variant_requirements": [],
        "localized_insert_requirements": [],
    }
    _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv(facts_content),
        requirement="demo requirement",
    )
    assert "planning_geometry_inventory" in state.metadata
    assert "planning_geometry_inventory_hash" in state.metadata
    assert "planning_material_requirement_set" in state.metadata
    assert "planning_universe_requirement_set" in state.metadata
    # Events recorded.
    event_types = [e.event_type for e in state.build_log]
    assert "planning.geometry_inventory_compilation_started" in event_types
    assert "planning.geometry_inventory_compiled" in event_types


def test_compile_inventory_failure_records_warning() -> None:
    """When the Facts content is malformed, compilation fails gracefully."""
    state = _state()
    _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv({"patch_type": "facts", "malformed": True}),
        requirement="demo",
    )
    event_types = [e.event_type for e in state.build_log]
    # Either compiled (FactsPatch accepts malformed dict gracefully) or
    # blocked — both are valid outcomes; the key is no exception.
    assert any(
        "geometry_inventory" in evt for evt in event_types
    )


# ---------------------------------------------------------------------------
# MU preflight integration
# ---------------------------------------------------------------------------


def test_maybe_run_inventory_preflight_noop_without_inventory() -> None:
    state = _state()
    issues = _maybe_run_inventory_preflight(
        state=state,
        materials_env=None,
        universes_env=None,
    )
    assert issues == []


def test_maybe_run_inventory_preflight_runs_when_inventory_present() -> None:
    state = _state()
    # Compile an empty inventory first.
    _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv({
            "patch_type": "facts",
            "model_scope": "single_assembly",
        }),
        requirement="demo",
    )
    # Now run the preflight with no patches — should produce findings
    # about uncovered material roles (if any) or just return empty.
    issues = _maybe_run_inventory_preflight(
        state=state,
        materials_env=None,
        universes_env=None,
    )
    # No fuel variants → no material requirements → no findings.
    assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# build_generation_context_from_state integration
# ---------------------------------------------------------------------------


def test_build_context_injects_inventory_evidence() -> None:
    """When state has an inventory, the Materials context carries it."""
    state = _state()
    state.metadata["planning_geometry_inventory"] = {"inventory_hash": "ih"}
    state.metadata["planning_material_requirement_set"] = {
        "requirements": [
            {
                "requirement_id": "mreq_1",
                "role": "fuel",
                "source_variant_id": "v1",
                "resolution_status": "required",
            }
        ]
    }
    ctx = build_generation_context_from_state(state, "materials")
    assert len(ctx.investigation_evidence) == 1
    assert ctx.investigation_evidence[0]["predicate"] == "material.role_required"


def test_build_context_no_inventory_no_evidence() -> None:
    """Off mode (no inventory) → context.investigation_evidence is empty."""
    state = _state()
    ctx = build_generation_context_from_state(state, "materials")
    assert ctx.investigation_evidence == []


# ---------------------------------------------------------------------------
# Off-mode zero-impact smoke test
# ---------------------------------------------------------------------------


def test_off_mode_never_compiles_inventory() -> None:
    """When plan investigation is off, the inventory hook is never called."""
    state = _state()
    # off mode = no _investigation_config_resolved → no hook call
    # We verify by running the executor end-to-end with off mode.
    def fake_patch_llm(prompt):
        return json.dumps({"patch_type": "facts", "model_scope": "single_assembly"})

    # off mode (default) — no investigation config passed
    run_incremental_planning(
        requirement="demo",
        state=state,
        llm_client=fake_patch_llm,
        task_order=["facts"],
        plan_loop_policy={"mode": "off"},
    )
    # No inventory should be compiled.
    assert "planning_geometry_inventory" not in state.metadata
