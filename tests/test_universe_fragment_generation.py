"""Tests for universe fragment generation and merge."""

from openmc_agent.plan_builder.universe_fragment_generation import (
    UniverseDefinitionFragment,
    UniverseManifest,
    UniverseManifestItem,
    merge_universe_fragments,
    validate_merged_patch,
    estimate_universes_output_size,
    should_fragment_universes,
)


def _manifest(ids):
    return UniverseManifest(
        manifest_id="test",
        input_hash="hash",
        expected_universe_count=len(ids),
        items=[UniverseManifestItem(universe_id=i, kind="fuel_pin") for i in ids],
        generation_order=list(ids),
    )


def _fragment(uid):
    return UniverseDefinitionFragment(
        universe_id=uid,
        universe={"universe_id": uid, "kind": "fuel_pin", "cells": [
            {"id": "c1", "role": "fuel", "material_id": "m1", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}
        ]},
    )


def test_merge_two_fragments():
    manifest = _manifest(["u1", "u2"])
    fragments = [_fragment("u1"), _fragment("u2")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    assert errors == []
    assert patch is not None
    assert len(patch["universes"]) == 2


def test_merge_missing_fragment():
    manifest = _manifest(["u1", "u2", "u3"])
    fragments = [_fragment("u1"), _fragment("u2")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    assert patch is None
    assert any("missing_fragment" in e for e in errors)


def test_merge_duplicate_fragment():
    manifest = _manifest(["u1"])
    fragments = [_fragment("u1"), _fragment("u1")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    assert patch is None
    assert any("duplicate_fragment" in e for e in errors)


def test_merge_extra_fragment():
    manifest = _manifest(["u1"])
    fragments = [_fragment("u1"), _fragment("u_extra")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    assert patch is None
    assert any("extra_fragment" in e for e in errors)


def test_merge_order_stable():
    manifest = _manifest(["a", "b", "c"])
    fragments = [_fragment("c"), _fragment("a"), _fragment("b")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    assert errors == []
    assert [u["universe_id"] for u in patch["universes"]] == ["a", "b", "c"]


def test_merge_validates_with_existing_validator():
    manifest = _manifest(["u1"])
    fragments = [_fragment("u1")]
    patch, errors = merge_universe_fragments(manifest=manifest, fragments=fragments)
    ok, issues = validate_merged_patch(patch, known_material_ids={"m1"})
    assert ok is True


def test_estimate_output_size_grows_with_count():
    small = estimate_universes_output_size(universe_count=3)
    large = estimate_universes_output_size(universe_count=11)
    assert large > small


def test_should_fragment_explicit_fragmented():
    do_it, reason = should_fragment_universes(mode="fragmented", universe_count=2)
    assert do_it is True


def test_should_fragment_explicit_monolithic():
    do_it, reason = should_fragment_universes(mode="monolithic", universe_count=20)
    assert do_it is False


def test_should_fragment_auto_large_count():
    do_it, reason = should_fragment_universes(mode="auto", universe_count=15)
    assert do_it is True


def test_should_fragment_auto_small_count():
    do_it, reason = should_fragment_universes(mode="auto", universe_count=3, provider_max_output_tokens=16000)
    assert do_it is False


def test_should_fragment_history_truncated():
    do_it, reason = should_fragment_universes(mode="auto", universe_count=3, history_json_truncated=True)
    assert do_it is True
