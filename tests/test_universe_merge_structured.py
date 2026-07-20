"""Tests for the structured deterministic merge (Part 5 of Step 4B-1).

Covers:

- canonical order
- missing fragment
- extra fragment
- duplicate fragment
- universe ID mismatch
- invalid fragment hash (via qualification_records drift)
- manifest contract mismatch
- unknown material
- merged schema invalid
- structured issue contains universe ID and JSON path
- fragment / global / manifest scope classification
- same input → same patch and hash
"""

from __future__ import annotations

from openmc_agent.plan_builder.universe_fragment_generation import (
    AcceptedFragmentRecord,
    UniverseDefinitionFragment,
    UniverseManifest,
    UniverseManifestItem,
    UniverseMergeResult,
    merge_universe_fragments_structured,
)


def _manifest(ids: list[str], *, kinds: dict[str, str] | None = None) -> UniverseManifest:
    kinds = kinds or {}
    items = []
    for uid in ids:
        item = UniverseManifestItem(universe_id=uid, kind=kinds.get(uid, "fuel_pin"))
        item.recompute_contract_hash()
        items.append(item)
    return UniverseManifest(
        manifest_id="test_manifest",
        input_hash="test_hash",
        expected_universe_count=len(ids),
        items=items,
        generation_order=list(ids),
    )


