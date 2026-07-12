"""Tests for P0-V1: VERA3 fuel helium gap and plenum geometry correction."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from openmc_agent.plan_builder.patches import (
    CellLayerPatch,
    UniverseSpecPatch,
    UniversesPatch,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.validators import validate_patch, PatchValidationContext
from openmc_agent.radial_profile_validation import validate_concentric_radial_profile

FIXTURES = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_universes(variant: str) -> list[dict]:
    fname = "vera3_3a_patches.json" if variant == "3A" else "vera3_3b_patches.json"
    data = json.loads((FIXTURES / fname).read_text())
    for patch in data["patches"]:
        if patch["patch_type"] == "universes":
            return patch["universes"]
    return []


def _find_universe(universes: list[dict], uid: str) -> dict | None:
    for u in universes:
        if u["universe_id"] == uid:
            return u
    return None


# -----------------------------------------------------------------------
# Active fuel pin: helium gap must be present
# -----------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fuel_pin_has_helium_gap(variant: str):
    universes = _load_universes(variant)
    fp = _find_universe(universes, "fuel_pin")
    assert fp is not None, "fuel_pin universe not found"
    cell_ids = [c["id"] for c in fp["cells"]]
    assert "helium_gap" in cell_ids, f"helium_gap cell missing in {variant} fuel_pin"


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fuel_pin_fuel_radius(variant: str):
    universes = _load_universes(variant)
    fp = _find_universe(universes, "fuel_pin")
    fuel = next(c for c in fp["cells"] if c["id"] == "fuel")
    assert fuel["r_max_cm"] == pytest.approx(0.4096)


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fuel_pin_gap_radii(variant: str):
    universes = _load_universes(variant)
    fp = _find_universe(universes, "fuel_pin")
    gap = next(c for c in fp["cells"] if c["id"] == "helium_gap")
    assert gap["r_min_cm"] == pytest.approx(0.4096)
    assert gap["r_max_cm"] == pytest.approx(0.418)
    assert gap["material_id"] == "helium"


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fuel_pin_clad_radii(variant: str):
    universes = _load_universes(variant)
    fp = _find_universe(universes, "fuel_pin")
    clad = next(c for c in fp["cells"] if c["id"] == "clad")
    assert clad["r_min_cm"] == pytest.approx(0.418)
    assert clad["r_max_cm"] == pytest.approx(0.475)


# -----------------------------------------------------------------------
# Plenum: helium extends to 0.418, not 0.4096
# -----------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_plenum_gas_radius_is_0_418(variant: str):
    universes = _load_universes(variant)
    plenum = _find_universe(universes, "fuel_pin_plenum")
    assert plenum is not None
    gas = next(c for c in plenum["cells"] if c["id"] == "plenum_gas")
    assert gas["r_max_cm"] == pytest.approx(0.418), \
        f"plenum_gas r_max should be 0.418, got {gas['r_max_cm']}"


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_plenum_clad_inner_is_0_418(variant: str):
    universes = _load_universes(variant)
    plenum = _find_universe(universes, "fuel_pin_plenum")
    clad = next(c for c in plenum["cells"] if c["id"] == "clad")
    assert clad["r_min_cm"] == pytest.approx(0.418), \
        f"plenum clad r_min should be 0.418, got {clad['r_min_cm']}"


# -----------------------------------------------------------------------
# Radial profile continuity (via shared validator)
# -----------------------------------------------------------------------

def _to_cell_patches(cells: list[dict]) -> list[CellLayerPatch]:
    return [CellLayerPatch(**{k: v for k, v in c.items() if k in CellLayerPatch.model_fields}) for c in cells]


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fuel_pin_radial_continuity(variant: str):
    universes = _load_universes(variant)
    fp = _find_universe(universes, "fuel_pin")
    cells = _to_cell_patches(fp["cells"])
    issues = validate_concentric_radial_profile("fuel_pin", cells)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"Radial continuity errors: {[(i.code, i.message) for i in errors]}"


@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_plenum_radial_continuity(variant: str):
    universes = _load_universes(variant)
    plenum = _find_universe(universes, "fuel_pin_plenum")
    cells = _to_cell_patches(plenum["cells"])
    issues = validate_concentric_radial_profile("fuel_pin_plenum", cells)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"Radial continuity errors: {[(i.code, i.message) for i in errors]}"


# -----------------------------------------------------------------------
# Patch-level validation passes for corrected fixtures
# -----------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_fixture_universes_patch_validates_clean(variant: str):
    fname = "vera3_3a_patches.json" if variant == "3A" else "vera3_3b_patches.json"
    data = json.loads((FIXTURES / fname).read_text())
    for patch_data in data["patches"]:
        if patch_data["patch_type"] != "universes":
            continue
        patch = UniversesPatch.model_validate(patch_data)
        result = validate_patch(patch, PatchValidationContext())
        errors = [i for i in result.issues if i.severity == "error"]
        assert errors == [], f"{variant} universes patch has errors: {[(i.code, i.message) for i in errors]}"


# -----------------------------------------------------------------------
# Pin counts preserved
# -----------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["3A", "3B"])
def test_pin_counts_preserved(variant: str):
    from openmc_agent.plan_builder.patches import parse_patch_content
    fname = "vera3_3a_patches.json" if variant == "3A" else "vera3_3b_patches.json"
    raw = json.loads((FIXTURES / fname).read_text())
    patches = [parse_patch_content(e["patch_type"], e) for e in raw["patches"]]
    result = assemble_simulation_plan_from_patches(patches)
    assert result.ok, f"Assembly failed for {variant}: {result.errors}"

    lat = result.plan.complex_model.lattices[0]
    pattern = lat.universe_pattern
    counts: dict[str, int] = {}
    for row in pattern:
        for uid in row:
            counts[uid] = counts.get(uid, 0) + 1
    assert counts.get("fuel_pin", 0) == 264
    assert counts.get("guide_tube", 0) == 24
    assert counts.get("instrument_tube", 0) == 1


# -----------------------------------------------------------------------
# Area calculations
# -----------------------------------------------------------------------

def test_active_fuel_areas():
    """Verify cross-sectional areas for the active fuel pin."""
    r_fuel = 0.4096
    r_gap = 0.418
    r_clad = 0.475

    fuel_area = math.pi * r_fuel ** 2
    gap_area = math.pi * (r_gap ** 2 - r_fuel ** 2)
    clad_area = math.pi * (r_clad ** 2 - r_gap ** 2)

    # Sanity: gap is much smaller than fuel
    assert gap_area < fuel_area * 0.05  # < 5% of fuel
    # Sanity: clad is larger than gap
    assert clad_area > gap_area * 3
    # Known values (approximate)
    assert fuel_area == pytest.approx(0.5271, abs=0.001)
    assert gap_area == pytest.approx(0.0218, abs=0.001)
    assert clad_area == pytest.approx(0.1599, abs=0.001)


def test_plenum_helium_area():
    """Verify plenum helium area is larger than fuel pellet area."""
    r_gap = 0.418
    helium_area = math.pi * r_gap ** 2
    r_fuel = 0.4096
    fuel_area = math.pi * r_fuel ** 2
    assert helium_area > fuel_area  # Plenum gas extends further


# -----------------------------------------------------------------------
# Few-shot assembly_3d also has gap
# -----------------------------------------------------------------------

def test_few_shot_assembly_3d_has_helium_gap():
    p = Path("data/few_shot_cases/assembly_3d_with_spacer_grids/patches/universes.json")
    data = json.loads(p.read_text())
    fp = _find_universe(data["universes"], "fuel_pin")
    assert fp is not None
    cell_ids = [c["id"] for c in fp["cells"]]
    assert "helium_gap" in cell_ids
