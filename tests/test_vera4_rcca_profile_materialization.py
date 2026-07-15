"""Tests for VERA4 RCCA profile materialization (P2-FULLCORE-2D-A)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vera4_base_fixture import (
    RCCA_ANCHOR_Z, RCCA_AIC_HEIGHT, RCCA_B4C_TOTAL, RCCA_PLENUM_TOTAL, RCCA_ENDPLUG_TOTAL,
    build_vera4_rcca_profile, build_vera4_assembly_catalog,
    build_all_vera4_patches,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.localized_insert_profiles import (
    resolve_all_profiles_for_catalog,
)


class TestRCCAProfileRegistry:
    def test_profile_exists(self):
        profiles = build_vera4_rcca_profile()
        assert len(profiles.profiles) == 1
        assert profiles.profiles[0].profile_id == "rcca_base"

    def test_profile_has_4_segments(self):
        profiles = build_vera4_rcca_profile()
        segs = profiles.profiles[0].segments
        assert len(segs) == 4
        seg_ids = [s.segment_id for s in segs]
        assert "aic" in seg_ids
        assert "b4c" in seg_ids
        assert "plenum" in seg_ids
        assert "endplug" in seg_ids

    def test_profile_anchor_is_bottom(self):
        profiles = build_vera4_rcca_profile()
        assert profiles.profiles[0].anchor_kind == "bottom"
        assert profiles.profiles[0].anchor_z_cm == RCCA_ANCHOR_Z

    def test_segment_boundaries(self):
        """Verify dynamic boundary computation."""
        z_bottom = RCCA_ANCHOR_Z
        z_aic_top = z_bottom + RCCA_AIC_HEIGHT
        z_b4c_top = z_bottom + RCCA_B4C_TOTAL
        z_plenum_top = z_bottom + RCCA_PLENUM_TOTAL
        z_endplug_top = z_bottom + RCCA_ENDPLUG_TOTAL

        assert abs(z_aic_top - 359.500) < 1e-3
        assert abs(z_b4c_top - 618.580) < 1e-3
        assert abs(z_plenum_top - 629.280) < 1e-3
        assert abs(z_endplug_top - 631.180) < 1e-3


class TestRCCAResolution:
    def test_profile_resolves_to_absolute_segments(self):
        profiles = build_vera4_rcca_profile()
        catalog = build_vera4_assembly_catalog()
        resolved = resolve_all_profiles_for_catalog(catalog, profiles)

        assert len(resolved) == 1
        rp = resolved[0]
        assert rp.profile_id == "rcca_base"
        assert rp.insert_id == "center_rcca"
        assert rp.assembly_type_id == "center_rcca"
        assert rp.anchor_z_cm == RCCA_ANCHOR_Z
        assert rp.control_state_id == "base"

    def test_resolved_aic_absolute_bounds(self):
        profiles = build_vera4_rcca_profile()
        catalog = build_vera4_assembly_catalog()
        resolved = resolve_all_profiles_for_catalog(catalog, profiles)
        rp = resolved[0]

        aic_seg = next(s for s in rp.resolved_segments if s.segment_id == "aic")
        assert abs(aic_seg.absolute_z_min_cm - 257.900) < 1e-3
        assert abs(aic_seg.absolute_z_max_cm - 359.500) < 1e-3
        assert aic_seg.universe_id == "rcca_aic"

    def test_resolved_b4c_absolute_bounds(self):
        profiles = build_vera4_rcca_profile()
        catalog = build_vera4_assembly_catalog()
        resolved = resolve_all_profiles_for_catalog(catalog, profiles)
        rp = resolved[0]

        b4c_seg = next(s for s in rp.resolved_segments if s.segment_id == "b4c")
        assert abs(b4c_seg.absolute_z_min_cm - 359.500) < 1e-3
        assert abs(b4c_seg.absolute_z_max_cm - 618.580) < 1e-3
        assert b4c_seg.universe_id == "rcca_b4c"


class TestRCCAAssembly:
    def test_center_assembly_has_rcca_intent_with_profile(self):
        catalog = build_vera4_assembly_catalog()
        center = next(at for at in catalog.assembly_types if at.assembly_type_id == "center_rcca")
        rcca_intents = [i for i in center.pin_map.localized_insert_intents if i.insert_kind == "control_rod"]
        assert len(rcca_intents) == 1
        assert rcca_intents[0].axial_profile_id == "rcca_base"
        assert rcca_intents[0].anchor_z_cm == RCCA_ANCHOR_Z
        assert rcca_intents[0].control_state_id == "base"

    def test_rcca_path_count_is_24(self):
        """Center assembly RCCA at 24 guide tube positions."""
        catalog = build_vera4_assembly_catalog()
        center = next(at for at in catalog.assembly_types if at.assembly_type_id == "center_rcca")
        rcca_intents = [i for i in center.pin_map.localized_insert_intents if i.insert_kind == "control_rod"]
        assert len(rcca_intents[0].coordinates) == 24

    def test_rcca_excludes_instrument_tube(self):
        catalog = build_vera4_assembly_catalog()
        center = next(at for at in catalog.assembly_types if at.assembly_type_id == "center_rcca")
        rcca_intents = [i for i in center.pin_map.localized_insert_intents if i.insert_kind == "control_rod"]
        assert (9, 9) not in rcca_intents[0].coordinates

    def test_rcca_universes_present_in_assembled_plan(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        uv_ids = {u.id for u in result.plan.complex_model.universes}
        assert "rcca_aic" in uv_ids
        assert "rcca_b4c" in uv_ids
