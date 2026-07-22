"""Geometry Component Inventory + deterministic compiler (Phase 8A Step 5).

The Inventory is a deterministic, evidence-driven summary of every
component a Materials/Universes patch must declare.  It replaces the
legacy ``if has_axial_geometry: add end_plug/gas_gap/water_pin`` blanket
rule with a source-driven compilation.

Inputs:
* Accepted FactsPatch (must have passed Facts Gate).
* Shared PlanningEvidenceLedger with accepted component claims.
* SourceIndexes (for span validation).
* Confirmed human facts (optional).

Outputs:
* :class:`GeometryComponentInventory` with radial profiles, axial
  regions, localized inserts, material role requirements, and
  unresolved components.

Hard rules:
* The compiler NEVER invents components.  ``has_axial_geometry=True``
  alone is NOT enough to declare end_plug / gas_gap / water_pin; those
  must come from explicit component claims or accepted Facts fields.
* All IDs are deterministic.
* Conflicts and unresolved source-critical components are surfaced, not
  silently resolved.
* No reactor-specific branches (no VERA / PWR / BWR terms in the
  compiler).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .component_evidence import (
    COMPONENT_KINDS,
    PROFILE_KINDS,
    ComponentKind,
    ProfileKind,
)
from .errors import PlanInvestigationIssue
from .evidence_ledger import PlanningEvidenceLedger
from .hashing import content_hash, short_id
from .models import EvidenceClaim, EvidenceStatus
from .source_index import SourceIndex

__all__ = [
    "RadialLayerRequirement",
    "RadialProfileRequirement",
    "AxialRegionRequirement",
    "MaterialRoleRequirement",
    "LocalizedInsertProfileBinding",
    "UnresolvedComponent",
    "GeometryComponentInventory",
    "GeometryInventoryCoverageReport",
    "compile_geometry_component_inventory",
    "INVENTORY_SCHEMA_VERSION",
    "INVENTORY_NOT_FACTS_ACCEPTED_CODE",
]


INVENTORY_SCHEMA_VERSION: str = "0.1"
INVENTORY_NOT_FACTS_ACCEPTED_CODE: str = "planning.inventory.facts_gate_not_accepted"


# Standard supporting material roles for each component kind.
# These are structurally necessary for the universe definition — every
# fuel pin has cladding and coolant; every guide tube has structural
# walls and internal coolant; etc.  Without declaring these roles, the
# MaterialsPatch omits them, and the LLM generates universe cells that
# reference non-existent material IDs (e.g. "helium", "water").
_SUPPORTING_MATERIAL_ROLES: dict[str, tuple[str, ...]] = {
    "fuel_pin": ("cladding", "coolant", "gas"),
    "guide_tube": ("structural", "coolant"),
    "instrument_tube": ("structural", "coolant"),
    "control_rod": ("cladding", "coolant"),
    "absorber_insert": ("cladding", "coolant"),
    "poison_insert": ("cladding", "coolant"),
    "pyrex_rod": ("cladding", "coolant"),
    "thimble_plug": ("coolant",),
    "end_plug": ("structural",),
    "gas_gap": ("structural",),
    "water_pin": (),
    "moderator_region": (),
}

# Standard cell roles implied by each component kind.
_SUPPORTING_CELL_ROLES: dict[str, tuple[str, ...]] = {
    "fuel_pin": ("fuel", "cladding", "gas", "coolant"),
    "guide_tube": ("structural", "coolant"),
    "instrument_tube": ("structural", "coolant"),
    "control_rod": ("absorber", "cladding", "coolant"),
    "absorber_insert": ("absorber", "cladding", "coolant"),
    "poison_insert": ("poison", "cladding", "coolant"),
    "pyrex_rod": ("poison", "cladding", "coolant"),
    "thimble_plug": ("structural", "coolant"),
    "water_pin": ("coolant",),
    "moderator_region": ("coolant",),
}


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class RadialLayerRequirement(AgentBaseModel):
    """One layer (annulus) inside a radial profile."""

    layer_id: str
    role: str
    material_role: str = ""
    r_min_cm: float | None = None
    r_max_cm: float | None = None
    region_kind: str = ""
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)


class RadialProfileRequirement(AgentBaseModel):
    """A radial cross-section profile that must be materialized as a Universe."""

    profile_id: str
    profile_kind: str
    component_kind: str
    fuel_variant_id: str | None = None
    required_cell_roles: tuple[str, ...] = Field(default_factory=tuple)
    required_material_roles: tuple[str, ...] = Field(default_factory=tuple)
    radial_layers: tuple[RadialLayerRequirement, ...] = Field(default_factory=tuple)
    protected_through_path_roles: tuple[str, ...] = Field(default_factory=tuple)
    applicable_assembly_type_ids: tuple[str, ...] = Field(default_factory=tuple)
    applicable_pin_roles: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    status: str = "resolved"  # resolved | unresolved | needs_confirmation
    required_by_axial_region_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_by_insert_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("profile_kind")
    @classmethod
    def _profile_in_ontology(cls, value: str) -> str:
        if value not in PROFILE_KINDS:
            raise PlanInvestigationIssue(
                "plan_investigation.profile_kind_not_in_ontology",
                f"profile_kind '{value}' is not in the ontology",
            )
        return value

    @field_validator("component_kind")
    @classmethod
    def _component_in_ontology(cls, value: str) -> str:
        if value not in COMPONENT_KINDS:
            raise PlanInvestigationIssue(
                "plan_investigation.component_kind_not_in_ontology",
                f"component_kind '{value}' is not in the ontology",
            )
        return value


class AxialRegionRequirement(AgentBaseModel):
    """A z-range with structural meaning (active fuel, plenum, end plug, etc.)."""

    region_id: str
    region_kind: str
    z_min_cm: float | None = None
    z_max_cm: float | None = None
    host_component_kind: str | None = None
    replacement_profile_id: str | None = None
    continues_through_path: bool = True
    applicable_assembly_type_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)


class MaterialRoleRequirement(AgentBaseModel):
    """One material role the Materials patch must satisfy."""

    requirement_id: str
    role: str
    preferred_identity: str | None = None
    required_by_component_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_by_profile_ids: tuple[str, ...] = Field(default_factory=tuple)
    fuel_variant_id: str | None = None
    localized_insert_requirement_id: str | None = None
    mixture_components: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    status: str = "required"  # required | resolved | needs_library | needs_confirmation


class LocalizedInsertProfileBinding(AgentBaseModel):
    """Binding between a Facts localized_insert_requirement and an Inventory profile."""

    insert_requirement_id: str
    insert_kind: str
    profile_id: str
    host_component_kind: str = "guide_tube"
    material_role: str = "absorber"
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)


class UnresolvedComponent(AgentBaseModel):
    """A component the Inventory could not fully resolve.

    ``blocking_patch_types`` declares which patch generations cannot
    proceed until this is resolved (e.g. ``("materials", "universes")``).
    """

    component_id: str
    component_kind: str
    missing_fields: tuple[str, ...] = Field(default_factory=tuple)
    blocking_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    requires_human: bool = False
    suggested_research_terms: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("component_kind")
    @classmethod
    def _component_in_ontology(cls, value: str) -> str:
        if value not in COMPONENT_KINDS:
            raise PlanInvestigationIssue(
                "plan_investigation.component_kind_not_in_ontology",
                f"component_kind '{value}' is not in the ontology",
            )
        return value


# ---------------------------------------------------------------------------
# Inventory + coverage report
# ---------------------------------------------------------------------------


class GeometryComponentInventory(AgentBaseModel):
    """The deterministic, evidence-driven component inventory.

    Build via :func:`compile_geometry_component_inventory`.  Never
    construct by hand with arbitrary content.
    """

    inventory_version: str = INVENTORY_SCHEMA_VERSION
    inventory_id: str
    requirement_hash: str
    source_index_hash: str
    ledger_hash: str
    accepted_facts_patch_hash: str = ""
    radial_profiles: tuple[RadialProfileRequirement, ...] = Field(default_factory=tuple)
    axial_regions: tuple[AxialRegionRequirement, ...] = Field(default_factory=tuple)
    localized_insert_profiles: tuple[LocalizedInsertProfileBinding, ...] = Field(default_factory=tuple)
    homogenized_components: tuple[RadialProfileRequirement, ...] = Field(default_factory=tuple)
    material_role_requirements: tuple[MaterialRoleRequirement, ...] = Field(default_factory=tuple)
    unresolved_components: tuple[UnresolvedComponent, ...] = Field(default_factory=tuple)
    conflicts: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    inventory_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _compute_inventory_hash(self) -> "GeometryComponentInventory":
        payload = {
            "v": self.inventory_version,
            "rh": self.requirement_hash,
            "sih": self.source_index_hash,
            "lh": self.ledger_hash,
            "fh": self.accepted_facts_patch_hash,
            "rp": [r.model_dump(mode="json") for r in self.radial_profiles],
            "ar": [r.model_dump(mode="json") for r in self.axial_regions],
            "li": [b.model_dump(mode="json") for b in self.localized_insert_profiles],
            "hc": [h.model_dump(mode="json") for h in self.homogenized_components],
            "mr": [m.model_dump(mode="json") for m in self.material_role_requirements],
            "uc": [u.model_dump(mode="json") for u in self.unresolved_components],
        }
        expected = content_hash(payload)
        if not self.inventory_hash:
            object.__setattr__(self, "inventory_hash", expected)
        elif self.inventory_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.inventory_hash_mismatch",
                "inventory_hash does not match the recomputed value",
                details={"expected": expected, "actual": self.inventory_hash},
            )
        return self

    @property
    def declared_material_roles(self) -> tuple[str, ...]:
        seen: list[str] = []
        for req in self.material_role_requirements:
            if req.role not in seen:
                seen.append(req.role)
        return tuple(seen)

    @property
    def declared_component_kinds(self) -> tuple[str, ...]:
        seen: list[str] = []
        for profile in self.radial_profiles:
            if profile.component_kind not in seen:
                seen.append(profile.component_kind)
        return tuple(seen)


class GeometryInventoryCoverageReport(AgentBaseModel):
    """Roll-up of how completely the Inventory covers its inputs."""

    accepted_facts_covered: bool = False
    fuel_variants_covered: int = 0
    fuel_variants_total: int = 0
    localized_inserts_covered: int = 0
    localized_inserts_total: int = 0
    explicit_components_covered: int = 0
    axial_regions_covered: int = 0
    profiles_with_valid_source: int = 0
    profiles_missing_material_roles: int = 0
    profiles_missing_geometry: int = 0
    unresolved_source_critical_components: int = 0
    conflict_count: int = 0
    unsupported_implicit_component_count: int = 0
    inventory_complete_for_materials: bool = False
    inventory_complete_for_universes: bool = False


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def compile_geometry_component_inventory(
    *,
    accepted_facts: Any,
    evidence_ledger: PlanningEvidenceLedger,
    source_indexes: Mapping[str, SourceIndex] | None = None,
    confirmed_human_facts: Mapping[str, Any] | None = None,
    facts_accepted: bool = True,
) -> GeometryComponentInventory:
    """Deterministically compile the inventory from accepted Facts + ledger.

    Hard rule: ``facts_accepted=False`` raises
    :class:`PlanInvestigationIssue`.  No provisional inventory.
    """

    if not facts_accepted:
        raise PlanInvestigationIssue(
            INVENTORY_NOT_FACTS_ACCEPTED_CODE,
            "GeometryComponentInventory requires the Facts Gate to be accepted",
        )
    if accepted_facts is None:
        raise PlanInvestigationIssue(
            INVENTORY_NOT_FACTS_ACCEPTED_CODE,
            "compile_geometry_component_inventory requires accepted_facts",
        )

    # Hash inputs.
    from .hashing import content_hash

    requirement_hash = evidence_ledger.requirement_hash
    source_index_hash = (
        content_hash(sorted(source_indexes.keys())) if source_indexes else ""
    )
    ledger_hash = evidence_ledger.ledger_hash
    accepted_facts_patch_hash = _facts_patch_hash(accepted_facts)

    radial_profiles: list[RadialProfileRequirement] = []
    axial_regions: list[AxialRegionRequirement] = []
    localized_bindings: list[LocalizedInsertProfileBinding] = []
    material_role_reqs: list[MaterialRoleRequirement] = []
    unresolved: list[UnresolvedComponent] = []

    # A. Fuel variants → one active_fuel_pin profile + fuel material role each.
    fuel_variants = list(getattr(accepted_facts, "fuel_variant_requirements", []) or [])
    for variant in fuel_variants:
        variant_id = getattr(variant, "variant_id", "")
        profile_id = short_id(
            "profile",
            {"k": "active_fuel_pin", "v": variant_id},
        )
        _component_kind = ComponentKind.FUEL_PIN.value
        _sup_roles = _SUPPORTING_MATERIAL_ROLES.get(_component_kind, ())
        _sup_cells = _SUPPORTING_CELL_ROLES.get(_component_kind, ())
        radial_profiles.append(
            RadialProfileRequirement(
                profile_id=profile_id,
                profile_kind=ProfileKind.ACTIVE_FUEL_PIN.value,
                component_kind=_component_kind,
                fuel_variant_id=variant_id,
                required_cell_roles=("fuel",) + _sup_cells,
                required_material_roles=("fuel",) + _sup_roles,
                applicable_assembly_type_ids=tuple(
                    getattr(variant, "assembly_type_ids", []) or []
                ),
                status="resolved",
            )
        )
        material_role_reqs.append(
            MaterialRoleRequirement(
                requirement_id=short_id(
                    "mrole",
                    {"role": "fuel", "variant": variant_id},
                ),
                role="fuel",
                fuel_variant_id=variant_id,
                required_by_profile_ids=(profile_id,),
                status="required",
            )
        )
        # Add supporting material roles (cladding, coolant, gas) so the
        # MaterialsPatch includes them and universe cells can reference
        # valid material IDs.
        for srole in _sup_roles:
            material_role_reqs.append(
                MaterialRoleRequirement(
                    requirement_id=short_id(
                        "mrole",
                        {"role": srole, "variant": variant_id},
                    ),
                    role=srole,
                    fuel_variant_id=variant_id,
                    required_by_profile_ids=(profile_id,),
                    status="required",
                )
            )

    # B. Localized inserts → profile + host component + material role.
    for req in getattr(accepted_facts, "localized_insert_requirements", []) or []:
        req_id = getattr(req, "requirement_id", "")
        insert_kind = getattr(req, "insert_kind", "custom")
        _component_kind = _insert_kind_to_component(insert_kind)
        _primary_role = _insert_kind_to_material_role(insert_kind)
        _sup_roles = _SUPPORTING_MATERIAL_ROLES.get(_component_kind, ())
        _sup_cells = _SUPPORTING_CELL_ROLES.get(_component_kind, ())
        profile_id = short_id(
            "profile",
            {"k": _insert_kind_to_profile(insert_kind), "req": req_id},
        )
        _primary_cell = "absorber" if insert_kind == "control_rod" else ("poison" if insert_kind == "pyrex_rod" else "structural")
        radial_profiles.append(
            RadialProfileRequirement(
                profile_id=profile_id,
                profile_kind=_insert_kind_to_profile(insert_kind),
                component_kind=_component_kind,
                required_cell_roles=(_primary_cell,) + _sup_cells,
                required_material_roles=(_primary_role,) + _sup_roles,
                applicable_assembly_type_ids=tuple(getattr(req, "assembly_type_ids", []) or []),
                status="resolved",
            )
        )
        host_kind = "guide_tube"
        if getattr(req, "host_kind", "guide_tube") == "instrument_tube":
            host_kind = "instrument_tube"
        localized_bindings.append(
            LocalizedInsertProfileBinding(
                insert_requirement_id=req_id,
                insert_kind=insert_kind,
                profile_id=profile_id,
                host_component_kind=host_kind,
                material_role=_primary_role,
            )
        )
        # Each localized insert creates MaterialRoleRequirements for
        # its primary role plus supporting roles (cladding, coolant).
        for mrole in (_primary_role,) + _sup_roles:
            material_role_reqs.append(
                MaterialRoleRequirement(
                    requirement_id=short_id(
                        "mrole",
                        {"role": mrole, "insert": req_id},
                    ),
                    role=mrole,
                    localized_insert_requirement_id=req_id,
                    required_by_profile_ids=(profile_id,),
                    status="required",
                )
            )

    # C. Component evidence claims from the ledger → radial profiles /
    #    axial regions / material roles.  The LLM-driven synthesis path
    #    stores accepted claims with metadata.component_kind /
    #    metadata.profile_kind; we walk those here.
    for claim in evidence_ledger.claims.values():
        component_kind = claim.metadata.get("component_kind")
        profile_kind = claim.metadata.get("profile_kind")
        if claim.predicate == "geometry.profile_required" and component_kind:
            _maybe_add_profile_from_claim(
                claim, radial_profiles, material_role_reqs, unresolved
            )
        elif claim.predicate == "geometry.axial_region_present" and component_kind:
            axial_regions.append(
                AxialRegionRequirement(
                    region_id=short_id(
                        "region",
                        {
                            "k": claim.metadata.get("axial_region_kind") or component_kind,
                            "c": claim.claim_id,
                        },
                    ),
                    region_kind=claim.metadata.get("axial_region_kind") or component_kind,
                    host_component_kind=component_kind,
                    source_claim_ids=(claim.claim_id,),
                )
            )
        elif claim.predicate == "material.role_required":
            material_role_reqs.append(
                MaterialRoleRequirement(
                    requirement_id=short_id(
                        "mrole",
                        {"role": str(claim.value), "claim": claim.claim_id},
                    ),
                    role=str(claim.value),
                    source_claim_ids=(claim.claim_id,),
                    status="required",
                )
            )

    # D. De-duplicate radial profiles with identical (component_kind,
    #    profile_kind, required_cell_roles, fuel_variant_id).  Fuel
    #    variants NEVER dedupe (each has its own composition); structural
    #    components like end plugs share a profile when identical.
    seen_profile_keys: set[str] = set()
    deduped_profiles: list[RadialProfileRequirement] = []
    for profile in radial_profiles:
        key = content_hash(
            {
                "c": profile.component_kind,
                "p": profile.profile_kind,
                "cr": sorted(profile.required_cell_roles),
                "mr": sorted(profile.required_material_roles),
                "v": profile.fuel_variant_id or "",
            }
        )
        if key in seen_profile_keys:
            continue
        seen_profile_keys.add(key)
        deduped_profiles.append(profile)

    # E. Build the inventory.
    inventory = GeometryComponentInventory(
        inventory_id=short_id(
            "inv",
            {
                "rh": requirement_hash,
                "lh": ledger_hash,
                "fh": accepted_facts_patch_hash,
            },
        ),
        requirement_hash=requirement_hash,
        source_index_hash=source_index_hash,
        ledger_hash=ledger_hash,
        accepted_facts_patch_hash=accepted_facts_patch_hash,
        radial_profiles=tuple(deduped_profiles),
        axial_regions=tuple(axial_regions),
        localized_insert_profiles=tuple(localized_bindings),
        material_role_requirements=tuple(material_role_reqs),
        unresolved_components=tuple(unresolved),
    )
    return inventory


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _facts_patch_hash(facts: Any) -> str:
    if hasattr(facts, "model_dump"):
        return content_hash(facts.model_dump(mode="json"))
    if isinstance(facts, dict):
        return content_hash(facts)
    return content_hash(str(facts))


def _insert_kind_to_profile(insert_kind: str) -> str:
    return {
        "control_rod": ProfileKind.CONTROL_ROD.value,
        "absorber_insert": ProfileKind.CONTROL_ROD.value,
        "pyrex_rod": ProfileKind.POISON_ROD.value,
        "thimble_plug": ProfileKind.PLUG_IN_GUIDE_TUBE.value,
        "instrumentation_insert": ProfileKind.INSTRUMENT_TUBE.value,
    }.get(insert_kind, ProfileKind.CUSTOM.value)


def _insert_kind_to_component(insert_kind: str) -> str:
    return {
        "control_rod": ComponentKind.CONTROL_ROD.value,
        "absorber_insert": ComponentKind.ABSORBER_INSERT.value,
        "pyrex_rod": ComponentKind.PYREX_ROD.value,
        "thimble_plug": ComponentKind.THIMBLE_PLUG.value,
        "instrumentation_insert": ComponentKind.INSTRUMENT_TUBE.value,
    }.get(insert_kind, ComponentKind.CUSTOM.value)


def _insert_kind_to_material_role(insert_kind: str) -> str:
    return {
        "control_rod": "absorber",
        "absorber_insert": "absorber",
        "pyrex_rod": "poison",
        "thimble_plug": "structural",
        "instrumentation_insert": "structural",
    }.get(insert_kind, "structural")


def _maybe_add_profile_from_claim(
    claim: EvidenceClaim,
    profiles: list[RadialProfileRequirement],
    material_reqs: list[MaterialRoleRequirement],
    unresolved: list[UnresolvedComponent],
) -> None:
    component_kind = claim.metadata.get("component_kind", ComponentKind.CUSTOM.value)
    profile_kind = claim.metadata.get("profile_kind", ProfileKind.CUSTOM.value)
    if profile_kind not in PROFILE_KINDS:
        profile_kind = ProfileKind.CUSTOM.value
    if component_kind not in COMPONENT_KINDS:
        component_kind = ComponentKind.CUSTOM.value
    profile_id = short_id(
        "profile",
        {"k": profile_kind, "c": claim.claim_id},
    )
    material_roles = tuple(claim.metadata.get("material_roles", []) or [])
    cell_roles = tuple(claim.metadata.get("cell_roles", []) or [])
    profiles.append(
        RadialProfileRequirement(
            profile_id=profile_id,
            profile_kind=profile_kind,
            component_kind=component_kind,
            required_cell_roles=cell_roles,
            required_material_roles=material_roles,
            source_claim_ids=(claim.claim_id,),
            status="resolved" if material_roles else "unresolved",
        )
    )
    for role in material_roles:
        material_reqs.append(
            MaterialRoleRequirement(
                requirement_id=short_id(
                    "mrole",
                    {"role": role, "claim": claim.claim_id},
                ),
                role=role,
                required_by_profile_ids=(profile_id,),
                source_claim_ids=(claim.claim_id,),
                status="required",
            )
        )
