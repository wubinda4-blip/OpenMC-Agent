"""Canonical VERA3 geometry contract tests (test-only facts)."""

from __future__ import annotations

from helpers.vera3_acceptance import load_vera3_geometry_contract


CONTRACT = load_vera3_geometry_contract()
_STATUSES = {"confirmed", "derived", "conflicting", "missing", "modeling_assumption", "resolved"}


def _facts(value):
    if isinstance(value, dict):
        if "status" in value and "value" in value:
            yield value
        for child in value.values():
            yield from _facts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _facts(child)


def test_contract_loads_and_separates_assembly_zones_from_component_profiles() -> None:
    assert CONTRACT["contract_version"] == 2
    assert CONTRACT["benchmark_id"] == "VERA3"
    zone_ids = {zone["id"] for zone in CONTRACT["assembly_level_zones"]}
    assert not zone_ids & {"lower_end_plug", "active_fuel", "upper_end_plug", "upper_plenum"}
    assert {"fuel_pin", "guide_tube", "instrument_tube", "pyrex_rod", "thimble_plug"} <= set(CONTRACT["component_profiles"])


def test_fact_statuses_have_required_provenance() -> None:
    facts = list(_facts(CONTRACT))
    assert facts
    assert {fact["status"] for fact in facts} <= _STATUSES
    for fact in facts:
        if fact["status"] == "confirmed":
            assert fact.get("source_section")
        if fact["status"] == "derived":
            assert fact.get("derivation")


def test_conflicts_are_explicit_and_not_confirmed() -> None:
    conflicts = CONTRACT["conflicts"]
    assert conflicts
    assert all(conflict["status"] in ("conflicting", "resolved") for conflict in conflicts)


def test_fuel_profile_is_continuous_and_preserves_outer_semantics() -> None:
    segments = CONTRACT["component_profiles"]["fuel_pin"]["axial_segments"]
    assert all(left["z_max_cm"]["value"] == right["z_min_cm"]["value"] for left, right in zip(segments, segments[1:]))
    assert all("outer_semantics" in segment for segment in segments)


def test_pyrex_stack_is_strictly_increasing_and_helium_gaps_are_confirmed() -> None:
    layers = CONTRACT["component_profiles"]["pyrex_rod"]["radial_layers"]
    assert all(left["r_max_cm"] == right["r_min_cm"] for left, right in zip(layers, layers[1:]))
    assert all(layer["r_max_cm"] > layer["r_min_cm"] for layer in layers)
    assert {layer["id"]: layer["material"] for layer in layers}["gap_1"] == "helium"
    assert {layer["id"]: layer["material"] for layer in layers}["gap_2"] == "helium"


def test_variant_loading_contract_keeps_3b_base_lattice_and_inserts_separate() -> None:
    assert CONTRACT["variant_loadings"]["3A"]["base_lattice"] == {"fuel_pin": 264, "guide_tube": 24, "instrument_tube": 1}
    loading = CONTRACT["variant_loadings"]["3B"]
    assert loading["base_lattice"] == {"fuel_pin": 264, "guide_tube": 24, "instrument_tube": 1}
    inserts = {item["id"]: item for item in loading["finite_inserts"]}
    assert len(inserts["pyrex_poison"]["coordinates_1based"]) == 16
    assert len(inserts["pyrex_upper_gas"]["coordinates_1based"]) == 16
    assert len(inserts["thimble_plug"]["coordinates_1based"]) == 8
    poison_coords = set(tuple(c) for c in inserts["pyrex_poison"]["coordinates_1based"])
    upper_gas_coords = set(tuple(c) for c in inserts["pyrex_upper_gas"]["coordinates_1based"])
    assert poison_coords == upper_gas_coords
