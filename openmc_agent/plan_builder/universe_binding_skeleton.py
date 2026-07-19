"""Deterministic Universe Binding Skeleton (Phase 8B Step 1).

Each ``UniverseRequirement`` generates exactly one ``UniverseBindingSlot``.
The LLM never assigns ``universe_id``, ``geometry_profile_id``, or
``source_requirement_ids`` — those are Python-determined immutable fields.

The skeleton is compiled from the inventory, universe requirement set, and
existing material bindings.  Fragment generation carries the ``slot_id`` so
the merge step can verify immutable field integrity.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from openmc_agent.schemas import AgentBaseModel


class UniverseBindingResolution(str, Enum):
    uniquely_resolved = "uniquely_resolved"
    requires_generation = "requires_generation"
    ambiguous = "ambiguous"
    unresolved = "unresolved"
    human_confirmed = "human_confirmed"


class UniverseBindingSlot(AgentBaseModel):
    slot_id: str
    universe_requirement_id: str
    geometry_profile_id: str | None = None
    source_requirement_ids: list[str] = []
    component_kind: str = ""
    profile_kind: str = ""
    fuel_variant_id: str | None = None
    required_cell_roles: list[str] = []
    required_material_roles: list[str] = []
    resolved_material_bindings: dict[str, str] = {}
    assigned_universe_id: str | None = None
    candidate_universe_ids: list[str] = []
    resolution_status: UniverseBindingResolution = UniverseBindingResolution.unresolved
    immutable_fields: list[str] = [
        "universe_id", "geometry_profile_id", "source_requirement_ids",
        "slot_id", "component_kind", "profile_kind",
    ]
    slot_hash: str = ""


class UniverseBindingSkeleton(AgentBaseModel):
    slots: list[UniverseBindingSlot] = []
    inventory_hash: str = ""
    requirement_set_hash: str = ""
    materials_patch_hash: str = ""
    universes_patch_hash: str = ""

    @property
    def resolved_count(self) -> int:
        return sum(
            1 for s in self.slots
            if s.resolution_status in (
                UniverseBindingResolution.uniquely_resolved,
                UniverseBindingResolution.human_confirmed,
            )
        )

    @property
    def requires_generation_count(self) -> int:
        return sum(1 for s in self.slots if s.resolution_status == UniverseBindingResolution.requires_generation)


def _compute_slot_hash(slot: UniverseBindingSlot) -> str:
    import hashlib
    payload = {
        "universe_requirement_id": slot.universe_requirement_id,
        "geometry_profile_id": slot.geometry_profile_id,
        "source_requirement_ids": sorted(slot.source_requirement_ids),
        "component_kind": slot.component_kind,
        "assigned_universe_id": slot.assigned_universe_id,
    }
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


def compile_universe_binding_skeleton(
    *,
    inventory: Any,
    universe_requirement_set: Any,
    material_requirement_set: Any,
    material_binding_skeleton: Any,
    accepted_facts: Any,
    existing_universes_patch: Any | None = None,
    existing_materials_patch: Any | None = None,
) -> UniverseBindingSkeleton:
    """Compile a deterministic universe binding skeleton.

    Each ``UniverseRequirement`` generates one ``UniverseBindingSlot``.
    The geometry_profile_id and source_requirement_ids are injected by
    Python from the inventory and requirement sets.
    """
    slots: list[UniverseBindingSlot] = []
    known_universes: dict[str, dict[str, Any]] = {}
    if existing_universes_patch is not None:
        universes_list = (
            existing_universes_patch.get("universes", [])
            if isinstance(existing_universes_patch, dict)
            else []
        )
        for univ in universes_list:
            uid = univ.get("universe_id")
            if uid:
                known_universes[str(uid)] = univ

    # Build material role lookup from the material skeleton.
    material_role_by_slot: dict[str, str] = {}
    if material_binding_skeleton is not None:
        for slot in material_binding_skeleton.slots:
            material_role_by_slot[slot.requirement_id] = slot.required_role

    for req in universe_requirement_set.requirements:
        requirement_id = req.requirement_id
        profile_id = getattr(req, "geometry_profile_id", None)
        comp_kind = getattr(req, "component_kind", "") or ""
        prof_kind = getattr(req, "profile_kind", "") or ""
        variant_id = getattr(req, "fuel_variant_id", None)
        src_ids = list(getattr(req, "source_requirement_ids", []) or [])
        cell_roles = list(getattr(req, "required_cell_roles", []) or [])
        mat_roles = list(getattr(req, "required_material_roles", []) or [])

        # Resolve material bindings for this requirement.
        resolved_mats: dict[str, str] = {}
        for mat_req_id in src_ids:
            if mat_req_id in material_role_by_slot:
                resolved_mats[mat_req_id] = material_role_by_slot[mat_req_id]

        # Find candidate universes that match profile_id.
        candidates: list[str] = []
        for uid, univ in known_universes.items():
            univ_profile = (
                univ.get("geometry_profile_id")
                or (univ.get("metadata") or {}).get("geometry_profile_id")
            )
            if profile_id and univ_profile == profile_id:
                candidates.append(uid)

        # Resolution
        if len(candidates) == 1:
            assigned = candidates[0]
            resolution = UniverseBindingResolution.uniquely_resolved
        elif len(candidates) > 1:
            assigned = None
            resolution = UniverseBindingResolution.ambiguous
        else:
            assigned = None
            resolution = UniverseBindingResolution.requires_generation

        slot = UniverseBindingSlot(
            slot_id=f"uni_slot_{requirement_id}",
            universe_requirement_id=requirement_id,
            geometry_profile_id=profile_id,
            source_requirement_ids=src_ids,
            component_kind=comp_kind,
            profile_kind=prof_kind,
            fuel_variant_id=variant_id,
            required_cell_roles=cell_roles,
            required_material_roles=mat_roles,
            resolved_material_bindings=resolved_mats,
            assigned_universe_id=assigned,
            candidate_universe_ids=candidates,
            resolution_status=resolution,
        )
        slot.slot_hash = _compute_slot_hash(slot)
        slots.append(slot)

    inventory_hash = getattr(inventory, "inventory_hash", "") or ""
    req_set_hash = getattr(universe_requirement_set, "requirement_set_hash", "") or ""
    mat_bind_hash = ""
    if material_binding_skeleton is not None:
        mat_bind_hash = getattr(material_binding_skeleton, "requirement_set_hash", "") or ""

    return UniverseBindingSkeleton(
        slots=slots,
        inventory_hash=inventory_hash,
        requirement_set_hash=req_set_hash,
        materials_patch_hash=mat_bind_hash,
    )
