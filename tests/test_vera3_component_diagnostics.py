"""Deterministic diagnostics for known VERA3 component geometry fixture errors."""

from __future__ import annotations

import json
from pathlib import Path

from helpers.vera3_acceptance import (
    collect_active_lattice_union,
    collect_base_lattice_counts,
    collect_loading_override_counts,
    diagnose_vera3_component_geometry,
    load_vera3_geometry_contract,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content


_FIXTURES = Path("tests/fixtures/vera3_patches")
CONTRACT = load_vera3_geometry_contract()


def _raw_fixture(variant: str) -> dict:
    return json.loads((_FIXTURES / f"vera3_{variant}_patches.json").read_text(encoding="utf-8"))


def _assembled(variant: str):
    raw = _raw_fixture(variant)
    return assemble_simulation_plan_from_patches(
        [parse_patch_content(item["patch_type"], item) for item in raw["patches"]]
    ).plan


def test_3a_component_profiles_use_lattice_fill() -> None:
    """3A fixture migrated to lattice fill with family replacement;
    no material-slab diagnostics should fire."""
    codes = {issue.code for issue in diagnose_vera3_component_geometry(_raw_fixture("3a"), CONTRACT, variant="3A")}
    assert "vera3.component_material_slab" not in codes
    assert "vera3.fuel_pin_profile_missing" not in codes


def test_3b_fixture_diagnoses_remaining_issues() -> None:
    """3B fixture has lattice fill for profiles and fixed pyrex gaps;
    remaining issues are thimble loading and pyrex axial conflict."""
    codes = {issue.code for issue in diagnose_vera3_component_geometry(_raw_fixture("3b"), CONTRACT, variant="3B")}
    # Fixed issues should NOT appear
    assert "vera3.component_material_slab" not in codes
    assert "vera3.fuel_pin_profile_missing" not in codes
    assert "vera3.pyrex_gap_material_mismatch" not in codes
    # Remaining issues SHOULD still appear
    assert "vera3.thimble_loading_missing" in codes
    assert "vera3.pyrex_axial_profile_conflict" in codes


def test_3b_base_lattice_and_finite_pyrex_loading_are_separate() -> None:
    plan = _assembled("3b")
    assert collect_base_lattice_counts(plan) == {"fuel_pin": 264, "guide_tube": 24, "instrument_tube": 1}
    # Pyrex is now a nested_component_override, not legacy overrides
    model = plan.complex_model
    pyrex_loading = next(l for l in model.lattice_loadings if l.id == "pyrex_active_loading")
    nested_ops = [t for t in pyrex_loading.transformations if t.operation_kind == "nested_component_override"]
    assert len(nested_ops) == 1
    assert nested_ops[0].replacement_universe_id == "pyrex_inner_profile"
    assert len(nested_ops[0].target_coordinates) == 16
    # Thimble loading also present with 8 coordinates
    thimble_loading = next(l for l in model.lattice_loadings if l.id == "thimble_plug_loading")
    thimble_ops = [t for t in thimble_loading.transformations if t.operation_kind == "nested_component_override"]
    assert len(thimble_ops) == 1
    assert len(thimble_ops[0].target_coordinates) == 8
    # Pyrex and thimble coordinates must not overlap
    pyrex_coords = set(tuple(c) for c in nested_ops[0].target_coordinates)
    thimble_coords = set(tuple(c) for c in thimble_ops[0].target_coordinates)
    assert pyrex_coords & thimble_coords == set()


def test_active_fuel_split_layers_cover_the_complete_active_region() -> None:
    plan = _assembled("3b")
    assert collect_active_lattice_union(plan) == (11.951, 377.711)
