"""Tests for ModelScope, CountScope, and ScopedExpectedCount schema (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import (
    CountScope,
    FactsPatch,
    ModelScope,
    ScopedExpectedCount,
)


def test_model_scope_values():
    expected = {"single_pin", "single_assembly", "multi_assembly_core", "full_core", "unknown"}
    import typing
    args = typing.get_args(ModelScope)
    assert set(args) == expected


def test_count_scope_values():
    expected = {"pin_cell", "pin_map", "assembly_type", "assembly_instance", "core_total", "unknown"}
    import typing
    args = typing.get_args(CountScope)
    assert set(args) == expected


def test_scoped_expected_count_basic():
    sec = ScopedExpectedCount(role="fuel_pin", value=264, scope="pin_map")
    assert sec.role == "fuel_pin"
    assert sec.value == 264
    assert sec.scope == "pin_map"
    assert sec.derived is False
    assert sec.requires_human_confirmation is False


def test_scoped_expected_count_assembly_type_scope():
    sec = ScopedExpectedCount(
        role="fuel_pin",
        value=250,
        scope="assembly_type",
        assembly_type_id="type_a",
        derived=True,
        derivation="core_total / assembly_count",
    )
    assert sec.assembly_type_id == "type_a"
    assert sec.derived is True
    assert sec.derivation is not None


def test_scoped_expected_count_core_total_scope():
    sec = ScopedExpectedCount(
        role="fuel_pin",
        value=1000,
        scope="core_total",
        provenance_refs=["req:table1"],
    )
    assert sec.scope == "core_total"
    assert sec.provenance_refs == ["req:table1"]


def test_facts_patch_default_model_scope():
    facts = FactsPatch()
    assert facts.model_scope == "single_assembly"


def test_facts_patch_multi_assembly_fields():
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=4,
        core_lattice_size=(2, 2),
        assembly_type_counts={"type_a": 2, "type_b": 2},
        scoped_expected_counts=[
            ScopedExpectedCount(role="fuel_pin", value=1000, scope="core_total"),
        ],
        boundary_scope="radial_outer",
        symmetry_description="quarter_symmetry",
    )
    assert facts.model_scope == "multi_assembly_core"
    assert facts.assembly_count == 4
    assert facts.core_lattice_size == (2, 2)
    assert facts.assembly_type_counts["type_a"] == 2
    assert len(facts.scoped_expected_counts) == 1
    assert facts.boundary_scope == "radial_outer"
    assert facts.symmetry_description == "quarter_symmetry"


def test_facts_patch_legacy_fields_still_present():
    facts = FactsPatch(
        expected_pin_count=264,
        expected_guide_tube_count=24,
    )
    assert facts.expected_pin_count == 264
    assert facts.expected_guide_tube_count == 24


def test_scoped_expected_count_extra_forbid():
    import pytest
    with pytest.raises(Exception):
        ScopedExpectedCount(role="x", value=1, bogus_field=True)
