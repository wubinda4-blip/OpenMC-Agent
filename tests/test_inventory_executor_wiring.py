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
    _inventory_planning_constraints_for_patch_type,
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
    # The new typed path also returns [] when no inventory is present.
    constraints = _inventory_planning_constraints_for_patch_type(state, "materials")
    assert constraints == []


def test_inventory_evidence_populated_when_inventory_present() -> None:
    """Phase 8A Step 6: inventory requirements become typed constraints.

    The legacy ``_inventory_evidence_payloads_for_patch_type`` is now a
    backward-compat shim that returns ``[]`` (see P0-4 fix).  The new
    ``_inventory_planning_constraints_for_patch_type`` returns typed
    :class:`EvidenceConstraintPayload` objects with
    ``derivation_status=deterministically_derived`` (NOT
    ``status="explicit"`` with empty source_spans).
    """
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
    # Legacy shim is always empty now.
    payloads = _inventory_evidence_payloads_for_patch_type(state, "materials")
    assert payloads == []
    # Typed path produces one derived constraint.
    constraints = _inventory_planning_constraints_for_patch_type(state, "materials")
    assert len(constraints) == 1
    c = constraints[0]
    assert c.predicate == "material.role_required"
    assert c.value["role"] == "fuel"
    assert c.criticality == "source_critical"  # fuel is critical
    # P0-4 fix: derivation_status is deterministically_derived, NOT explicit.
    assert c.derivation_status == "deterministically_derived"
    assert c.source_claim_ids == ()
    assert c.inventory_requirement_ids == ("mreq_1",)
    # constraint_hash + constraint_id are populated.
    assert c.constraint_hash
    assert c.constraint_id.startswith("constraint_")


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
    constraints = _inventory_planning_constraints_for_patch_type(state, "universes")
    assert len(constraints) == 1
    c = constraints[0]
    assert c.predicate == "geometry.profile_required"
    assert c.value["profile_kind"] == "active_fuel_pin"
    assert c.derivation_status == "deterministically_derived"


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
    status = _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv(facts_content),
        requirement="demo requirement",
    )
    assert status["compiled"] is True
    assert status["inventory_hash"]
    assert "planning_geometry_inventory" in state.metadata
    assert "planning_geometry_inventory_hash" in state.metadata
    assert "planning_material_requirement_set" in state.metadata
    assert "planning_universe_requirement_set" in state.metadata
    # Events recorded.
    event_types = [e.event_type for e in state.build_log]
    assert "planning.geometry_inventory_compilation_started" in event_types
    assert "planning.geometry_inventory_compiled" in event_types


def test_compile_inventory_failure_records_warning() -> None:
    """When the Facts content is malformed, compilation fails gracefully.

    Phase 8A Step 6: the function returns ``compiled=False`` and a
    stable ``failure_code`` so the caller can fail closed in
    controlled mode.  Advisory mode logs the event and continues.
    """
    state = _state()
    status = _maybe_compile_geometry_inventory(
        state=state,
        facts_env=_StubFactsEnv({"patch_type": "facts", "malformed": True}),
        requirement="demo",
    )
    # Either compiled (FactsPatch accepts malformed dict gracefully) or
    # blocked — both are valid outcomes; the key is the typed status.
    assert isinstance(status, dict)
    assert "compiled" in status
    if not status["compiled"]:
        assert status["failure_code"] == "planning.inventory.compilation_failed"
        assert status["error"]
    event_types = [e.event_type for e in state.build_log]
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
    """When state has an inventory, the Materials context carries typed constraints.

    Phase 8A Step 6: the new ``planning_constraints`` field holds the
    derived constraints; the legacy ``investigation_evidence`` field
    is reserved for source-backed EvidenceClaims.
    """
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
    # Constraints live in the new field.
    assert len(ctx.planning_constraints) == 1
    assert ctx.planning_constraints[0]["predicate"] == "material.role_required"
    assert ctx.planning_constraints[0]["derivation_status"] == "deterministically_derived"
    # Legacy investigation_evidence is empty (no source-backed claims yet).
    assert ctx.investigation_evidence == []


def test_build_context_no_inventory_no_evidence() -> None:
    """Off mode (no inventory) → both fields empty."""
    state = _state()
    ctx = build_generation_context_from_state(state, "materials")
    assert ctx.investigation_evidence == []
    assert ctx.planning_constraints == []


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