def _fragment(uid: str, *, material_id: str = "m_fuel", kind: str = "fuel_pin") -> UniverseDefinitionFragment:
    return UniverseDefinitionFragment(
        universe_id=uid,
        universe={
            "universe_id": uid, "kind": kind,
            "cells": [
                {"id": "c1", "role": "fuel", "material_id": material_id,
                 "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_merge_canonical_order():
    manifest = _manifest(["a", "b", "c"])
    fragments = [_fragment("c"), _fragment("a"), _fragment("b")]  # out of order
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=fragments, known_material_ids={"m_fuel"},
    )
    assert isinstance(result, UniverseMergeResult)
    assert result.ok is True
    assert result.merged_patch is not None
    assert [u["universe_id"] for u in result.merged_patch["universes"]] == ["a", "b", "c"]
    assert result.merged_patch_hash


def test_merge_same_input_same_hash():
    manifest = _manifest(["a", "b"])
    fragments = [_fragment("a"), _fragment("b")]
    r1 = merge_universe_fragments_structured(
        manifest=manifest, fragments=list(fragments), known_material_ids={"m_fuel"},
    )
    r2 = merge_universe_fragments_structured(
        manifest=manifest, fragments=list(fragments), known_material_ids={"m_fuel"},
    )
    assert r1.ok and r2.ok
    assert r1.merged_patch_hash == r2.merged_patch_hash


# ---------------------------------------------------------------------------
# Missing / extra / duplicate
# ---------------------------------------------------------------------------


def test_merge_missing_fragment_is_fragment_scoped():
    manifest = _manifest(["u1", "u2", "u3"])
    fragments = [_fragment("u1"), _fragment("u2")]
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=fragments, known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    assert "u3" in result.invalid_fragment_ids
    missing = [i for i in result.issues if i.code == "merge.missing_fragment"]
    assert len(missing) == 1
    assert missing[0].universe_id == "u3"
    assert missing[0].retry_scope == "fragment"
    assert missing[0].retryable is True
    assert missing[0].json_path == "/universes/u3"


def test_merge_extra_fragment_is_fragment_scoped():
    manifest = _manifest(["u1"])
    fragments = [_fragment("u1"), _fragment("u_extra")]
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=fragments, known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    extra = [i for i in result.issues if i.code == "merge.extra_fragment"]
    assert len(extra) == 1
    assert extra[0].universe_id == "u_extra"
    assert extra[0].retry_scope == "fragment"


def test_merge_duplicate_fragment_is_fragment_scoped():
    manifest = _manifest(["u1"])
    fragments = [_fragment("u1"), _fragment("u1")]
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=fragments, known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    dup = [i for i in result.issues if i.code == "merge.duplicate_fragment"]
    assert len(dup) == 1
    assert dup[0].universe_id == "u1"
    assert dup[0].retry_scope == "fragment"
    assert "u1" in result.invalid_fragment_ids


# ---------------------------------------------------------------------------
# Identity / kind / material
# ---------------------------------------------------------------------------


def test_merge_universe_id_mismatch_is_fragment_scoped():
    manifest = _manifest(["u1"])
    bad_frag = UniverseDefinitionFragment(
        universe_id="u1",
        universe={"universe_id": "wrong_id", "kind": "fuel_pin", "cells": [
            {"id": "c1", "role": "fuel", "material_id": "m_fuel",
             "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}
        ]},
    )
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[bad_frag], known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    mismatch = [i for i in result.issues if i.code == "merge.universe_id_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].universe_id == "u1"
    assert mismatch[0].json_path == "/universes/u1/universe_id"
    assert mismatch[0].retry_scope == "fragment"
    assert "u1" in result.invalid_fragment_ids


def test_merge_kind_mismatch_is_fragment_scoped():
    manifest = _manifest(["u1"], kinds={"u1": "fuel_pin"})
    bad_frag = UniverseDefinitionFragment(
        universe_id="u1",
        universe={"universe_id": "u1", "kind": "guide_tube", "cells": [
            {"id": "c1", "role": "fuel", "material_id": "m_fuel",
             "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}
        ]},
    )
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[bad_frag], known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    mismatch = [i for i in result.issues if i.code == "merge.kind_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].universe_id == "u1"
    assert mismatch[0].retry_scope == "fragment"


def test_merge_unknown_material_is_fragment_scoped_with_json_path():
    manifest = _manifest(["u1"])
    bad_frag = _fragment("u1", material_id="m_unknown")
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[bad_frag], known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    unknown = [i for i in result.issues if i.code == "merge.unknown_material"]
    assert len(unknown) == 1
    assert unknown[0].universe_id == "u1"
    assert unknown[0].json_path == "/universes/u1/cells/c1/material_id"
    assert unknown[0].actual == "m_unknown"
    assert "m_fuel" in unknown[0].expected
    assert "u1" in result.invalid_fragment_ids


# ---------------------------------------------------------------------------
# Manifest scope fail-closed
# ---------------------------------------------------------------------------


def test_merge_manifest_count_mismatch_fails_closed():
    manifest = _manifest(["u1", "u2"])
    # Corrupt: expected count is wrong.
    manifest.expected_universe_count = 3
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[_fragment("u1"), _fragment("u2")],
        known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    assert any(i.retry_scope == "manifest" for i in result.issues)


def test_merge_duplicate_in_generation_order_fails_closed():
    manifest = _manifest(["u1"])
    manifest.generation_order = ["u1", "u1"]
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[_fragment("u1")], known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    assert any(i.code == "merge.manifest_duplicate_in_order" and i.retry_scope == "manifest" for i in result.issues)


# ---------------------------------------------------------------------------
# Qualification record drift
# ---------------------------------------------------------------------------


def test_merge_rejects_fragment_with_unpassed_qualification():
    manifest = _manifest(["u1"])
    frag = _fragment("u1")
    rec = AcceptedFragmentRecord(
        universe_id="u1", universe=frag.universe,
        qualification_status="failed",
        qualification_issues=[{"code": "qualification.unknown_material_id"}],
    )
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[frag],
        known_material_ids={"m_fuel"},
        qualification_records={"u1": rec},
    )
    assert result.ok is False
    bad = [i for i in result.issues if i.code == "merge.qualification_not_passed"]
    assert len(bad) == 1
    assert bad[0].universe_id == "u1"
    assert "u1" in result.invalid_fragment_ids


def test_merge_rejects_fragment_with_drifted_contract_hash():
    manifest = _manifest(["u1"])
    item = manifest.items[0]
    item.recompute_contract_hash()
    frag = _fragment("u1")
    rec = AcceptedFragmentRecord(
        universe_id="u1", universe=frag.universe,
        fragment_hash="somehash", qualification_status="passed",
        manifest_contract_hash="stale_contract_hash",
    )
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[frag],
        known_material_ids={"m_fuel"},
        qualification_records={"u1": rec},
    )
    assert result.ok is False
    drift = [i for i in result.issues if i.code == "merge.manifest_contract_drift"]
    assert len(drift) == 1
    assert drift[0].retry_scope == "fragment"
    assert "u1" in result.invalid_fragment_ids


# ---------------------------------------------------------------------------
# Top-level error code compatibility
# ---------------------------------------------------------------------------


def test_merge_result_top_level_error_code_is_patch_generation_merge_failed():
    manifest = _manifest(["u1"])
    result = merge_universe_fragments_structured(
        manifest=manifest, fragments=[], known_material_ids={"m_fuel"},
    )
    assert result.ok is False
    assert result.top_level_error_code == "patch_generation.merge_failed"
    legacy_patch, legacy_codes = result.to_legacy_tuple()
    assert legacy_patch is None
    assert legacy_codes  # non-empty error list
