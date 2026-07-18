"""Tests for universe requirement extraction."""

from openmc_agent.plan_builder.universe_fragment_generation import (
    UniverseGenerationRequirement,
    UniverseGenerationRequirementSet,
    extract_universe_requirements,
)


class _FakeFacts:
    def __init__(self):
        self.fuel_variant_requirements = [
            type("V", (), {"variant_id": "v1"})(),
            type("V", (), {"variant_id": "v2"})(),
        ]
        self.localized_insert_requirements = [
            type("R", (), {"requirement_id": "r1", "insert_kind": "control_rod"})(),
            type("R", (), {"requirement_id": "r2", "insert_kind": "pyrex_rod"})(),
        ]
        self.planning_feature_contract = type("FC", (), {
            "expected_guide_tube_count": 8,
            "expected_instrument_tube_count": 1,
        })()


class _FakeMaterials:
    def __init__(self):
        self.materials = [
            type("M", (), {"material_id": "m1", "role": "fuel"})(),
            type("M", (), {"material_id": "m2", "role": "structural"})(),
        ]


def test_fuel_variant_requirements_extracted():
    reqs = extract_universe_requirements(facts=_FakeFacts(), materials=_FakeMaterials())
    fuel_reqs = [r for r in reqs.requirements if r.kind == "fuel_pin"]
    assert len(fuel_reqs) == 2
    assert all(r.fuel_variant_id for r in fuel_reqs)


def test_localized_insert_requirements_extracted():
    reqs = extract_universe_requirements(facts=_FakeFacts(), materials=_FakeMaterials())
    insert_reqs = [r for r in reqs.requirements if r.localized_insert_requirement_id]
    assert len(insert_reqs) == 2


def test_guide_tube_implicit_requirement():
    reqs = extract_universe_requirements(facts=_FakeFacts(), materials=_FakeMaterials())
    guide_reqs = [r for r in reqs.requirements if r.kind == "guide_tube"]
    assert len(guide_reqs) == 1


def test_requirement_set_has_input_hash():
    reqs = extract_universe_requirements(facts=_FakeFacts(), materials=_FakeMaterials())
    assert reqs.input_hash
    assert len(reqs.input_hash) > 0


def test_no_benchmark_names_used():
    """Requirement extraction must not use benchmark names."""
    reqs = extract_universe_requirements(facts=_FakeFacts(), materials=_FakeMaterials())
    for r in reqs.requirements:
        assert "vera" not in r.universe_id.lower()
        assert "benchmark" not in r.universe_id.lower()


def test_empty_facts_returns_empty():
    reqs = extract_universe_requirements(facts=None, materials=None)
    assert len(reqs.requirements) == 0
