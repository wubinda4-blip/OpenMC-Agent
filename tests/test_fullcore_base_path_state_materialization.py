"""Tests for base fuel-path axial-state materialization (P2-FULLCORE-2D-A-HARDENING)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vera4_base_fixture import build_all_vera4_patches, build_vera4_base_path_profiles
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.axial_state_materializer import (
    _resolve_base_path_bindings,
    _apply_base_path_state,
)


class TestBasePathProfileContract:
    def test_profile_has_5_bindings(self):
        profiles = build_vera4_base_path_profiles()
        assert len(profiles.profiles) == 1
        p = profiles.profiles[0]
        assert p.profile_id == "vera4_fuel_path"
        assert len(p.state_bindings) == 5

    def test_bindings_cover_non_active_roles(self):
        profiles = build_vera4_base_path_profiles()
        roles = {b.axial_role for b in profiles.profiles[0].state_bindings}
        assert "lower_shoulder_gap" in roles
        assert "lower_fuel_endplug" in roles
        assert "upper_fuel_endplug" in roles
        assert "fuel_upper_plenum" in roles
        assert "upper_shoulder_gap" in roles

    def test_active_fuel_not_in_bindings(self):
        """Active fuel should NOT be in bindings — it's the default state."""
        profiles = build_vera4_base_path_profiles()
        roles = {b.axial_role for b in profiles.profiles[0].state_bindings}
        assert "active_fuel" not in roles


class TestBasePathStateResolution:
    def test_shoulder_gap_resolves_to_water_pin(self):
        profiles = build_vera4_base_path_profiles()
        pm = {p.profile_id: p for p in profiles.profiles}
        bindings = _resolve_base_path_bindings("lower_shoulder_gap", "corner", pm, "vera4_fuel_path")
        assert len(bindings) == 1
        assert bindings[0].replacement_universe_id == "water_pin"

    def test_endplug_resolves_to_fuel_endplug(self):
        profiles = build_vera4_base_path_profiles()
        pm = {p.profile_id: p for p in profiles.profiles}
        bindings = _resolve_base_path_bindings("lower_fuel_endplug", "corner", pm, "vera4_fuel_path")
        assert len(bindings) == 1
        assert bindings[0].replacement_universe_id == "fuel_endplug"

    def test_plenum_resolves_to_fuel_plenum(self):
        profiles = build_vera4_base_path_profiles()
        pm = {p.profile_id: p for p in profiles.profiles}
        bindings = _resolve_base_path_bindings("fuel_upper_plenum", "edge", pm, "vera4_fuel_path")
        assert len(bindings) == 1
        assert bindings[0].replacement_universe_id == "fuel_plenum"

    def test_active_fuel_returns_no_bindings(self):
        profiles = build_vera4_base_path_profiles()
        pm = {p.profile_id: p for p in profiles.profiles}
        bindings = _resolve_base_path_bindings("active_fuel", "corner", pm, "vera4_fuel_path")
        assert len(bindings) == 0


class TestBasePathStateApplication:
    def test_fuel_replaced_with_water_pin(self):
        """In shoulder gap, fuel positions should be replaced with water_pin."""
        pattern = [["fuel_active_r1", "guide_tube"], ["fuel_active_r1", "fuel_active_r1"]]
        from openmc_agent.plan_builder.patches import BasePathStateBindingPatchItem
        bindings = [BasePathStateBindingPatchItem(
            axial_role="lower_shoulder_gap",
            source_universe_ids=["fuel_active_r1", "fuel_active_r2"],
            replacement_universe_id="water_pin",
        )]
        modified, applied = _apply_base_path_state(pattern, bindings)
        assert modified[0][0] == "water_pin"
        assert modified[0][1] == "guide_tube"  # guide tube preserved
        assert modified[1][0] == "water_pin"
        assert "water_pin" in applied

    def test_fuel_replaced_with_endplug(self):
        pattern = [["fuel_active_r1", "guide_tube"]]
        from openmc_agent.plan_builder.patches import BasePathStateBindingPatchItem
        bindings = [BasePathStateBindingPatchItem(
            axial_role="lower_fuel_endplug",
            source_universe_ids=["fuel_active_r1"],
            replacement_universe_id="fuel_endplug",
        )]
        modified, _ = _apply_base_path_state(pattern, bindings)
        assert modified[0][0] == "fuel_endplug"
        assert modified[0][1] == "guide_tube"


class TestAssembledBasePathStates:
    def test_water_pin_in_shoulder_segments(self):
        """Assembled model should have water_pin in shoulder gap segments."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        assert result.ok
        found_water = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid == "water_pin":
                            found_water = True
                            break
        assert found_water, "No water_pin in any lattice — base path states not working"

    def test_fuel_endplug_in_endplug_segments(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        found_endplug = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid == "fuel_endplug":
                            found_endplug = True
        assert found_endplug

    def test_fuel_plenum_in_plenum_segments(self):
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        found_plenum = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid == "fuel_plenum":
                            found_plenum = True
        assert found_plenum

    def test_active_fuel_still_present(self):
        """Active fuel should still be present in the active fuel segments."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        found_active = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid in ("fuel_active_r1", "fuel_active_r2"):
                            found_active = True
        assert found_active

    def test_r1_and_r2_distinct(self):
        """R1 and R2 fuel variants should remain distinct in active fuel segments."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        # Corner uses r1, edge uses r2
        found_r1 = False
        found_r2 = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid == "fuel_active_r1":
                            found_r1 = True
                        if uid == "fuel_active_r2":
                            found_r2 = True
        assert found_r1, "R1 fuel not found"
        assert found_r2, "R2 fuel not found"

    def test_guide_tube_preserved_in_all_states(self):
        """Guide tubes should not be replaced by base path state."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        found_gt = False
        for lat in result.plan.complex_model.lattices:
            if lat.universe_pattern:
                for row in lat.universe_pattern:
                    for uid in row:
                        if uid == "guide_tube":
                            found_gt = True
        assert found_gt
