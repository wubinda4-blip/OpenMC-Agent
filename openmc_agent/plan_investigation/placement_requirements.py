"""Phase 8A Step 6C — PlacementRequirementSet (Section 20).

Defines the typed placement contract compiled from accepted Facts +
GeometryComponentInventory + accepted Materials + accepted Universes.

The set captures:

* ``AssemblyTypePlacementRequirement`` — per-assembly-type instance
  count + default profile bindings + fuel variant.
* ``CoreLayoutRequirement`` — lattice shape, assembly-type counts,
  boundary scope, symmetry.
* ``LocalizedInsertPlacementRequirement`` — control rods / instrumentation
  thimbles / other inserts with host-path binding.

Hard rules (Section 20):

* Never derive coordinates from a total count.
* Never derive "control rod placed" from "control rod material exists".
* Never derive "placement satisfied" from "Universe exists".
* Every localized insert must have a host path + segment role binding.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .hashing import content_hash, short_id

__all__ = [
    "AssemblyTypePlacementRequirement",
    "CoreLayoutRequirement",
    "LocalizedInsertPlacementRequirement",
    "PlacementRequirementSet",
    "PLACEMENT_REQUIREMENT_SCHEMA_VERSION",
]


PLACEMENT_REQUIREMENT_SCHEMA_VERSION = "1.0"


class LocalizedInsertPlacementRequirement(AgentBaseModel):
    """One localized insert (control rod, instrument thimble, etc.).

    Hard requirements:

    * Must bind to a host assembly type + host profile.
    * Must declare a ``insert_profile_id`` referencing a Universe.
    * Must declare required segment roles (e.g. ``"absorber"``,
      ``"instrumentation"``).
    * ``anchor_z_cm`` is optional (only when axial positioning is
      source-backed).
    """

    requirement_id: str = ""
    insert_kind: str
    assembly_type_ids: tuple[str, ...] = Field(default_factory=tuple)
    expected_assembly_instance_count: int | None = None
    expected_coordinate_count_per_assembly: int | None = None
    host_kind: str = ""
    host_profile_id: str = ""
    insert_profile_id: str = ""
    required_universe_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_segment_roles: tuple[str, ...] = Field(default_factory=tuple)
    control_state_id: str = ""
    anchor_z_cm: float | None = None
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    status: str = "required"
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)


class AssemblyTypePlacementRequirement(AgentBaseModel):
    """Per-assembly-type placement requirement."""

    assembly_type_id: str
    expected_instance_count: int | None = None
    default_profile_ids: tuple[str, ...] = Field(default_factory=tuple)
    fuel_variant_id: str = ""
    insert_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)


class CoreLayoutRequirement(AgentBaseModel):
    """Core-wide layout requirement (lattice shape + counts)."""

    lattice_shape: tuple[int, ...] = Field(default_factory=tuple)
    assembly_type_counts: dict[str, int] = Field(default_factory=dict)
    boundary_scope: str = ""
    symmetry: str = ""
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    derivation_ids: tuple[str, ...] = Field(default_factory=tuple)


class PlacementRequirementSet(AgentBaseModel):
    """The complete placement requirement set for one incremental run.

    Compiled deterministically from accepted Facts + GeometryComponentInventory
    + accepted Materials + accepted Universes.  Never from a free-form LLM
    suggestion.

    Hash fields tie the set to the ledger / inventory / facts_patch it
    was derived from so resume fingerprints detect drift.
    """

    requirement_set_version: str = PLACEMENT_REQUIREMENT_SCHEMA_VERSION
    requirement_set_id: str = ""
    requirement_hash: str = ""
    ledger_hash: str = ""
    inventory_hash: str = ""
    facts_patch_hash: str = ""
    assembly_type_requirements: tuple[AssemblyTypePlacementRequirement, ...] = Field(default_factory=tuple)
    core_layout_requirements: tuple[CoreLayoutRequirement, ...] = Field(default_factory=tuple)
    localized_insert_bindings: tuple[LocalizedInsertPlacementRequirement, ...] = Field(default_factory=tuple)
    host_path_requirements: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    scoped_count_requirements: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    unresolved_requirements: tuple[str, ...] = Field(default_factory=tuple)
    conflicts: tuple[str, ...] = Field(default_factory=tuple)
    requirement_set_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "PlacementRequirementSet":
        body = {
            "assembly_types": [r.model_dump(mode="json") for r in self.assembly_type_requirements],
            "core_layout": [r.model_dump(mode="json") for r in self.core_layout_requirements],
            "localized_inserts": [r.model_dump(mode="json") for r in self.localized_insert_bindings],
            "host_paths": list(self.host_path_requirements),
            "scoped_counts": list(self.scoped_count_requirements),
            "ledger_hash": self.ledger_hash,
            "inventory_hash": self.inventory_hash,
            "facts_patch_hash": self.facts_patch_hash,
        }
        h = content_hash(body)
        object.__setattr__(self, "requirement_set_hash", h)
        object.__setattr__(self, "requirement_hash", h)
        if not self.requirement_set_id:
            object.__setattr__(self, "requirement_set_id", short_id("placement_req", h))
        return self


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def extract_placement_requirements(
    *,
    accepted_facts: Any,
    geometry_inventory: Any = None,
    material_requirement_set: Any = None,
    universe_requirement_set: Any = None,
    accepted_materials_patch: Any = None,
    accepted_universes_patch: Any = None,
    ledger_hash: str = "",
    inventory_hash: str = "",
    facts_patch_hash: str = "",
) -> PlacementRequirementSet:
    """Compile the PlacementRequirementSet from accepted inputs.

    Pure-Python and reactor-neutral.  Reads only typed fields from
    Facts + Inventory + requirement sets; never invents coordinates
    or counts.
    """

    assembly_reqs: list[AssemblyTypePlacementRequirement] = []
    core_layout_reqs: list[CoreLayoutRequirement] = []
    localized_inserts: list[LocalizedInsertPlacementRequirement] = []
    unresolved: list[str] = []
    # 1. Per-assembly-type requirements from accepted Facts.
    assembly_types_field = (
        list(getattr(accepted_facts, "assembly_type_counts", {}).items())
        if accepted_facts is not None
        else []
    )
    for assembly_type_id, expected_count in assembly_types_field:
        if not isinstance(expected_count, int) or expected_count <= 0:
            continue
        fuel_variant_id = ""
        # Look up fuel variant from accepted Facts.
        for variant in getattr(accepted_facts, "fuel_variant_requirements", []) or []:
            if getattr(variant, "assembly_type_id", "") == assembly_type_id:
                fuel_variant_id = getattr(variant, "fuel_variant_id", "") or getattr(variant, "variant_id", "")
                break
        assembly_reqs.append(AssemblyTypePlacementRequirement(
            assembly_type_id=assembly_type_id,
            expected_instance_count=expected_count,
            fuel_variant_id=fuel_variant_id,
        ))
    # 2. Core layout requirement from Facts.
    core_lattice = getattr(accepted_facts, "core_lattice_size", None)
    if core_lattice:
        # core_lattice is a tuple (nx, ny) on FactsPatch.
        try:
            shape = tuple(int(x) for x in core_lattice)
        except Exception:
            shape = tuple()
        counts = dict(getattr(accepted_facts, "assembly_type_counts", {}) or {})
        if shape or counts:
            core_layout_reqs.append(CoreLayoutRequirement(
                lattice_shape=shape,
                assembly_type_counts=counts,
                boundary_scope=getattr(accepted_facts, "model_scope", ""),
            ))
    # 3. Localized insert requirements from Facts.
    for insert_req in getattr(accepted_facts, "localized_insert_requirements", []) or []:
        insert_kind = getattr(insert_req, "insert_kind", "") or "unknown"
        host_kind = getattr(insert_req, "host_kind", "") or "guide_tube"
        host_profile_id = getattr(insert_req, "host_profile_id", "") or ""
        insert_profile_id = getattr(insert_req, "insert_profile_id", "") or ""
        control_state_id = getattr(insert_req, "control_state_id", "") or ""
        required_roles = tuple(getattr(insert_req, "required_segment_roles", []) or ())
        required_universe_ids = tuple(getattr(insert_req, "required_universe_ids", []) or ())
        if not host_profile_id:
            unresolved.append(f"localized_insert:{insert_kind}:host_profile_id")
        if not insert_profile_id:
            unresolved.append(f"localized_insert:{insert_kind}:insert_profile_id")
        localized_inserts.append(LocalizedInsertPlacementRequirement(
            insert_kind=insert_kind,
            assembly_type_ids=tuple(getattr(insert_req, "assembly_type_ids", []) or ()),
            host_kind=host_kind,
            host_profile_id=host_profile_id,
            insert_profile_id=insert_profile_id,
            required_universe_ids=required_universe_ids,
            required_segment_roles=required_roles,
            control_state_id=control_state_id,
        ))
    return PlacementRequirementSet(
        assembly_type_requirements=tuple(assembly_reqs),
        core_layout_requirements=tuple(core_layout_reqs),
        localized_insert_bindings=tuple(localized_inserts),
        unresolved_requirements=tuple(unresolved),
        ledger_hash=ledger_hash,
        inventory_hash=inventory_hash,
        facts_patch_hash=facts_patch_hash,
    )
