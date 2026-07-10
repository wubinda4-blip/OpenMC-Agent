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


def test_3a_legacy_component_slabs_are_diagnosed() -> None:
    codes = {issue.code for issue in diagnose_vera3_component_geometry(_raw_fixture("3a"), CONTRACT, variant="3A")}
    assert {"vera3.component_material_slab", "vera3.fuel_pin_profile_missing"} <= codes


def test_3b_fixture_diagnoses_component_pyrex_and_thimble_errors() -> None:
    codes = {issue.code for issue in diagnose_vera3_component_geometry(_raw_fixture("3b"), CONTRACT, variant="3B")}
    assert {"vera3.component_material_slab", "vera3.fuel_pin_profile_missing", "vera3.pyrex_radial_stack_mismatch", "vera3.pyrex_gap_material_mismatch", "vera3.thimble_loading_missing", "vera3.pyrex_axial_profile_conflict"} <= codes


def test_3b_base_lattice_and_finite_pyrex_loading_are_separate() -> None:
    plan = _assembled("3b")
    assert collect_base_lattice_counts(plan) == {"fuel_pin": 264, "guide_tube": 24, "instrument_tube": 1}
    assert collect_loading_override_counts(plan, "pyrex_active_loading") == {"pyrex_rod": 16}


def test_active_fuel_split_layers_cover_the_complete_active_region() -> None:
    plan = _assembled("3b")
    assert collect_active_lattice_union(plan) == (11.951, 377.711)
