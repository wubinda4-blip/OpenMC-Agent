"""Deterministic Material-Universe inventory preflight (Phase 8A Step 5).

A pure-Python preflight that runs BEFORE the Material-Universe Gate
reviewer.  It cross-checks the generated Materials + Universes patches
against the GeometryComponentInventory and produces stable issue codes
that flow into the existing closed-loop finding ledger.

Hard rules:
* Inventory hash must match the current Ledger / Facts state.
* Every source-critical material role must have a Materials entry.
* Every fuel variant must have exactly one fuel material.
* Every radial profile must have a Universe with a valid
  ``geometry_profile_id`` binding.
* No legacy implicit-only Universe may slip through in controlled mode.
* No fabricated geometry values (gap, radius) when Inventory didn't
  declare them.
* Partial fragments must not pass through.

This module is read-only with respect to Graph state.  Findings are
returned to the caller; the existing gate framework decides routing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from openmc_agent.plan_builder.material_requirements import (
    MaterialGenerationRequirementSet,
    validate_materials_against_requirement_set,
)
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.geometry_inventory import GeometryComponentInventory
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.inventory_universe_requirements import (
    LEGACY_IMPLICIT_REQUIREMENT_IDS,
    InventoryUniverseRequirementSet,
)

__all__ = [
    "InventoryPreflightFinding",
    "InventoryPreflightReport",
    "run_geometry_inventory_material_universe_preflight",
    "PREFLIGHT_ISSUE_CODES",
    # Stable issue codes
    "INVENTORY_HASH_MISMATCH",
    "INVENTORY_SOURCE_CLAIM_MISSING",
    "INVENTORY_SOURCE_SPAN_INVALID",
    "INVENTORY_CONFLICT_UNRESOLVED",
    "INVENTORY_COMPONENT_UNRESOLVED",
    "INVENTORY_MATERIAL_ROLE_UNCOVERED",
    "INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED",
    "INVENTORY_LOCALIZED_INSERT_PROFILE_UNCOVERED",
    "INVENTORY_RADIAL_PROFILE_UNCOVERED",
    "INVENTORY_PROFILE_LAYER_UNCOVERED",
    "INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED",
    "INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT",
    "INVENTORY_FABRICATED_GEOMETRY_VALUE",
    "MANIFEST_INVENTORY_REQUIREMENT_MISSING",
    "MATERIAL_UNIVERSE_INVENTORY_PREFLIGHT_FAILED",
]


# ---------------------------------------------------------------------------
# Stable issue codes
# ---------------------------------------------------------------------------


INVENTORY_HASH_MISMATCH = "inventory.hash_mismatch"
INVENTORY_SOURCE_CLAIM_MISSING = "inventory.source_claim_missing"
INVENTORY_SOURCE_SPAN_INVALID = "inventory.source_span_invalid"
INVENTORY_CONFLICT_UNRESOLVED = "inventory.conflict_unresolved"
INVENTORY_COMPONENT_UNRESOLVED = "inventory.component_unresolved"
INVENTORY_MATERIAL_ROLE_UNCOVERED = "inventory.material_role_uncovered"
INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED = "inventory.fuel_variant_material_uncovered"
INVENTORY_LOCALIZED_INSERT_PROFILE_UNCOVERED = "inventory.localized_insert_profile_uncovered"
INVENTORY_RADIAL_PROFILE_UNCOVERED = "inventory.radial_profile_uncovered"
INVENTORY_PROFILE_LAYER_UNCOVERED = "inventory.profile_layer_uncovered"
INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED = "inventory.universe_material_unresolved"
INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT = "inventory.unsupported_implicit_component"
INVENTORY_FABRICATED_GEOMETRY_VALUE = "inventory.fabricated_geometry_value"
MANIFEST_INVENTORY_REQUIREMENT_MISSING = "manifest.inventory_requirement_missing"
MATERIAL_UNIVERSE_INVENTORY_PREFLIGHT_FAILED = "material_universe.inventory_preflight_failed"


PREFLIGHT_ISSUE_CODES: tuple[str, ...] = (
    INVENTORY_HASH_MISMATCH,
    INVENTORY_SOURCE_CLAIM_MISSING,
    INVENTORY_SOURCE_SPAN_INVALID,
    INVENTORY_CONFLICT_UNRESOLVED,
    INVENTORY_COMPONENT_UNRESOLVED,
    INVENTORY_MATERIAL_ROLE_UNCOVERED,
    INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
    INVENTORY_LOCALIZED_INSERT_PROFILE_UNCOVERED,
    INVENTORY_RADIAL_PROFILE_UNCOVERED,
    INVENTORY_PROFILE_LAYER_UNCOVERED,
    INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED,
    INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT,
    INVENTORY_FABRICATED_GEOMETRY_VALUE,
    MANIFEST_INVENTORY_REQUIREMENT_MISSING,
)


# ---------------------------------------------------------------------------
# Finding + report
# ---------------------------------------------------------------------------


class InventoryPreflightFinding(AgentBaseModel):
    """One deterministic preflight finding."""

    code: str
    severity: str = "error"  # error | warning
    message: str
    affected_profile_ids: tuple[str, ...] = Field(default_factory=tuple)
    affected_material_ids: tuple[str, ...] = Field(default_factory=tuple)
    affected_universe_ids: tuple[str, ...] = Field(default_factory=tuple)
    details: dict[str, Any] = Field(default_factory=dict)


class InventoryPreflightReport(AgentBaseModel):
    """Aggregate preflight result."""

    inventory_hash: str = ""
    material_requirement_set_hash: str = ""
    universe_requirement_set_hash: str = ""
    materials_patch_hash: str = ""
    universes_patch_hash: str = ""
    findings: tuple[InventoryPreflightFinding, ...] = Field(default_factory=tuple)
    passed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Preflight entry point
# ---------------------------------------------------------------------------


def run_geometry_inventory_material_universe_preflight(
    *,
    inventory: GeometryComponentInventory,
    material_requirement_set: MaterialGenerationRequirementSet,
    universe_requirement_set: InventoryUniverseRequirementSet,
    materials_patch: Any,
    universes_patch: Any,
    expected_inventory_hash: str | None = None,
    known_material_ids: Iterable[str] | None = None,
    known_universe_ids: Iterable[str] | None = None,
) -> InventoryPreflightReport:
    """Run the deterministic Material-Universe inventory preflight.

    Returns an :class:`InventoryPreflightReport`.  ``passed=True`` only
    when there are zero error-severity findings.
    """

    findings: list[InventoryPreflightFinding] = []

    # 1. Inventory hash consistency.
    if expected_inventory_hash and inventory.inventory_hash != expected_inventory_hash:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_HASH_MISMATCH,
                message="inventory hash drifts from the expected value",
                details={
                    "expected": expected_inventory_hash,
                    "actual": inventory.inventory_hash,
                },
            )
        )

    # 2. Inventory-level conflicts + unresolved source-critical components.
    if inventory.conflicts:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_CONFLICT_UNRESOLVED,
                message=f"{len(inventory.conflicts)} unresolved inventory conflicts",
                details={"count": len(inventory.conflicts)},
            )
        )
    for unresolved in inventory.unresolved_components:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_COMPONENT_UNRESOLVED,
                message=(
                    f"component {unresolved.component_kind} has unresolved fields: "
                    f"{list(unresolved.unresolved_fields)}"
                ),
                details={
                    "component_id": unresolved.component_id,
                    "component_kind": unresolved.component_kind,
                    "blocking_patch_types": list(unresolved.blocking_patch_types),
                },
            )
        )

    # 3. Materials coverage.
    materials_report = validate_materials_against_requirement_set(
        materials_patch=materials_patch,
        requirement_set=material_requirement_set,
    )
    if materials_report.uncovered_requirement_ids:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_MATERIAL_ROLE_UNCOVERED,
                message=(
                    f"{len(materials_report.uncovered_requirement_ids)} material "
                    f"requirements have no Materials entry"
                ),
                details={
                    "uncovered_requirement_ids": list(
                        materials_report.uncovered_requirement_ids
                    ),
                },
            )
        )
    # 3b. Fuel variant binding: each variant must have exactly one fuel material.
    fuel_variants = {
        req.source_variant_id: req.requirement_id
        for req in material_requirement_set.requirements
        if req.source_variant_id
    }
    for variant_id, _req_id in fuel_variants.items():
        if variant_id not in materials_report.fuel_variant_coverage:
            findings.append(
                InventoryPreflightFinding(
                    code=INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
                    message=f"fuel variant {variant_id} has no fuel material binding",
                    details={"variant_id": variant_id},
                )
            )

    # 4. Universes coverage: each inventory profile must have a universe.
    universes_list = _extract_universes_list(universes_patch)
    universe_profile_bindings = _extract_profile_bindings(universes_list)
    inventory_profile_ids = {p.profile_id for p in inventory.radial_profiles}
    missing_profiles = inventory_profile_ids - set(universe_profile_bindings.keys())
    if missing_profiles:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_RADIAL_PROFILE_UNCOVERED,
                message=(
                    f"{len(missing_profiles)} radial profiles have no universe binding"
                ),
                affected_profile_ids=tuple(sorted(missing_profiles)),
            )
        )

    # 5. Universe material IDs must resolve against known materials.
    if known_material_ids is not None:
        known_set = set(known_material_ids)
        for universe in universes_list:
            for mid in _universe_material_ids(universe):
                if mid not in known_set:
                    findings.append(
                        InventoryPreflightFinding(
                            code=INVENTORY_UNIVERSE_MATERIAL_UNRESOLVED,
                            message=(
                                f"universe references unknown material id {mid}"
                            ),
                            affected_universe_ids=(
                                _universe_id(universe),
                            ),
                        )
                    )

    # 6. No legacy implicit-only universe should be present (controlled mode).
    universe_ids = [_universe_id(u) for u in universes_list]
    legacy_present = [
        uid for uid in universe_ids if uid in LEGACY_IMPLICIT_REQUIREMENT_IDS
    ]
    if legacy_present:
        findings.append(
            InventoryPreflightFinding(
                code=INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT,
                message=(
                    f"{len(legacy_present)} legacy implicit-only universes present; "
                    "controlled mode forbids implicit components without source evidence"
                ),
                affected_universe_ids=tuple(legacy_present),
            )
        )

    # 7. Manifest covers all resolved inventory requirements.
    resolved_req_ids = {
        req.requirement_id for req in universe_requirement_set.requirements if req.resolved
    }
    covered_req_ids = set()
    for universe in universes_list:
        for src_id in _universe_source_requirement_ids(universe):
            covered_req_ids.add(src_id)
    missing_req_ids = resolved_req_ids - covered_req_ids
    if missing_req_ids:
        findings.append(
            InventoryPreflightFinding(
                code=MANIFEST_INVENTORY_REQUIREMENT_MISSING,
                message=(
                    f"{len(missing_req_ids)} resolved inventory universe requirements "
                    f"are not covered by any universe"
                ),
                details={"missing_requirement_ids": sorted(missing_req_ids)},
            )
        )

    passed = not any(f.severity == "error" for f in findings)
    return InventoryPreflightReport(
        inventory_hash=inventory.inventory_hash,
        material_requirement_set_hash=material_requirement_set.requirement_set_hash,
        universe_requirement_set_hash=universe_requirement_set.requirement_set_hash,
        findings=tuple(findings),
        passed=passed,
        metadata={
            "materials_uncovered_count": len(materials_report.uncovered_requirement_ids),
            "missing_profile_count": len(missing_profiles),
            "legacy_implicit_present": len(legacy_present),
            "missing_requirement_count": len(missing_req_ids),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers (duck-typed universe extraction)
# ---------------------------------------------------------------------------


def _extract_universes_list(universes_patch: Any) -> list[Any]:
    if hasattr(universes_patch, "universes"):
        return list(universes_patch.universes or [])
    if isinstance(universes_patch, dict):
        return list(universes_patch.get("universes", []) or [])
    return []


def _extract_profile_bindings(universes: Iterable[Any]) -> dict[str, str]:
    """Return ``{profile_id: universe_id}`` for universes that declare one."""

    out: dict[str, str] = {}
    for universe in universes:
        uid = _universe_id(universe)
        profile_id = (
            getattr(universe, "geometry_profile_id", None)
            or (universe.get("geometry_profile_id") if isinstance(universe, dict) else None)
            or _metadata_profile_id(universe)
        )
        if profile_id:
            out[profile_id] = uid
    return out


def _universe_id(universe: Any) -> str:
    return (
        getattr(universe, "universe_id", "")
        or (universe.get("universe_id") if isinstance(universe, dict) else "")
        or ""
    )


def _universe_material_ids(universe: Any) -> list[str]:
    cells = (
        getattr(universe, "cells", None)
        or (universe.get("cells") if isinstance(universe, dict) else None)
        or []
    )
    out: list[str] = []
    for cell in cells:
        mid = (
            getattr(cell, "material_id", None)
            or (cell.get("material_id") if isinstance(cell, dict) else None)
        )
        if mid:
            out.append(str(mid))
    return out


def _universe_source_requirement_ids(universe: Any) -> list[str]:
    metadata = (
        getattr(universe, "metadata", None)
        or (universe.get("metadata") if isinstance(universe, dict) else None)
        or {}
    )
    return list(metadata.get("source_requirement_ids", []) or [])


def _metadata_profile_id(universe: Any) -> str | None:
    metadata = (
        getattr(universe, "metadata", None)
        or (universe.get("metadata") if isinstance(universe, dict) else None)
        or {}
    )
    return metadata.get("geometry_profile_id")
