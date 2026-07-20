from __future__ import annotations

from types import SimpleNamespace

from openmc_agent.plan_builder.requirement_skeletons import (
    RequirementResolution,
    compile_material_requirement_skeleton,
    compile_universe_requirement_skeleton,
)


def _inventory():
    return SimpleNamespace(
        inventory_hash="inv-hash",
        material_role_requirements=(SimpleNamespace(
            requirement_id="mreq-fuel-v1", role="fuel", fuel_variant_id="v1",
            required_by_profile_ids=("profile-v1",), source_claim_ids=("claim-1",),
            status="required",
        ), SimpleNamespace(
            requirement_id="mreq-absorber", role="absorber", fuel_variant_id=None,
            required_by_profile_ids=("profile-insert",), source_claim_ids=(),
            status="required", localized_insert_requirement_id="insert-1",
        )),
        radial_profiles=(SimpleNamespace(
            profile_id="profile-v1", profile_kind="active_fuel_pin", component_kind="fuel_pin",
            fuel_variant_id="v1", required_cell_roles=("fuel",), required_material_roles=("fuel",),
            protected_through_path_roles=(), source_claim_ids=("claim-1",), source_span_ids=(), status="resolved",
        ), SimpleNamespace(
            profile_id="profile-insert", profile_kind="control_rod", component_kind="control_rod",
            fuel_variant_id=None, required_cell_roles=("absorber",), required_material_roles=("absorber",),
            protected_through_path_roles=("coolant",), source_claim_ids=(), source_span_ids=(), status="resolved",
        )),
        localized_insert_profiles=(SimpleNamespace(insert_requirement_id="insert-1", profile_id="profile-insert"),),
    )


def test_material_skeleton_source_derives_role_and_variant():
    result = compile_material_requirement_skeleton(
        inventory=_inventory(), accepted_facts=SimpleNamespace(fuel_variant_requirements=()),
    )
    fuel = next(item for item in result.requirements if item.role == "fuel")
    assert fuel.source_variant == "v1"
    assert fuel.resolution_status is RequirementResolution.requires_generation
    assert fuel.geometry_usage == ("profile-v1",)


def test_universe_skeleton_preserves_profile_and_protected_path():
    result = compile_universe_requirement_skeleton(
        inventory=_inventory(), accepted_facts=SimpleNamespace(fuel_variant_requirements=()),
    )
    insert = next(item for item in result.requirements if item.geometry_profile == "profile-insert")
    assert insert.required_cells == ("absorber",)
    assert insert.protected_paths == ("coolant",)
    assert insert.localized_insert_requirement_id == "insert-1"
