"""Tests for LocalizedInsertProfilesPatch and profile resolver (P2-FULLCORE-2C-A).

Covers:
- Profile registry patch parsing
- Anchor resolution (bottom/top/center/absolute)
- Profile segment resolution to absolute z
- Profile validation (duplicates, overlaps, gaps, missing universes)
- Cross-reference validation (intent -> profile)
- Resolved profile for intent
- Global axial segment compilation with profiles
"""

import pytest
from openmc_agent.plan_builder.patches import (
    LocalizedInsertAxialProfilePatchItem,
    LocalizedInsertAxialSegmentPatchItem,
    LocalizedInsertIntentPatchItem,
    LocalizedInsertProfilesPatch,
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    FactsPatch,
    parse_patch_content,
)
from openmc_agent.plan_builder.localized_insert_profiles import (
    ResolvedLocalizedInsertProfile,
    ResolvedSegment,
    resolve_profile_anchor,
    resolve_profile_absolute_segments,
    resolve_profile_for_intent,
    resolve_all_profiles_for_catalog,
    validate_profile_registry,
    validate_profile_segments,
    validate_profile_references,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    compile_global_axial_segments,
)


# ---------------------------------------------------------------------------
# Patch parsing
# ---------------------------------------------------------------------------


def test_profile_patch_parsing():
    patch = parse_patch_content("localized_insert_profiles", {
        "profiles": [
            {
                "profile_id": "rcca_1",
                "anchor_kind": "bottom",
                "anchor_z_cm": 200.0,
                "segments": [
                    {"segment_id": "abs", "relative_z_min_cm": 0, "relative_z_max_cm": 100, "universe_id": "abs_uv"},
                ],
            }
        ],
    })
    assert isinstance(patch, LocalizedInsertProfilesPatch)
    assert len(patch.profiles) == 1
    assert patch.profiles[0].profile_id == "rcca_1"


def test_profile_patch_empty_is_valid():
    patch = LocalizedInsertProfilesPatch(profiles=[])
    assert len(patch.profiles) == 0


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------


def test_anchor_bottom():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="rcca_1",
        anchor_z_cm=257.9,
    )
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_1",
        anchor_kind="bottom",
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="abs", relative_z_min_cm=0, relative_z_max_cm=100,
                universe_id="abs_uv",
            ),
        ],
    )
    anchor, issues = resolve_profile_anchor(intent, profile)
    assert anchor == 257.9
    assert len(issues) == 0


def test_anchor_absolute_no_intent_anchor():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="abs_profile",
    )
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="abs_profile",
        anchor_kind="absolute",
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="s1", relative_z_min_cm=200, relative_z_max_cm=300,
                universe_id="abs_uv",
            ),
        ],
    )
    anchor, issues = resolve_profile_anchor(intent, profile)
    assert anchor == 0.0
    assert len(issues) == 0


def test_anchor_absolute_conflict():
    """Intent must not provide anchor_z_cm for absolute profiles."""
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="abs_profile",
        anchor_z_cm=250.0,
    )
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="abs_profile",
        anchor_kind="absolute",
    )
    anchor, issues = resolve_profile_anchor(intent, profile)
    assert anchor is None
    assert any("profile_anchor_conflict" in i["code"] for i in issues)


def test_anchor_conflict_intent_vs_profile():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="rcca_1",
        anchor_z_cm=257.9,
    )
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_1",
        anchor_kind="bottom",
        anchor_z_cm=300.0,
    )
    anchor, issues = resolve_profile_anchor(intent, profile)
    assert anchor is None
    assert any("conflict" in i["code"] for i in issues)


def test_anchor_missing():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="rcca_1",
    )
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_1",
        anchor_kind="bottom",
    )
    anchor, issues = resolve_profile_anchor(intent, profile)
    assert anchor is None
    assert any("missing" in i["code"] for i in issues)


# ---------------------------------------------------------------------------
# Absolute segment resolution
# ---------------------------------------------------------------------------


