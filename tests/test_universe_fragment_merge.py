"""Tests for universe fragment merge validation."""

from openmc_agent.plan_builder.universe_fragment_generation import (
    UniverseDefinitionFragment,
    UniverseManifest,
    UniverseManifestItem,
    merge_universe_fragments,
    validate_merged_patch,
)
from openmc_agent.plan_builder.patch_generator import FakePatchLLM


def test_merged_patch_is_valid_universes_patch():
    manifest = UniverseManifest(
        manifest_id="test", input_hash="h", expected_universe_count=1,
        items=[UniverseManifestItem(universe_id="u1", kind="fuel_pin")],
        generation_order=["u1"],
    )
    frag = UniverseDefinitionFragment(
        universe_id="u1",
        universe={"universe_id": "u1", "kind": "fuel_pin", "cells": [
            {"id": "c1", "role": "fuel", "material_id": "m1", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}
        ]},
    )
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=[frag])
    assert errors == []
    assert patch["patch_type"] == "universes"
    assert len(patch["universes"]) == 1


def test_merge_unknown_material_detected():
    manifest = UniverseManifest(
        manifest_id="test", input_hash="h", expected_universe_count=1,
        items=[UniverseManifestItem(universe_id="u1", kind="fuel_pin")],
        generation_order=["u1"],
    )
    frag = UniverseDefinitionFragment(
        universe_id="u1",
        universe={"universe_id": "u1", "kind": "fuel_pin", "cells": [
            {"id": "c1", "role": "fuel", "material_id": "nonexistent", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}
        ]},
    )
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=[frag], known_material_ids={"m1"})
    assert any("unknown_material" in e for e in errors)


def test_merge_id_mismatch_detected():
    manifest = UniverseManifest(
        manifest_id="test", input_hash="h", expected_universe_count=1,
        items=[UniverseManifestItem(universe_id="u1", kind="fuel_pin")],
        generation_order=["u1"],
    )
    frag = UniverseDefinitionFragment(
        universe_id="u1",
        universe={"universe_id": "wrong_id", "kind": "fuel_pin", "cells": []},
    )
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=[frag])
    assert any("universe_id_mismatch" in e for e in errors)
