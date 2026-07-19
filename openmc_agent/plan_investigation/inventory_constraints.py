"""Phase 8A Step 6 — convert inventory requirement sets to typed
:class:`EvidenceConstraintPayload` objects.

This replaces the legacy ``_inventory_evidence_payloads_for_patch_type``
helper that misrepresented derived requirements as ``status="explicit"``
``EvidenceClaims`` with empty ``source_spans`` (the P0-4 bug).  The new
payloads carry ``derivation_status=deterministically_derived`` and
never claim to be source quotations.

The function is pure Python and reactor-neutral: it only reads the
inventory / requirement-set structures already stored on the
PlanBuildState by the deterministic compiler.
"""

from __future__ import annotations

from typing import Any

from .constraint_payloads import (
    ConstraintDerivationStatus,
    EvidenceConstraintPayload,
)

__all__ = [
    "build_material_constraints_from_requirement_set",
    "build_universe_constraints_from_requirement_set",
    "build_inventory_constraints_for_patch_type",
]


def build_material_constraints_from_requirement_set(
    *,
    mreq_set_dump: dict[str, Any],
    inventory_hash: str,
    ledger_hash: str = "",
) -> list[EvidenceConstraintPayload]:
    """Convert a MaterialGenerationRequirementSet to typed constraints.

    ``mreq_set_dump`` is the serialized
    :class:`MaterialGenerationRequirementSet` (a dict matching
    ``model_dump(mode='json')``).  Each ``requirements`` entry becomes
    one :class:`EvidenceConstraintPayload` with
    ``derivation_status=deterministically_derived``.
    """

    out: list[EvidenceConstraintPayload] = []
    for req in mreq_set_dump.get("requirements", []) or []:
        role = req.get("role", "")
        # Source-backed material claims (rare; only when the
        # investigation stage explicitly produced one) are flagged
        # via source_claim_ids on the requirement entry.  When none
        # are present, the constraint is deterministically derived
        # from the inventory's role analysis.
        source_claim_ids = tuple(req.get("source_claim_ids", []) or ())
        derivation = (
            ConstraintDerivationStatus.EXPLICIT_SOURCE
            if source_claim_ids
            else ConstraintDerivationStatus.DETERMINISTICALLY_DERIVED
        )
        criticality = "source_critical" if role == "fuel" else "supporting"
        unresolved = tuple(req.get("unresolved_fields", []) or ())
        if not req.get("material_id_resolved", True):
            unresolved = unresolved + ("material_id",)
        constraint = EvidenceConstraintPayload(
            constraint_kind="material_role",
            subject=f"material_role:{role}",
            predicate="material.role_required",
            value={
                "role": role,
                "fuel_variant_id": req.get("source_variant_id"),
                "localized_insert_requirement_id": req.get(
                    "localized_insert_requirement_id"
                ),
                "resolution_status": req.get("resolution_status"),
            },
            derivation_status=derivation,
            source_claim_ids=source_claim_ids,
            inventory_requirement_ids=(req.get("requirement_id", ""),),
            inventory_hash=inventory_hash,
            ledger_hash=ledger_hash,
            criticality=criticality,
            unresolved_fields=unresolved,
        )
        out.append(constraint)
    return out


def build_universe_constraints_from_requirement_set(
    *,
    ureq_set_dump: dict[str, Any],
    inventory_hash: str,
    ledger_hash: str = "",
) -> list[EvidenceConstraintPayload]:
    """Convert an InventoryUniverseRequirementSet to typed constraints."""

    out: list[EvidenceConstraintPayload] = []
    for req in ureq_set_dump.get("requirements", []) or []:
        profile_kind = req.get("profile_kind", "")
        component_kind = req.get("component_kind", "")
        source_claim_ids = tuple(req.get("source_claim_ids", []) or ())
        derivation = (
            ConstraintDerivationStatus.EXPLICIT_SOURCE
            if source_claim_ids
            else ConstraintDerivationStatus.DETERMINISTICALLY_DERIVED
        )
        unresolved = tuple(req.get("unresolved_fields", []) or ())
        if not req.get("geometry_profile_id_resolved", True):
            unresolved = unresolved + ("geometry_profile_id",)
        constraint = EvidenceConstraintPayload(
            constraint_kind="geometry_profile",
            subject=f"geometry_profile:{component_kind or profile_kind}",
            predicate="geometry.profile_required",
            value={
                "profile_kind": profile_kind,
                "component_kind": component_kind,
                "fuel_variant_id": req.get("fuel_variant_id"),
                "required_cell_roles": req.get("required_cell_roles", []),
                "required_material_roles": req.get("required_material_roles", []),
                "geometry_profile_id": req.get("geometry_profile_id"),
            },
            derivation_status=derivation,
            source_claim_ids=source_claim_ids,
            inventory_requirement_ids=(req.get("requirement_id", ""),),
            inventory_hash=inventory_hash,
            ledger_hash=ledger_hash,
            criticality="supporting",
            unresolved_fields=unresolved,
        )
        out.append(constraint)
    return out


def build_inventory_constraints_for_patch_type(
    *,
    patch_type: str,
    inventory_dump: dict[str, Any] | None,
    material_requirement_set_dump: dict[str, Any] | None,
    universe_requirement_set_dump: dict[str, Any] | None,
    ledger_hash: str = "",
) -> list[EvidenceConstraintPayload]:
    """Build the constraint list for one patch type.

    Returns an empty list when no inventory is present (off mode),
    preserving byte-identical legacy prompt behaviour.
    """

    if not inventory_dump:
        return []
    inventory_hash = inventory_dump.get("inventory_hash", "")
    if patch_type == "materials" and material_requirement_set_dump:
        return build_material_constraints_from_requirement_set(
            mreq_set_dump=material_requirement_set_dump,
            inventory_hash=inventory_hash,
            ledger_hash=ledger_hash,
        )
    if patch_type == "universes" and universe_requirement_set_dump:
        return build_universe_constraints_from_requirement_set(
            ureq_set_dump=universe_requirement_set_dump,
            inventory_hash=inventory_hash,
            ledger_hash=ledger_hash,
        )
    return []
