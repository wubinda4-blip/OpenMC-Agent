"""Tests for the structured universe manifest contract (Part 2 of Step 4B-1).

Covers:

- requirement field pass-through to manifest items
- localized insert requirement ID preserved
- profile ID preserved
- protected path roles preserved
- source requirement IDs preserved
- per-item contract hash stability
- contract field change → hash change
- order change → per-item hash unchanged
- duplicate ID and missing requirement fail closed
"""

from __future__ import annotations

from openmc_agent.plan_builder.universe_fragment_generation import (
    UniverseGenerationRequirement,
    UniverseGenerationRequirementSet,
    UniverseManifest,
    UniverseManifestItem,
    build_manifest_from_requirements,
    compute_manifest_item_contract_hash,
    validate_manifest,
)


def _req(
    *,
    requirement_id: str,
    universe_id: str | None = None,
    kind: str = "fuel_pin",
    required_cell_roles: list[str] | None = None,
    required_material_ids: list[str] | None = None,
    required_material_roles: list[str] | None = None,
    fuel_variant_id: str | None = None,
    localized_insert_requirement_id: str | None = None,
    base_path_component_profile_id: str | None = None,
    protected_through_path_roles: list[str] | None = None,
    source_requirement_ids: list[str] | None = None,
    dependency_ids: list[str] | None = None,
) -> UniverseGenerationRequirement:
    return UniverseGenerationRequirement(
        requirement_id=requirement_id,
        universe_id=universe_id or requirement_id.replace(":", "_"),
        kind=kind,
        required_cell_roles=required_cell_roles or [],
        required_material_ids=required_material_ids or [],
        required_material_roles=required_material_roles or [],
        fuel_variant_id=fuel_variant_id,
        localized_insert_requirement_id=localized_insert_requirement_id,
        base_path_component_profile_id=base_path_component_profile_id,
        protected_through_path_roles=protected_through_path_roles or [],
        source_requirement_ids=source_requirement_ids or [requirement_id],
        dependency_ids=dependency_ids or [],
        resolved=True,
    )


def _req_set(*reqs: UniverseGenerationRequirement) -> UniverseGenerationRequirementSet:
    return UniverseGenerationRequirementSet(requirements=list(reqs), input_hash="test_input_hash")


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------


def test_manifest_preserves_localized_insert_requirement_id():
    req = _req(
        requirement_id="localized_insert:r1",
        universe_id="u_insert",
        kind="control_rod",
        localized_insert_requirement_id="r1",
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert len(manifest.items) == 1
    assert manifest.items[0].localized_insert_requirement_id == "r1"


def test_manifest_preserves_base_path_component_profile_id():
    req = _req(
        requirement_id="profile:p1",
        universe_id="u_profile",
        kind="custom",
        base_path_component_profile_id="p1",
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].base_path_component_profile_id == "p1"


def test_manifest_preserves_protected_through_path_roles():
    req = _req(
        requirement_id="protected:x",
        universe_id="u_ptp",
        kind="fuel_pin",
        protected_through_path_roles=["fuel", "cladding"],
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].protected_through_path_roles == ["fuel", "cladding"]


def test_manifest_preserves_source_requirement_ids():
    req = _req(
        requirement_id="multi:a",
        universe_id="u_multi",
        kind="fuel_pin",
        source_requirement_ids=["multi:a", "multi:b"],
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].source_requirement_ids == ["multi:a", "multi:b"]


def test_manifest_preserves_dependency_ids():
    req = _req(
        requirement_id="dep:a",
        universe_id="u_dep",
        kind="fuel_pin",
        dependency_ids=["dep:b"],
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].dependency_ids == ["dep:b"]


def test_manifest_preserves_fuel_variant_id():
    req = _req(
        requirement_id="fv:v1",
        universe_id="u_fv",
        kind="fuel_pin",
        fuel_variant_id="v1",
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].fuel_variant_id == "v1"


def test_manifest_preserves_required_material_roles():
    req = _req(
        requirement_id="roles:r",
        universe_id="u_roles",
        kind="fuel_pin",
        required_material_roles=["fuel", "cladding"],
    )
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].required_material_roles == ["fuel", "cladding"]


