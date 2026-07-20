"""Deterministic Material/Universe requirement skeletons.

These models are the source-derived contract between accepted planning facts,
the geometry inventory, and patch generation.  They deliberately contain no
LLM decisions about required identity, role, count, or source variant.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.plan_investigation.hashing import content_hash, short_id


class RequirementResolution(str, Enum):
    resolved = "resolved"
    requires_generation = "requires_generation"
    ambiguous = "ambiguous"
    unresolved = "unresolved"
    human_confirmed = "human_confirmed"


class MaterialRequirementSkeleton(AgentBaseModel):
    material_id: str
    role: str
    required_density: float | None = None
    composition_requirement: dict[str, Any] = Field(default_factory=dict)
    source_variant: str | None = None
    required_isotopes: tuple[str, ...] = Field(default_factory=tuple)
    geometry_usage: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    requirement_id: str = ""
    resolution_status: RequirementResolution = RequirementResolution.unresolved
    localized_insert_requirement_id: str | None = None
    assumptions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _require_identity(self) -> "MaterialRequirementSkeleton":
        if not self.material_id or not self.role:
            raise ValueError("material requirement requires material_id and role")
        return self


class MaterialRequirementSkeletonSet(AgentBaseModel):
    requirements: tuple[MaterialRequirementSkeleton, ...] = Field(default_factory=tuple)
    inventory_hash: str = ""
    facts_hash: str = ""
    evidence_hash: str = ""
    requirement_set_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "MaterialRequirementSkeletonSet":
        expected = content_hash({
            "requirements": [item.model_dump(mode="json") for item in self.requirements],
            "inventory_hash": self.inventory_hash,
            "facts_hash": self.facts_hash,
            "evidence_hash": self.evidence_hash,
        })
        if not self.requirement_set_hash:
            object.__setattr__(self, "requirement_set_hash", expected)
        elif self.requirement_set_hash != expected:
            raise ValueError("material requirement skeleton hash mismatch")
        return self


class UniverseRequirementSkeleton(AgentBaseModel):
    universe_id: str
    component_kind: str
    required_materials: tuple[str, ...] = Field(default_factory=tuple)
    geometry_profile: str
    protected_paths: tuple[str, ...] = Field(default_factory=tuple)
    required_cells: tuple[str, ...] = Field(default_factory=tuple)
    source_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    profile_kind: str = ""
    fuel_variant: str | None = None
    required_material_roles: tuple[str, ...] = Field(default_factory=tuple)
    resolution_status: RequirementResolution = RequirementResolution.unresolved
    localized_insert_requirement_id: str | None = None

    @model_validator(mode="after")
    def _require_identity(self) -> "UniverseRequirementSkeleton":
        if not self.universe_id or not self.component_kind or not self.geometry_profile:
            raise ValueError("universe requirement requires identity and geometry profile")
        return self


class UniverseRequirementSkeletonSet(AgentBaseModel):
    requirements: tuple[UniverseRequirementSkeleton, ...] = Field(default_factory=tuple)
    inventory_hash: str = ""
    facts_hash: str = ""
    evidence_hash: str = ""
    requirement_set_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "UniverseRequirementSkeletonSet":
        expected = content_hash({
            "requirements": [item.model_dump(mode="json") for item in self.requirements],
            "inventory_hash": self.inventory_hash,
            "facts_hash": self.facts_hash,
            "evidence_hash": self.evidence_hash,
        })
        if not self.requirement_set_hash:
            object.__setattr__(self, "requirement_set_hash", expected)
        elif self.requirement_set_hash != expected:
            raise ValueError("universe requirement skeleton hash mismatch")
        return self


def _facts_hash(accepted_facts: Any) -> str:
    return content_hash(_jsonable(accepted_facts or {}))


def _evidence_hash(evidence_ledger: Any | None) -> str:
    if evidence_ledger is None:
        return ""
    return str(getattr(evidence_ledger, "ledger_hash", "") or content_hash(evidence_ledger))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return value


def compile_material_requirement_skeleton(
    *, inventory: Any, accepted_facts: Any, evidence_ledger: Any | None = None,
    existing_material_ids: set[str] | None = None,
) -> MaterialRequirementSkeletonSet:
    """Compile one immutable material requirement per inventory role requirement."""
    existing = existing_material_ids or set()
    requirements: list[MaterialRequirementSkeleton] = []
    for req in getattr(inventory, "material_role_requirements", ()):
        variant = getattr(req, "fuel_variant_id", None)
        insert_id = getattr(req, "localized_insert_requirement_id", None)
        requirement_id = str(getattr(req, "requirement_id", ""))
        material_id = short_id("matreq", {"requirement": requirement_id})
        usage = tuple(getattr(req, "required_by_profile_ids", ()) or ())
        density = None
        for fact_variant in getattr(accepted_facts, "fuel_variant_requirements", ()):
            if variant and getattr(fact_variant, "variant_id", None) == variant:
                density = getattr(fact_variant, "density_g_cm3", None)
                break
        status = RequirementResolution.resolved if material_id in existing else RequirementResolution.requires_generation
        if getattr(req, "status", "required") in {"needs_confirmation", "ambiguous"}:
            status = RequirementResolution.ambiguous
        requirements.append(MaterialRequirementSkeleton(
            material_id=material_id, role=str(req.role), required_density=density,
            source_variant=variant, geometry_usage=usage,
            source_claim_ids=tuple(getattr(req, "source_claim_ids", ()) or ()),
            localized_insert_requirement_id=insert_id, requirement_id=requirement_id,
            resolution_status=status,
        ))
    return MaterialRequirementSkeletonSet(
        requirements=tuple(requirements), inventory_hash=str(getattr(inventory, "inventory_hash", "")),
        facts_hash=_facts_hash(accepted_facts), evidence_hash=_evidence_hash(evidence_ledger),
    )


def compile_universe_requirement_skeleton(
    *, inventory: Any, accepted_facts: Any, evidence_ledger: Any | None = None,
    existing_universe_ids: set[str] | None = None,
) -> UniverseRequirementSkeletonSet:
    """Compile one immutable Universe requirement per resolved radial profile."""
    existing = existing_universe_ids or set()
    requirements: list[UniverseRequirementSkeleton] = []
    for profile in getattr(inventory, "radial_profiles", ()):
        requirement_id = short_id("unireq", {"profile": profile.profile_id, "variant": profile.fuel_variant_id or ""})
        universe_id = short_id("unireq", {"profile": profile.profile_id})
        insert_id = next((binding.insert_requirement_id for binding in getattr(inventory, "localized_insert_profiles", ()) if binding.profile_id == profile.profile_id), None)
        status = RequirementResolution.resolved if profile.status == "resolved" else RequirementResolution.unresolved
        if universe_id not in existing and status is RequirementResolution.resolved:
            status = RequirementResolution.requires_generation
        requirements.append(UniverseRequirementSkeleton(
            universe_id=universe_id, component_kind=profile.component_kind,
            required_materials=tuple(profile.required_material_roles), geometry_profile=profile.profile_id,
            protected_paths=tuple(profile.protected_through_path_roles),
            required_cells=tuple(profile.required_cell_roles), source_requirement_ids=(requirement_id,),
            source_claim_ids=tuple(profile.source_claim_ids), source_span_ids=tuple(profile.source_span_ids),
            profile_kind=profile.profile_kind, fuel_variant=profile.fuel_variant_id,
            required_material_roles=tuple(profile.required_material_roles),
            resolution_status=status, localized_insert_requirement_id=insert_id,
        ))
    return UniverseRequirementSkeletonSet(
        requirements=tuple(requirements), inventory_hash=str(getattr(inventory, "inventory_hash", "")),
        facts_hash=_facts_hash(accepted_facts), evidence_hash=_evidence_hash(evidence_ledger),
    )


__all__ = [
    "RequirementResolution", "MaterialRequirementSkeleton", "MaterialRequirementSkeletonSet",
    "UniverseRequirementSkeleton", "UniverseRequirementSkeletonSet",
    "compile_material_requirement_skeleton", "compile_universe_requirement_skeleton",
]