def test_resolve_bottom_anchor():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_1",
        anchor_kind="bottom",
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=100,
                universe_id="uv1",
            ),
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="s2", relative_z_min_cm=100, relative_z_max_cm=150,
                universe_id="uv2",
            ),
        ],
    )
    resolved = resolve_profile_absolute_segments(profile, anchor_z_cm=257.9)
    assert len(resolved) == 2
    assert resolved[0].absolute_z_min_cm == pytest.approx(257.9)
    assert resolved[0].absolute_z_max_cm == pytest.approx(357.9)
    assert resolved[1].absolute_z_min_cm == pytest.approx(357.9)
    assert resolved[1].absolute_z_max_cm == pytest.approx(407.9)


def test_resolve_top_anchor():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_1",
        anchor_kind="top",
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=100,
                universe_id="uv1",
            ),
        ],
    )
    resolved = resolve_profile_absolute_segments(profile, anchor_z_cm=400.0)
    assert len(resolved) == 1
    assert resolved[0].absolute_z_min_cm == pytest.approx(300.0)
    assert resolved[0].absolute_z_max_cm == pytest.approx(400.0)


def test_resolve_absolute():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="abs_1",
        anchor_kind="absolute",
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="s1", relative_z_min_cm=200, relative_z_max_cm=300,
                universe_id="uv1",
            ),
        ],
    )
    resolved = resolve_profile_absolute_segments(profile, anchor_z_cm=0.0)
    assert len(resolved) == 1
    assert resolved[0].absolute_z_min_cm == pytest.approx(200.0)
    assert resolved[0].absolute_z_max_cm == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Resolve for intent
# ---------------------------------------------------------------------------


def test_resolve_for_intent_simple():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(8, 8)],
        axial_profile_id="rcca_1",
        anchor_z_cm=257.9,
        control_state_id="base",
    )
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="rcca_1",
            anchor_kind="bottom",
            segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="abs", relative_z_min_cm=0, relative_z_max_cm=100,
                    universe_id="abs_uv",
                ),
            ],
        ),
    ])
    resolved = resolve_profile_for_intent(intent, profiles_patch, assembly_type_id="type_a")
    assert resolved is not None
    assert resolved.profile_id == "rcca_1"
    assert resolved.insert_id == "rod_1"
    assert resolved.control_state_id == "base"
    assert resolved.deterministic
    assert len(resolved.resolved_segments) == 1
    assert resolved.resolved_segments[0].absolute_z_min_cm == pytest.approx(257.9)
    assert resolved.resolved_segments[0].absolute_z_max_cm == pytest.approx(357.9)


def test_resolve_for_intent_no_profile_id():
    """Intent without axial_profile_id returns None."""
    intent = LocalizedInsertIntentPatchItem(
        insert_id="pyrex1", insert_kind="pyrex_rod",
        insert_universe_id="pyrex_uv",
        coordinates=[(1, 1)],
        z_min_cm=0.0, z_max_cm=100.0,
    )
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[])
    resolved = resolve_profile_for_intent(intent, profiles_patch)
    assert resolved is None


def test_resolve_for_intent_missing_profile():
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rod_1", insert_kind="control_rod",
        insert_universe_id="abs_uv",
        coordinates=[(1, 1)],
        axial_profile_id="nonexistent",
    )
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[])
    resolved = resolve_profile_for_intent(intent, profiles_patch)
    assert resolved is not None
    assert not resolved.deterministic
    assert any("ref_missing" in i["code"] for i in resolved.issues)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_registry_duplicate_profile_id():
    patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="dup", segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=10,
                    universe_id="uv1",
                ),
            ],
        ),
        LocalizedInsertAxialProfilePatchItem(
            profile_id="dup", segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=10,
                    universe_id="uv1",
                ),
            ],
        ),
    ])
    result = validate_profile_registry(patch)
    assert not result.ok
    assert any("duplicate" in i["code"] for i in result.issues)


def test_validate_registry_empty_profile():
    patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(profile_id="empty", segments=[]),
    ])
    result = validate_profile_registry(patch)
    assert not result.ok
    assert any("empty" in i["code"] for i in result.issues)