# ---------------------------------------------------------------------------
# Contract hash
# ---------------------------------------------------------------------------


def test_manifest_items_have_contract_hash():
    req = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    manifest = build_manifest_from_requirements(_req_set(req))
    assert manifest.items[0].contract_hash
    assert len(manifest.items[0].contract_hash) == 16  # truncated SHA-256


def test_contract_hash_stable_across_order_changes():
    req_a = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    req_b = _req(requirement_id="r:b", universe_id="u_b", kind="fuel_pin")
    manifest1 = build_manifest_from_requirements(_req_set(req_a, req_b))
    manifest2 = build_manifest_from_requirements(_req_set(req_b, req_a))
    hash_a_1 = next(i.contract_hash for i in manifest1.items if i.universe_id == "u_a")
    hash_a_2 = next(i.contract_hash for i in manifest2.items if i.universe_id == "u_a")
    assert hash_a_1 == hash_a_2


def test_contract_hash_changes_when_contract_field_changes():
    req1 = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    req2 = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin", required_cell_roles=["fuel"])
    m1 = build_manifest_from_requirements(_req_set(req1))
    m2 = build_manifest_from_requirements(_req_set(req2))
    assert m1.items[0].contract_hash != m2.items[0].contract_hash


def test_compute_manifest_item_contract_hash_excludes_metadata():
    """metadata, expected_cell_count, assumptions_allowed are NOT part of contract."""
    base_data = {
        "universe_id": "u_x", "kind": "fuel_pin",
        "required_cell_roles": ["fuel"],
        "required_material_ids": [],
        "required_material_roles": [],
        "fuel_variant_id": None,
        "localized_insert_requirement_id": None,
        "base_path_component_profile_id": None,
        "protected_through_path_roles": [],
        "source_requirement_ids": ["r:x"],
        "dependency_ids": [],
    }
    variant_a = dict(base_data, metadata={"foo": 1}, expected_cell_count=3, assumptions_allowed=True)
    variant_b = dict(base_data, metadata={"bar": 2}, expected_cell_count=5, assumptions_allowed=False)
    assert compute_manifest_item_contract_hash(variant_a) == compute_manifest_item_contract_hash(variant_b)


# ---------------------------------------------------------------------------
# Validation (fail-closed)
# ---------------------------------------------------------------------------


def test_validate_manifest_catches_duplicate_universe_id():
    req_a = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    req_b = _req(requirement_id="r:b", universe_id="u_a", kind="fuel_pin")  # dup id
    manifest = build_manifest_from_requirements(_req_set(req_a, req_b))
    errors = validate_manifest(manifest, _req_set(req_a, req_b))
    assert "manifest.duplicate_universe_id" in errors


def test_validate_manifest_catches_missing_required_universe():
    req_a = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    req_b = _req(requirement_id="r:b", universe_id="u_b", kind="fuel_pin")
    manifest = build_manifest_from_requirements(_req_set(req_a, req_b))
    # Drop one item but keep the requirement set intact.
    manifest.items = manifest.items[:1]
    manifest.generation_order = [manifest.items[0].universe_id]
    errors = validate_manifest(manifest, _req_set(req_a, req_b))
    assert "manifest.missing_required_universe" in errors


def test_validate_manifest_catches_unknown_material_id():
    req_a = _req(
        requirement_id="r:a", universe_id="u_a", kind="fuel_pin",
        required_material_ids=["m_unknown"],
    )
    manifest = build_manifest_from_requirements(_req_set(req_a), known_material_ids={"m_known"})
    errors = validate_manifest(manifest, _req_set(req_a), known_material_ids={"m_known"})
    assert any("unknown_material_id" in e for e in errors)


def test_manifest_no_benchmark_names():
    """No benchmark names (VERA3, VERA4, ...) leak into the manifest."""
    req = _req(requirement_id="r:a", universe_id="u_a", kind="fuel_pin")
    manifest = build_manifest_from_requirements(_req_set(req))
    serialized = manifest.model_dump_json().lower()
    assert "vera" not in serialized
    assert "benchmark" not in serialized
