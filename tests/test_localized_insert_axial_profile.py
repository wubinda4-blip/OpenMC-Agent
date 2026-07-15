"""Tests for localized insert axial profiles (P2-FULLCORE-2B).

Verifies multi-segment RCCA-style inserts with anchor-based positioning.
"""

from openmc_agent.plan_builder.patches import (
    LocalizedInsertAxialSegmentPatchItem,
    LocalizedInsertAxialProfilePatchItem,
    LocalizedInsertIntentPatchItem,
)


def test_axial_segment_basic():
    seg = LocalizedInsertAxialSegmentPatchItem(
        segment_id="absorber",
        relative_z_min_cm=0.0,
        relative_z_max_cm=100.0,
        universe_id="rcca_absorber",
        role="absorber",
    )
    assert seg.segment_id == "absorber"
    assert seg.universe_id == "rcca_absorber"


def test_axial_profile_basic():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_profile",
        anchor_kind="bottom",
        anchor_z_cm=257.9,
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="absorber",
                relative_z_min_cm=0.0,
                relative_z_max_cm=100.0,
                universe_id="rcca_absorber",
            ),
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="plenum",
                relative_z_min_cm=100.0,
                relative_z_max_cm=150.0,
                universe_id="rcca_plenum",
            ),
        ],
    )
    assert profile.profile_id == "rcca_profile"
    assert profile.anchor_kind == "bottom"
    assert profile.anchor_z_cm == 257.9
    assert len(profile.segments) == 2


def test_intent_with_axial_profile():
    """Intent can reference a profile instead of static z_min/z_max."""
    intent = LocalizedInsertIntentPatchItem(
        insert_id="rcca_center",
        insert_kind="control_rod",
        insert_universe_id="rcca_absorber",
        coordinates=[(1, 1)],
        axial_profile_id="rcca_profile",
        anchor_z_cm=257.9,
        control_state_id="base",
    )
    assert intent.axial_profile_id == "rcca_profile"
    assert intent.anchor_z_cm == 257.9
    assert intent.control_state_id == "base"


def test_intent_simple_mode_still_works():
    """Simple static insert without profile still works."""
    intent = LocalizedInsertIntentPatchItem(
        insert_id="pyrex1",
        insert_kind="pyrex_rod",
        insert_universe_id="pyrex",
        coordinates=[(1, 1)],
        z_min_cm=0.0,
        z_max_cm=100.0,
    )
    assert intent.axial_profile_id is None
    assert intent.z_min_cm == 0.0
    assert intent.z_max_cm == 100.0


def test_rcca_profile_multi_segment():
    """RCCA profile should have absorber + plenum + end structure."""
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="rcca_full",
        anchor_kind="bottom",
        anchor_z_cm=257.9,
        segments=[
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="lower_end",
                relative_z_min_cm=0.0,
                relative_z_max_cm=10.0,
                universe_id="rcca_end",
                role="end_structure",
            ),
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="absorber",
                relative_z_min_cm=10.0,
                relative_z_max_cm=110.0,
                universe_id="rcca_absorber",
                role="absorber",
            ),
            LocalizedInsertAxialSegmentPatchItem(
                segment_id="upper_plenum",
                relative_z_min_cm=110.0,
                relative_z_max_cm=150.0,
                universe_id="rcca_plenum",
                role="plenum",
            ),
        ],
    )
    assert len(profile.segments) == 3
    roles = [s.role for s in profile.segments]
    assert "end_structure" in roles
    assert "absorber" in roles
    assert "plenum" in roles


def test_profile_anchor_absolute():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="abs_profile",
        anchor_kind="absolute",
    )
    assert profile.anchor_kind == "absolute"


def test_profile_requires_human_confirmation():
    profile = LocalizedInsertAxialProfilePatchItem(
        profile_id="uncertain",
        requires_human_confirmation=True,
    )
    assert profile.requires_human_confirmation is True