def test_validate_registry_overlap():
    patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="p1", segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=100,
                    universe_id="uv1",
                ),
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s2", relative_z_min_cm=50, relative_z_max_cm=150,
                    universe_id="uv2",
                ),
            ],
        ),
    ])
    result = validate_profile_registry(patch)
    assert not result.ok
    assert any("overlap" in i["code"] for i in result.issues)


def test_validate_registry_universe_missing():
    patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="p1", segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=10,
                    universe_id="missing_uv",
                ),
            ],
        ),
    ])
    result = validate_profile_registry(patch, known_universe_ids=["uv1", "uv2"])
    assert not result.ok
    assert any("universe_missing" in i["code"] for i in result.issues)


def test_validate_registry_valid():
    patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="p1", segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=100,
                    universe_id="uv1",
                ),
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s2", relative_z_min_cm=100, relative_z_max_cm=150,
                    universe_id="uv2",
                ),
            ],
        ),
    ])
    result = validate_profile_registry(patch, known_universe_ids=["uv1", "uv2"])
    assert result.ok


def test_validate_references_missing():
    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_uv",
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="rod_1", insert_kind="control_rod",
                        insert_universe_id="abs_uv",
                        coordinates=[(1, 1)],
                        axial_profile_id="nonexistent",
                    ),
                ],
            ),
        ),
    ])
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[])
    result = validate_profile_references(catalog, profiles_patch)
    assert not result.ok


def test_validate_references_conflict():
    """Intent with both axial_profile_id and z_min/z_max is a conflict."""
    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_uv",
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="rod_1", insert_kind="control_rod",
                        insert_universe_id="abs_uv",
                        coordinates=[(1, 1)],
                        axial_profile_id="rcca_1",
                        z_min_cm=0.0, z_max_cm=100.0,
                    ),
                ],
            ),
        ),
    ])
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="rcca_1", anchor_kind="bottom",
            segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="s1", relative_z_min_cm=0, relative_z_max_cm=100,
                    universe_id="abs_uv",
                ),
            ],
        ),
    ])
    result = validate_profile_references(catalog, profiles_patch)
    assert not result.ok
    assert any("conflict" in i["code"] for i in result.issues)


# ---------------------------------------------------------------------------
# Global axial segment compilation with profiles
# ---------------------------------------------------------------------------


def _make_catalog_with_profiled_insert():
    return AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_uv",
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="rod_1", insert_kind="control_rod",
                        insert_universe_id="abs_uv",
                        coordinates=[(1, 1)],
                        axial_profile_id="rcca_1",
                        anchor_z_cm=200.0,
                    ),
                ],
            ),
        ),
    ])


def test_global_segments_with_profile():
    catalog = _make_catalog_with_profiled_insert()
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=1,
        axial_domain_cm=(0.0, 400.0),
    )
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="rcca_1", anchor_kind="bottom",
            segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="abs", relative_z_min_cm=0, relative_z_max_cm=100,
                    universe_id="abs_uv",
                ),
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="plen", relative_z_min_cm=100, relative_z_max_cm=150,
                    universe_id="plen_uv",
                ),
            ],
        ),
    ])
    resolved = resolve_all_profiles_for_catalog(catalog, profiles_patch)
    assert len(resolved) == 1

    segments = compile_global_axial_segments(
        facts, catalog, resolved_profiles=resolved,
    )
    assert len(segments) > 0
    breakpoints = {s.z_min_cm for s in segments} | {s.z_max_cm for s in segments}
    assert 200.0 in breakpoints
    assert 300.0 in breakpoints
    assert 350.0 in breakpoints


def test_global_segments_without_profile_skips_profile_breakpoints():
    """Simple inserts (no profile) still work."""
    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_uv",
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="pyrex1", insert_kind="pyrex_rod",
                        insert_universe_id="pyrex_uv",
                        coordinates=[(1, 1)],
                        z_min_cm=50.0, z_max_cm=150.0,
                    ),
                ],
            ),
        ),
    ])
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=1,
        axial_domain_cm=(0.0, 400.0),
    )
    segments = compile_global_axial_segments(facts, catalog)
    breakpoints = {s.z_min_cm for s in segments} | {s.z_max_cm for s in segments}
    assert 50.0 in breakpoints
    assert 150.0 in breakpoints
