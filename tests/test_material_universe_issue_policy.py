"""Phase 4: Material-Universe issue policy (Python owner registry)."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_issue_policy import material_universe_issue_owner, registered_material_universe_issue_codes


def test_materials_owner_for_density_issue() -> None:
    owner = material_universe_issue_owner("material_universe.material_density_invalid")
    assert owner["owner_patch_types"] == ["materials"]


def test_universes_owner_for_unknown_reference() -> None:
    owner = material_universe_issue_owner("material_universe.material_reference_missing")
    assert owner["owner_patch_types"] == ["universes"]


def test_facts_dependency_for_missing_fuel_variant() -> None:
    owner = material_universe_issue_owner("material_universe.required_fuel_variant_missing")
    assert owner.get("dependency_patch_type") == "facts"


def test_universe_duplicate_routed_to_universes() -> None:
    owner = material_universe_issue_owner("material_universe.universe_duplicate")
    assert owner["owner_patch_types"] == ["universes"]


def test_material_duplicate_routed_to_materials() -> None:
    owner = material_universe_issue_owner("material_universe.material_duplicate")
    assert owner["owner_patch_types"] == ["materials"]


def test_unknown_code_fails_closed() -> None:
    owner = material_universe_issue_owner("invented.unknown_code")
    assert owner == {}


def test_registered_codes_cover_all_categories() -> None:
    codes = registered_material_universe_issue_codes()
    # Materials codes
    assert any(c.startswith("material_universe.material_") for c in codes)
    # Universe codes
    assert any("universe" in c for c in codes)
    # Fuel variant codes
    assert any("fuel_variant" in c for c in codes)
