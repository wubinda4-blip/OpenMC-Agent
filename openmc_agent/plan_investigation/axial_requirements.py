"""Phase 8A Step 6C — AxialGeometryRequirementSet (Section 22).

Defines the typed axial-geometry contract compiled from accepted
Facts + GeometryComponentInventory + Universes + Placement.

Hard rules (Section 22-23):

* Axial domain must have source or derived evidence.
* Region intervals must be legal (no overlap, no unexpected gap).
* Every replacement_profile must exist.
* Every required Universe must exist.
* Through-path must be continuous.
* Localized-insert profile coverage is required.
* Homogenization method MUST have a source or human confirmation.
* Mixture fractions MUST have a source.
* Never auto-50/50.
* Never auto-split by lattice cell count.
* Never derive fixed structure from ``has_axial_geometry``.
* Never invent gap / radius / z boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .hashing import content_hash, short_id

__all__ = [
    "AxialRegionContract",
    "AxialOverlayContract",
    "ThroughPathContract",
    "AxialGeometryRequirementSet",
    "AXIAL_REQUIREMENT_SCHEMA_VERSION",
]


AXIAL_REQUIREMENT_SCHEMA_VERSION = "1.0"


class AxialRegionContract(AgentBaseModel):
    """One axial region (fuel, plenum, gas gap, etc.)."""

    region_id: str = ""
    region_kind: str
    z_min_cm: float | None = None
    z_max_cm: float | None = None
    host_component_kind: str = ""
    replacement_profile_id: str = ""
    required_universe_id: str = ""
    applicable_assembly_type_ids: tuple[str, ...] = Field(default_factory=tuple)
    continues_through_path: bool = False
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)


class AxialOverlayContract(AgentBaseModel):
    """One axial overlay (spacer grid homogenization, etc.)."""

    overlay_id: str = ""
    overlay_kind: str
    z_min_cm: float | None = None
    z_max_cm: float | None = None
    target_profile_ids: tuple[str, ...] = Field(default_factory=tuple)
    material_role: str = ""
    geometry_mode: str = ""
    homogenization_method: str = ""
    mixture_fractions: dict[str, float] = Field(default_factory=dict)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    requires_human_confirmation: bool = False


class ThroughPathContract(AgentBaseModel):
    """One through-path (e.g. coolant channel crossing all axial regions)."""

    path_role: str
    base_profile_id: str
    regions_crossed: tuple[str, ...] = Field(default_factory=tuple)
    protected_cell_roles: tuple[str, ...] = Field(default_factory=tuple)
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)


class AxialGeometryRequirementSet(AgentBaseModel):
    """The complete axial-geometry requirement set for one incremental run."""

    requirement_set_version: str = AXIAL_REQUIREMENT_SCHEMA_VERSION
    requirement_set_id: str = ""
    requirement_hash: str = ""
    ledger_hash: str = ""
    inventory_hash: str = ""
    facts_patch_hash: str = ""
    axial_domain: tuple[float, float] | None = None
    axial_regions: tuple[AxialRegionContract, ...] = Field(default_factory=tuple)
    replacement_bindings: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    localized_insert_profiles: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    overlay_requirements: tuple[AxialOverlayContract, ...] = Field(default_factory=tuple)
    through_path_requirements: tuple[ThroughPathContract, ...] = Field(default_factory=tuple)
    homogenization_requirements: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    unresolved_requirements: tuple[str, ...] = Field(default_factory=tuple)
    conflicts: tuple[str, ...] = Field(default_factory=tuple)
    requirement_set_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "AxialGeometryRequirementSet":
        body = {
            "axial_domain": list(self.axial_domain) if self.axial_domain else [],
            "regions": [r.model_dump(mode="json") for r in self.axial_regions],
            "overlays": [o.model_dump(mode="json") for o in self.overlay_requirements],
            "through_paths": [t.model_dump(mode="json") for t in self.through_path_requirements],
            "ledger_hash": self.ledger_hash,
            "inventory_hash": self.inventory_hash,
            "facts_patch_hash": self.facts_patch_hash,
        }
        h = content_hash(body)
        object.__setattr__(self, "requirement_set_hash", h)
        object.__setattr__(self, "requirement_hash", h)
        if not self.requirement_set_id:
            object.__setattr__(self, "requirement_set_id", short_id("axial_req", h))
        return self


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def extract_axial_geometry_requirements(
    *,
    accepted_facts: Any,
    geometry_inventory: Any = None,
    accepted_universes_patch: Any = None,
    accepted_placement_requirement_set: Any = None,
    ledger_hash: str = "",
    inventory_hash: str = "",
    facts_patch_hash: str = "",
) -> AxialGeometryRequirementSet:
    """Compile the AxialGeometryRequirementSet from accepted inputs.

    Reads axial domain / regions / overlays from accepted Facts +
    GeometryComponentInventory.  Never invents extents, gaps, or
    homogenization methods.
    """

    regions: list[AxialRegionContract] = []
    overlays: list[AxialOverlayContract] = []
    through_paths: list[ThroughPathContract] = []
    unresolved: list[str] = []
    # Axial domain from Facts (active_fuel_region_cm or axial_domain_cm).
    axial_domain = None
    active_region = getattr(accepted_facts, "active_fuel_region_cm", None)
    if active_region and len(active_region) == 2:
        try:
            axial_domain = (float(active_region[0]), float(active_region[1]))
        except Exception:
            axial_domain = None
    # Axial regions from inventory (if present).
    if geometry_inventory is not None:
        for inv_region in getattr(geometry_inventory, "axial_regions", []) or []:
            region_kind = getattr(inv_region, "region_kind", "") or getattr(inv_region, "kind", "")
            z_min = getattr(inv_region, "z_min_cm", None)
            z_max = getattr(inv_region, "z_max_cm", None)
            host_kind = getattr(inv_region, "host_component_kind", "")
            replacement_profile_id = getattr(inv_region, "replacement_profile_id", "") or ""
            required_universe_id = getattr(inv_region, "required_universe_id", "") or ""
            regions.append(AxialRegionContract(
                region_kind=region_kind,
                z_min_cm=z_min,
                z_max_cm=z_max,
                host_component_kind=host_kind,
                replacement_profile_id=replacement_profile_id,
                required_universe_id=required_universe_id,
                source_claim_ids=tuple(getattr(inv_region, "source_claim_ids", []) or ()),
                unresolved_fields=tuple(getattr(inv_region, "unresolved_fields", []) or ()),
            ))
            if not replacement_profile_id:
                unresolved.append(f"axial_region:{region_kind}:replacement_profile_id")
    # Spacer grid / overlay contracts from Facts.
    spacer_count = getattr(accepted_facts, "expected_spacer_grid_count", None)
    if spacer_count:
        # We declare the need for ``spacer_count`` overlays but do NOT
        # invent z extents or mixture fractions.
        for i in range(int(spacer_count)):
            overlays.append(AxialOverlayContract(
                overlay_kind="spacer_grid",
                overlay_id=f"spacer_grid_{i+1:02d}",
                material_role="structural",
                requires_human_confirmation=True,  # homogenization method needs source/human
            ))
        unresolved.append(f"axial_overlay:spacer_grid:homogenization_method (count={spacer_count})")
    return AxialGeometryRequirementSet(
        axial_domain=axial_domain,
        axial_regions=tuple(regions),
        overlay_requirements=tuple(overlays),
        through_path_requirements=tuple(through_paths),
        unresolved_requirements=tuple(unresolved),
        ledger_hash=ledger_hash,
        inventory_hash=inventory_hash,
        facts_patch_hash=facts_patch_hash,
    )
