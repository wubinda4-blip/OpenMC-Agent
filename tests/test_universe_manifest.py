"""Tests for universe manifest construction and validation."""

from openmc_agent.plan_builder.universe_fragment_generation import (
    UniverseGenerationRequirementSet,
    UniverseGenerationRequirement,
    UniverseManifest,
    UniverseManifestItem,
    build_manifest_from_requirements,
    validate_manifest,
)


def _req_set():
    return UniverseGenerationRequirementSet(
        requirements=[
            UniverseGenerationRequirement(
                requirement_id="fuel:v1", universe_id="u_fuel",
                kind="fuel_pin", required_cell_roles=["fuel"],
                resolved=True,
            ),
            UniverseGenerationRequirement(
                requirement_id="insert:r1", universe_id="u_cr",
                kind="control_rod", required_cell_roles=["absorber"],
                resolved=True,
            ),
        ],
        input_hash="test_hash_001",
    )


def test_manifest_covers_all_requirements():
    reqs = _req_set()
    manifest = build_manifest_from_requirements(reqs)
    assert manifest.expected_universe_count == 2
    assert len(manifest.items) == 2
    assert set(manifest.generation_order) == {"u_fuel", "u_cr"}


def test_manifest_validation_passes():
    reqs = _req_set()
    manifest = build_manifest_from_requirements(reqs)
    errors = validate_manifest(manifest, reqs)
    assert errors == []


def test_manifest_missing_required_universe():
    reqs = _req_set()
    manifest = build_manifest_from_requirements(reqs)
    manifest.items = manifest.items[:1]  # remove one
    manifest.generation_order = [manifest.items[0].universe_id]
    errors = validate_manifest(manifest, reqs)
    assert "manifest.missing_required_universe" in errors


def test_manifest_duplicate_id():
    reqs = _req_set()
    manifest = build_manifest_from_requirements(reqs)
    manifest.items[1].universe_id = manifest.items[0].universe_id
    errors = validate_manifest(manifest, reqs)
    assert "manifest.duplicate_universe_id" in errors


def test_manifest_generation_order_mismatch():
    reqs = _req_set()
    manifest = build_manifest_from_requirements(reqs)
    manifest.generation_order = [manifest.items[0].universe_id]
    errors = validate_manifest(manifest, reqs)
    assert "manifest.generation_order_mismatch" in errors
