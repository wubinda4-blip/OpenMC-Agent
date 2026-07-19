"""Phase 8A Step 6A — inventory constraint provenance tests (P0-4).

Verifies that:
* Inventory-derived constraints are NOT mislabeled as ``status="explicit"``
  EvidenceClaims with empty source_spans.
* ``derivation_status=deterministically_derived`` for inventory-derived
  requirements without source_claim_ids.
* ``derivation_status=explicit_source`` REQUIRES source_claim_ids
  (the misrepresentation guard).
* ``requirement_id`` is no longer smuggled as ``claim_id``.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.constraint_payloads import (
    ConstraintDerivationStatus,
    EvidenceConstraintPayload,
)
from openmc_agent.plan_investigation.inventory_constraints import (
    build_inventory_constraints_for_patch_type,
    build_material_constraints_from_requirement_set,
    build_universe_constraints_from_requirement_set,
)


# ---------------------------------------------------------------------------
# Constraint model invariants
# ---------------------------------------------------------------------------


def test_derived_constraint_has_deterministically_derived_status() -> None:
    """A constraint with no source_claim_ids must be deterministically_derived."""

    c = EvidenceConstraintPayload(
        constraint_kind="material_role",
        subject="material_role:fuel",
        predicate="material.role_required",
        value={"role": "fuel"},
        derivation_status=ConstraintDerivationStatus.DETERMINISTICALLY_DERIVED,
        inventory_requirement_ids=("mreq_1",),
        inventory_hash="abc",
    )
    assert c.derivation_status == "deterministically_derived"
    assert c.source_claim_ids == ()
    # Hash + id are populated.
    assert c.constraint_hash
    assert c.constraint_id.startswith("constraint_")


def test_explicit_source_requires_source_claim_ids() -> None:
    """explicit_source derivation MUST cite at least one real claim id.

    This is the P0-4 fix: previously inventory requirements were
    labelled ``status="explicit"`` with empty source_spans, which
    made them indistinguishable from source-backed EvidenceClaims.
    """

    with pytest.raises(ValueError, match="explicit_source requires"):
        EvidenceConstraintPayload(
            constraint_kind="material_role",
            subject="material_role:fuel",
            predicate="material.role_required",
            value={"role": "fuel"},
            derivation_status=ConstraintDerivationStatus.EXPLICIT_SOURCE,
            source_claim_ids=(),  # empty → must raise
        )


def test_explicit_source_with_claim_ids_ok() -> None:
    """When source_claim_ids are present, explicit_source is valid."""

    c = EvidenceConstraintPayload(
        constraint_kind="material_role",
        subject="material_role:fuel",
        predicate="material.role_required",
        value={"role": "fuel"},
        derivation_status=ConstraintDerivationStatus.EXPLICIT_SOURCE,
        source_claim_ids=("claim_abc",),
        source_spans=({"source_id": "doc1", "span_id": "span1"},),
    )
    assert c.derivation_status == "explicit_source"
    assert c.source_claim_ids == ("claim_abc",)


def test_unknown_derivation_status_rejected() -> None:
    """Inventing a new derivation_status is rejected (fail-closed)."""

    with pytest.raises(ValueError, match="unknown derivation_status"):
        EvidenceConstraintPayload(
            constraint_kind="x",
            subject="y",
            predicate="z",
            derivation_status="invented_status",
        )


# ---------------------------------------------------------------------------
# Material requirement set → constraints
# ---------------------------------------------------------------------------


def test_material_requirement_id_not_smuggled_as_claim_id() -> None:
    """P0-4 regression: requirement_id must NOT appear as claim_id.

    The legacy ``_inventory_evidence_payloads_for_patch_type`` set
    ``claim_id = req.get("requirement_id", "")`` which made
    requirement IDs look like EvidenceClaim IDs.  The new
    :class:`EvidenceConstraintPayload` does not have a ``claim_id``
    field at all — ``requirement_id`` lives in
    ``inventory_requirement_ids`` and is never confused with a
    source-backed claim id.
    """

    constraints = build_material_constraints_from_requirement_set(
        mreq_set_dump={
            "requirements": [
                {
                    "requirement_id": "mreq_001",
                    "role": "fuel",
                    "source_variant_id": "v1",
                    "resolution_status": "required",
                }
            ]
        },
        inventory_hash="ih",
        ledger_hash="lh",
    )
    assert len(constraints) == 1
    c = constraints[0]
    # Has constraint_id (deterministic hash), NOT claim_id.
    assert c.constraint_id.startswith("constraint_")
    # requirement_id is stored where it belongs.
    assert c.inventory_requirement_ids == ("mreq_001",)
    # No source_claim_ids (deterministically derived, not source-backed).
    assert c.source_claim_ids == ()
    # Status is correct.
    assert c.derivation_status == "deterministically_derived"


def test_fuel_role_gets_source_critical_criticality() -> None:
    """``role=fuel`` is the only role that gets source_critical."""

    constraints = build_material_constraints_from_requirement_set(
        mreq_set_dump={
            "requirements": [
                {"requirement_id": "m1", "role": "fuel", "resolution_status": "required"},
                {"requirement_id": "m2", "role": "coolant", "resolution_status": "required"},
                {"requirement_id": "m3", "role": "cladding", "resolution_status": "required"},
            ]
        },
        inventory_hash="ih",
    )
    assert len(constraints) == 3
    by_role = {c.value["role"]: c for c in constraints}
    assert by_role["fuel"].criticality == "source_critical"
    assert by_role["coolant"].criticality == "supporting"
    assert by_role["cladding"].criticality == "supporting"


def test_universe_constraint_carries_geometry_profile() -> None:
    """Universe constraints carry geometry_profile_id binding."""

    constraints = build_universe_constraints_from_requirement_set(
        ureq_set_dump={
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
        },
        inventory_hash="ih",
    )
    assert len(constraints) == 1
    c = constraints[0]
    assert c.value["profile_kind"] == "active_fuel_pin"
    assert c.value["geometry_profile_id"] == "prof_v1"
    assert c.derivation_status == "deterministically_derived"


def test_no_inventory_returns_empty_list() -> None:
    """Off mode (no inventory) → empty constraint list."""

    constraints = build_inventory_constraints_for_patch_type(
        patch_type="materials",
        inventory_dump=None,
        material_requirement_set_dump=None,
        universe_requirement_set_dump=None,
    )
    assert constraints == []


def test_prompt_dict_excludes_internal_hash_fields() -> None:
    """to_prompt_dict() does not expose ledger_hash / inventory_hash.

    The prompt renderer should show derivation_status + value +
    requirement_ids; internal hash fields stay in the typed payload.
    """

    c = EvidenceConstraintPayload(
        constraint_kind="material_role",
        subject="material_role:fuel",
        predicate="material.role_required",
        value={"role": "fuel"},
        inventory_requirement_ids=("mreq_1",),
        inventory_hash="secret_inventory_hash",
        ledger_hash="secret_ledger_hash",
    )
    d = c.to_prompt_dict()
    assert "constraint_id" in d
    assert "derivation_status" in d
    assert "inventory_requirement_ids" in d
    # Internal hash fields not in prompt dict.
    assert "inventory_hash" not in d
    assert "ledger_hash" not in d
    assert "constraint_hash" not in d
