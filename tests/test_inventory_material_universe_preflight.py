"""Tests for the Material-Universe inventory preflight."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.material_requirements import (
    MaterialGenerationRequirement,
    MaterialGenerationRequirementSet,
)
from openmc_agent.plan_investigation.geometry_inventory import (
    GeometryComponentInventory,
    MaterialRoleRequirement,
    RadialProfileRequirement,
)
from openmc_agent.plan_investigation.inventory_preflight import (
    INVENTORY_FABRICATED_GEOMETRY_VALUE,
    INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED,
    INVENTORY_HASH_MISMATCH,
    INVENTORY_MATERIAL_ROLE_UNCOVERED,
    INVENTORY_RADIAL_PROFILE_UNCOVERED,
    INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT,
    MANIFEST_INVENTORY_REQUIREMENT_MISSING,
    MATERIAL_UNIVERSE_INVENTORY_PREFLIGHT_FAILED,
    PREFLIGHT_ISSUE_CODES,
    run_geometry_inventory_material_universe_preflight,
)
from openmc_agent.plan_investigation.inventory_universe_requirements import (
    extract_universe_requirements_from_inventory,
)


class _Mat:
    def __init__(self, mid, role):
        self.material_id = mid
        self.role = role


class _MaterialsPatch:
    def __init__(self, materials):
        self.materials = materials


class _Cell:
    def __init__(self, material_id):
        self.material_id = material_id


class _Universe:
    def __init__(self, uid, cells=None, profile_id=None, source_req_ids=None):
        self.universe_id = uid
        self.cells = cells or []
        self.metadata = {
            "geometry_profile_id": profile_id,
            "source_requirement_ids": source_req_ids or [],
        }


class _UniversesPatch:
    def __init__(self, universes):
        self.universes = universes


def _inventory_with_fuel_profile(variant_id="v1"):
    profile = RadialProfileRequirement(
        profile_id=f"prof_fuel_{variant_id}",
        profile_kind="active_fuel_pin",
        component_kind="fuel_pin",
        fuel_variant_id=variant_id,
        required_cell_roles=("fuel",),
        required_material_roles=("fuel",),
    )
    role = MaterialRoleRequirement(
        requirement_id=f"mrole_fuel_{variant_id}",
        role="fuel",
        fuel_variant_id=variant_id,
        required_by_profile_ids=(profile.profile_id,),
    )
    return GeometryComponentInventory(
        inventory_id="inv_t",
        requirement_hash="rh",
        source_index_hash="sih",
        ledger_hash="lh",
        radial_profiles=(profile,),
        material_role_requirements=(role,),
    )


def _full_setup(variant_id="v1", *, include_material=True, include_universe=True):
    inv = _inventory_with_fuel_profile(variant_id)
    mreq_set = MaterialGenerationRequirementSet(
        requirements=(
            MaterialGenerationRequirement(
                requirement_id="mreq_fuel",
                role="fuel",
                source_variant_id=variant_id,
                required_by_profile_ids=(f"prof_fuel_{variant_id}",),
            ),
        ),
    )
    ureq_set = extract_universe_requirements_from_inventory(inv)
    materials_patch = _MaterialsPatch(
        [_Mat(f"m_fuel_{variant_id}", "fuel")] if include_material else []
    )
    universes_patch = _UniversesPatch(
        [_Universe(
            uid=f"u_fuel_{variant_id}",
            cells=[_Cell(f"m_fuel_{variant_id}")],
            profile_id=f"prof_fuel_{variant_id}",
            source_req_ids=[ureq_set.requirements[0].requirement_id] if ureq_set.requirements else [],
        )] if include_universe else []
    )
    return inv, mreq_set, ureq_set, materials_patch, universes_patch


def test_preflight_passes_when_everything_covered() -> None:
    inv, mreq, ureq, mats, unis = _full_setup()
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=unis,
    )
    assert report.passed
    assert report.error_count == 0


def test_preflight_fails_when_material_missing() -> None:
    inv, mreq, ureq, _, unis = _full_setup(include_material=False)
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=_MaterialsPatch([]),
        universes_patch=unis,
    )
    assert not report.passed
    codes = [f.code for f in report.findings]
    assert INVENTORY_MATERIAL_ROLE_UNCOVERED in codes


def test_preflight_fails_when_fuel_variant_uncovered() -> None:
    inv, mreq, ureq, _, unis = _full_setup(include_material=False)
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=_MaterialsPatch([]),
        universes_patch=unis,
    )
    codes = [f.code for f in report.findings]
    assert INVENTORY_FUEL_VARIANT_MATERIAL_UNCOVERED in codes


def test_preflight_fails_when_universe_missing() -> None:
    inv, mreq, ureq, mats, _ = _full_setup(include_universe=False)
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=_UniversesPatch([]),
    )
    codes = [f.code for f in report.findings]
    assert INVENTORY_RADIAL_PROFILE_UNCOVERED in codes


def test_preflight_fails_on_inventory_hash_mismatch() -> None:
    inv, mreq, ureq, mats, unis = _full_setup()
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=unis,
        expected_inventory_hash="stale_hash",
    )
    codes = [f.code for f in report.findings]
    assert INVENTORY_HASH_MISMATCH in codes


def test_preflight_flags_legacy_implicit_universe() -> None:
    """A universe with id implicit:end_plug_lower is flagged."""
    inv, mreq, ureq, mats, _ = _full_setup()
    legacy_universe = _Universe(
        uid="implicit:end_plug_lower",
        cells=[_Cell("m_struct")],
        profile_id=None,
    )
    # Mix the implicit universe with the valid fuel universe.
    valid_universe = _UniversesPatch(
        [_Universe(
            uid="u_fuel_v1",
            cells=[_Cell("m_fuel_v1")],
            profile_id="prof_fuel_v1",
            source_req_ids=[ureq.requirements[0].requirement_id],
        ), legacy_universe]
    )
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=valid_universe,
    )
    codes = [f.code for f in report.findings]
    assert INVENTORY_UNSUPPORTED_IMPLICIT_COMPONENT in codes


def test_preflight_flags_missing_manifest_coverage() -> None:
    """A universe that doesn't carry source_requirement_ids fails manifest check."""
    inv, mreq, ureq, mats, _ = _full_setup()
    # Universe exists but does NOT declare source_requirement_ids.
    bad_universe = _UniversesPatch(
        [_Universe(
            uid="u_fuel_v1",
            cells=[_Cell("m_fuel_v1")],
            profile_id="prof_fuel_v1",
            source_req_ids=[],  # missing manifest coverage
        )]
    )
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=bad_universe,
    )
    codes = [f.code for f in report.findings]
    assert MANIFEST_INVENTORY_REQUIREMENT_MISSING in codes


def test_preflight_issue_codes_set_is_stable() -> None:
    for code in PREFLIGHT_ISSUE_CODES:
        assert isinstance(code, str)
        assert code.startswith("inventory.") or code.startswith("manifest.") or code.startswith("material_universe.")


def test_preflight_universe_material_must_be_known() -> None:
    inv, mreq, ureq, mats, _ = _full_setup()
    # Universe references an unknown material id.
    bad_universe = _UniversesPatch(
        [_Universe(
            uid="u_fuel_v1",
            cells=[_Cell("m_unknown")],
            profile_id="prof_fuel_v1",
            source_req_ids=[ureq.requirements[0].requirement_id],
        )]
    )
    report = run_geometry_inventory_material_universe_preflight(
        inventory=inv,
        material_requirement_set=mreq,
        universe_requirement_set=ureq,
        materials_patch=mats,
        universes_patch=bad_universe,
        known_material_ids={"m_fuel_v1"},  # m_unknown not in set
    )
    codes = [f.code for f in report.findings]
    assert any("universe_material_unresolved" in c for c in codes)
