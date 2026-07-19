"""Deterministic Material Binding Skeleton (Phase 8B Step 1).

Each ``MaterialRequirement`` generates exactly one ``MaterialBindingSlot``.
The LLM never assigns ``material_id``, ``role``, ``requirement_id``, or
``source_variant_id`` — those are Python-determined immutable fields.

Resolution states:
* ``uniquely_resolved`` — exactly one candidate material exists.
* ``requires_generation`` — no candidate material exists.
* ``ambiguous`` — multiple candidates exist.
* ``unresolved`` — no resolution attempted yet.
* ``human_confirmed`` — a human confirmed the binding.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from openmc_agent.schemas import AgentBaseModel


class MaterialBindingResolution(str, Enum):
    uniquely_resolved = "uniquely_resolved"
    requires_generation = "requires_generation"
    ambiguous = "ambiguous"
    unresolved = "unresolved"
    human_confirmed = "human_confirmed"


class MaterialBindingSlot(AgentBaseModel):
    slot_id: str
    requirement_id: str
    required_role: str
    source_variant_id: str | None = None
    localized_insert_requirement_id: str | None = None
    required_composition_status: str = ""
    source_claim_ids: list[str] = []
    source_span_ids: list[str] = []
    assigned_material_id: str | None = None
    candidate_material_ids: list[str] = []
    resolution_status: MaterialBindingResolution = MaterialBindingResolution.unresolved
    immutable_fields: list[str] = [
        "material_id", "role", "requirement_id", "source_variant_id",
    ]
    slot_hash: str = ""


class MaterialBindingSkeleton(AgentBaseModel):
    slots: list[MaterialBindingSlot] = []
    inventory_hash: str = ""
    requirement_set_hash: str = ""
    materials_patch_hash: str = ""

    @property
    def resolved_count(self) -> int:
        return sum(
            1 for s in self.slots
            if s.resolution_status in (
                MaterialBindingResolution.uniquely_resolved,
                MaterialBindingResolution.human_confirmed,
            )
        )

    @property
    def requires_generation_count(self) -> int:
        return sum(1 for s in self.slots if s.resolution_status == MaterialBindingResolution.requires_generation)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for s in self.slots if s.resolution_status == MaterialBindingResolution.ambiguous)


def _compute_slot_hash(slot: MaterialBindingSlot) -> str:
    import hashlib
    payload = {
        "requirement_id": slot.requirement_id,
        "required_role": slot.required_role,
        "source_variant_id": slot.source_variant_id,
        "assigned_material_id": slot.assigned_material_id,
    }
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


def compile_material_binding_skeleton(
    *,
    inventory: Any,
    material_requirement_set: Any,
    accepted_facts: Any,
    evidence_ledger: Any | None = None,
    existing_materials_patch: Any | None = None,
) -> MaterialBindingSkeleton:
    """Compile a deterministic binding skeleton from the inventory and
    material requirement set.

    Each ``MaterialRequirement`` generates one ``MaterialBindingSlot``.
    The skeleton is a pure data structure — no LLM calls, no side effects.
    """
    slots: list[MaterialBindingSlot] = []
    known_materials: dict[str, dict[str, Any]] = {}
    if existing_materials_patch is not None:
        materials_list = (
            existing_materials_patch.get("materials", [])
            if isinstance(existing_materials_patch, dict)
            else []
        )
        for mat in materials_list:
            mid = mat.get("material_id")
            if mid:
                known_materials[str(mid)] = mat

    for req in material_requirement_set.requirements:
        requirement_id = req.requirement_id
        role = getattr(req, "role", "") or ""
        variant_id = getattr(req, "source_variant_id", None)
        localized_insert_id = getattr(req, "localized_insert_requirement_id", None)
        composition_status = getattr(req, "required_composition_status", "") or ""

        # Find candidate materials: existing materials that match role + variant.
        candidates: list[str] = []
        for mid, mat in known_materials.items():
            mat_role = mat.get("role", "")
            mat_variant = mat.get("source_variant_id")
            if mat_role == role and (not variant_id or mat_variant == variant_id):
                candidates.append(mid)

        # Resolution
        if len(candidates) == 1:
            assigned = candidates[0]
            resolution = MaterialBindingResolution.uniquely_resolved
        elif len(candidates) > 1:
            assigned = None
            resolution = MaterialBindingResolution.ambiguous
        else:
            assigned = None
            resolution = MaterialBindingResolution.requires_generation

        slot = MaterialBindingSlot(
            slot_id=f"mat_slot_{requirement_id}",
            requirement_id=requirement_id,
            required_role=role,
            source_variant_id=variant_id,
            localized_insert_requirement_id=localized_insert_id,
            required_composition_status=composition_status,
            assigned_material_id=assigned,
            candidate_material_ids=candidates,
            resolution_status=resolution,
        )
        slot.slot_hash = _compute_slot_hash(slot)
        slots.append(slot)

    inventory_hash = getattr(inventory, "inventory_hash", "") or ""
    req_set_hash = getattr(material_requirement_set, "requirement_set_hash", "") or ""

    return MaterialBindingSkeleton(
        slots=slots,
        inventory_hash=inventory_hash,
        requirement_set_hash=req_set_hash,
    )
