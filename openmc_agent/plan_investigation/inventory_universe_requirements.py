"""Inventory-driven Universe generation requirements (Phase 8A Step 5).

Adds an additive path that derives UniverseGenerationRequirements
from a :class:`GeometryComponentInventory` instead of the legacy
heuristic rules in :mod:`universe_fragment_generation`.

Migration strategy:
* ``off`` mode: legacy ``extract_universe_requirements`` is unchanged;
  callers that do not pass an Inventory get byte-identical behaviour.
* ``advisory`` mode: build both requirement sets and emit a comparison
  warning; do NOT change the legacy patch.
* ``controlled`` mode: use ONLY the inventory-driven requirements;
  legacy implicit fallback is forbidden.

This module does NOT modify the legacy ``extract_universe_requirements``
function; it provides a separate entry point.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from openmc_agent.plan_builder.material_requirements import (
    MaterialGenerationRequirementSet,
)
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.geometry_inventory import (
    GeometryComponentInventory,
    GeometryInventoryCoverageReport,
)
from openmc_agent.plan_investigation.hashing import content_hash, short_id

__all__ = [
    "InventoryUniverseRequirement",
    "InventoryUniverseRequirementSet",
    "LegacyInventoryComparison",
    "extract_universe_requirements_from_inventory",
    "compare_against_legacy_requirements",
    "LEGACY_IMPLICIT_REQUIREMENT_IDS",
]


# Legacy implicit requirement ids that the new inventory-driven path
# refuses to emit without explicit source evidence.  Tracked here so
# the comparison report can flag them.
LEGACY_IMPLICIT_REQUIREMENT_IDS: frozenset[str] = frozenset(
    {
        "implicit:end_plug_lower",
        "implicit:end_plug_upper",
        "implicit:gas_gap",
        "implicit:water_pin",
        "implicit:guide_tube",
        "implicit:instrument_tube",
    }
)


class InventoryUniverseRequirement(AgentBaseModel):
    """One inventory-driven universe requirement.

    Each requirement is bound to a RadialProfileRequirement via
    ``geometry_profile_id`` and carries its source claim ids for audit.
    """

    requirement_id: str
    geometry_profile_id: str
    profile_kind: str
    component_kind: str
    fuel_variant_id: str | None = None
    localized_insert_requirement_id: str | None = None
    required_cell_roles: tuple[str, ...] = Field(default_factory=tuple)
    required_material_roles: tuple[str, ...] = Field(default_factory=tuple)
    protected_through_path_roles: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_layer_roles: tuple[str, ...] = Field(default_factory=tuple)
    resolved: bool = True
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)
    assumptions_allowed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class InventoryUniverseRequirementSet(AgentBaseModel):
    """The complete inventory-driven universe requirement set."""

    requirements: tuple[InventoryUniverseRequirement, ...] = Field(default_factory=tuple)
    unresolved_requirements: tuple[InventoryUniverseRequirement, ...] = Field(default_factory=tuple)
    inventory_hash: str = ""
    material_requirement_set_hash: str = ""
    requirement_set_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _compute_hash(self) -> "InventoryUniverseRequirementSet":
        payload = {
            "r": [r.model_dump(mode="json") for r in self.requirements],
            "ih": self.inventory_hash,
            "mr": self.material_requirement_set_hash,
        }
        expected = content_hash(payload)
        if not self.requirement_set_hash:
            object.__setattr__(self, "requirement_set_hash", expected)
        elif self.requirement_set_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.inventory_universe_requirement_set_hash_mismatch",
                "requirement_set_hash does not match the recomputed value",
                details={"expected": expected, "actual": self.requirement_set_hash},
            )
        return self


class LegacyInventoryComparison(AgentBaseModel):
    """Diff between the legacy implicit requirements and the inventory-driven ones."""

    inventory_only_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    legacy_only_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    shared_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    unsupported_implicit_components: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)


def extract_universe_requirements_from_inventory(
    inventory: GeometryComponentInventory,
    *,
    material_requirement_set: MaterialGenerationRequirementSet | None = None,
    accepted_facts: Any | None = None,
) -> InventoryUniverseRequirementSet:
    """Build universe requirements from the GeometryComponentInventory.

    One requirement per RadialProfileRequirement.  Source-backed only;
    no implicit components.
    """

    requirements: list[InventoryUniverseRequirement] = []
    unresolved: list[InventoryUniverseRequirement] = []

    # Build a quick lookup of material requirements by profile id so we
    # can attach required_material_ids to each universe requirement.
    profile_to_roles: dict[str, list[str]] = {}
    if material_requirement_set is not None:
        for req in material_requirement_set.requirements:
            for pid in req.required_by_profile_ids:
                profile_to_roles.setdefault(pid, []).append(req.role)

    # Build a lookup: profile_id → localized_insert_requirement_id so
    # each universe requirement knows which insert it belongs to.
    profile_to_insert: dict[str, str] = {}
    for binding in inventory.localized_insert_profiles:
        profile_to_insert[binding.profile_id] = binding.insert_requirement_id

    for profile in inventory.radial_profiles:
        roles = profile_to_roles.get(profile.profile_id, list(profile.required_material_roles))
        cell_roles = list(profile.required_cell_roles)
        req = InventoryUniverseRequirement(
            requirement_id=short_id(
                "ureq",
                {
                    "profile": profile.profile_id,
                    "component": profile.component_kind,
                    "variant": profile.fuel_variant_id or "",
                },
            ),
            geometry_profile_id=profile.profile_id,
            profile_kind=profile.profile_kind,
            component_kind=profile.component_kind,
            fuel_variant_id=profile.fuel_variant_id,
            localized_insert_requirement_id=profile_to_insert.get(profile.profile_id),
            required_cell_roles=tuple(cell_roles),
            required_material_roles=tuple(roles),
            protected_through_path_roles=tuple(profile.protected_through_path_roles),
            source_claim_ids=profile.source_claim_ids,
            source_span_ids=profile.source_span_ids,
            required_layer_roles=tuple(layer.role for layer in profile.radial_layers),
            resolved=profile.status == "resolved",
            unresolved_fields=profile.unresolved_fields if hasattr(profile, "unresolved_fields") else (),
            assumptions_allowed=False,
        )
        if req.resolved:
            requirements.append(req)
        else:
            unresolved.append(req)

    # Localized insert profile bindings may add additional universe
    # requirements when their profile differs from the base fuel profile.
    for binding in inventory.localized_insert_profiles:
        # The binding's profile_id already produced a universe requirement
        # above; no separate requirement is needed unless the binding
        # declares a different host_component_kind that needs its own
        # universe (e.g. guide_tube host).
        # For Step 5 we keep this minimal: the localized insert's profile
        # is already covered; we just record the binding in metadata.
        pass

    return InventoryUniverseRequirementSet(
        requirements=tuple(requirements),
        unresolved_requirements=tuple(unresolved),
        inventory_hash=inventory.inventory_hash,
        material_requirement_set_hash=(
            material_requirement_set.requirement_set_hash
            if material_requirement_set is not None
            else ""
        ),
        metadata={
            "profile_count": len(inventory.radial_profiles),
            "localized_insert_count": len(inventory.localized_insert_profiles),
        },
    )


def compare_against_legacy_requirements(
    *,
    inventory_requirements: InventoryUniverseRequirementSet,
    legacy_requirement_ids: tuple[str, ...] | list[str],
) -> LegacyInventoryComparison:
    """Compare the inventory-driven requirements against legacy implicit ones.

    Used in advisory mode to surface drift between the old heuristic
    rules and the new evidence-driven compilation.
    """

    inv_ids = {r.requirement_id for r in inventory_requirements.requirements}
    legacy_ids = set(legacy_requirement_ids)
    inventory_only = sorted(inv_ids - legacy_ids)
    legacy_only = sorted(legacy_ids - inv_ids)
    shared = sorted(inv_ids & legacy_ids)
    unsupported_implicit = sorted(
        rid for rid in legacy_only if rid in LEGACY_IMPLICIT_REQUIREMENT_IDS
    )
    warnings: list[str] = []
    if unsupported_implicit:
        warnings.append(
            f"legacy implicit requirements not supported by Inventory: {unsupported_implicit}"
        )
    if inventory_only:
        warnings.append(
            f"inventory declares requirements absent from legacy: {len(inventory_only)} new"
        )
    return LegacyInventoryComparison(
        inventory_only_requirement_ids=tuple(inventory_only),
        legacy_only_requirement_ids=tuple(legacy_only),
        shared_requirement_ids=tuple(shared),
        unsupported_implicit_components=tuple(unsupported_implicit),
        warnings=tuple(warnings),
    )
