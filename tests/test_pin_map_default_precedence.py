from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches, expand_pin_map
from openmc_agent.plan_builder.patches import PinMapPatch, parse_patch_content


def test_explicit_default_wins_over_duplicate_fuel_pin_kind() -> None:
    pin_map = PinMapPatch(lattice_size=(2, 2), default_universe_id="fuel_pin")
    expanded = expand_pin_map(pin_map, universe_ids={"fuel_pin": "fuel_pin_endplug"})
    assert expanded == [["fuel_pin", "fuel_pin"], ["fuel_pin", "fuel_pin"]]


def test_missing_explicit_default_is_an_assembly_error() -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text())
    patches = []
    for payload in raw["patches"]:
        content = dict(payload)
        if content["patch_type"] == "pin_map":
            content["default_universe_id"] = "missing_universe"
        patches.append(parse_patch_content(content["patch_type"], content))
    result = assemble_simulation_plan_from_patches(patches, strict=True)
    assert result.ok is False
    assert any(issue.code == "assembly.pin_map.default_universe_missing" for issue in result.issues)


def test_fixture_base_fuel_count_remains_explicit_default() -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text())
    result = assemble_simulation_plan_from_patches([
        parse_patch_content(payload["patch_type"], payload) for payload in raw["patches"]
    ], strict=True)
    assert result.ok and result.plan is not None
    lattice = result.plan.complex_model.lattices[0]
    assert sum(row.count("fuel_pin") for row in lattice.universe_pattern) == 264
