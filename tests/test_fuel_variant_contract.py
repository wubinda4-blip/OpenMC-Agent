"""Tests for the reactor-neutral fuel variant source contract (P2-FULLCORE-2D-B).

Covers:
- Patch schema: FuelVariantRequirementPatchItem, source_variant_id, fuel_variant_id
- Validators: materials, universes, assembly_catalog fuel variant checks
- Prompt contract: universes, assembly_catalog, materials, facts prompts
- Retry routing: fuel variant error codes route to correct dependency

Reactor-neutral — uses generic type IDs and variant IDs unless explicitly
testing VERA4-specific acceptance.
"""

from __future__ import annotations

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    FactsPatch,
    FuelVariantRequirementPatchItem,
    MaterialsPatch,
    MaterialSpecPatch,
    UniversesPatch,
    UniverseSpecPatch,
    CellLayerPatch,
    parse_patch_content,
)
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)
from openmc_agent.plan_builder.patch_prompts import _PATCH_RULES
from unittest.mock import MagicMock

from openmc_agent.plan_builder.executor import route_retry, RetryDecision


def _mock_state(valid_types: list[str] | None = None) -> MagicMock:
    state = MagicMock()
    state.patches = {}
    if valid_types:
        for pt in valid_types:
            env = MagicMock()
            env.patch_type = pt
            env.status = "valid"
            state.patches[f"patch_{pt}_0"] = env
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx_with_fuel_variants(**extra) -> PatchValidationContext:
    return PatchValidationContext(
        fuel_variant_requirements=[
            {"variant_id": "var_a", "enrichment_wt_percent": 2.0,
             "assembly_type_ids": ["type_a"]},
            {"variant_id": "var_b", "enrichment_wt_percent": 3.5,
             "assembly_type_ids": ["type_b"]},
        ],
        **extra,
    )


def _fuel_material(mid: str, variant_id: str | None = None,
                   enrichment: float = 2.0) -> dict:
    comp = {"U235": enrichment, "U238": 100.0 - enrichment, "O16": 2.0}
    return {
        "material_id": mid, "name": f"fuel {mid}", "role": "fuel",
        "density_g_cm3": 10.0, "composition": comp,
        "composition_basis": "stoichiometric_ratio",
        "composition_status": "confirmed",
        "source_variant_id": variant_id,
    }


def _structural_material(mid: str, role: str = "structural") -> dict:
    return {
        "material_id": mid, "name": mid, "role": role,
        "density_g_cm3": 7.0, "composition": {"Fe": 100.0},
        "composition_basis": "weight_frac",
        "composition_status": "approximate",
    }


def _fuel_universe(uid: str, mat_id: str) -> dict:
    return {
        "universe_id": uid, "kind": "fuel_pin",
        "cells": [
            {"id": "fuel", "role": "fuel", "material_id": mat_id,
             "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4},
        ],
    }


def _guide_tube_universe(uid: str) -> dict:
    return {
        "universe_id": uid, "kind": "guide_tube",
        "cells": [
            {"id": "inner", "role": "coolant", "material_id": "water",
             "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.5},
            {"id": "wall", "role": "structure", "material_id": "zr4",
             "region_kind": "cylinder", "r_min_cm": 0.5, "r_max_cm": 0.6},
        ],
    }


# ---------------------------------------------------------------------------
# A. Correct binding → pass
# ---------------------------------------------------------------------------

def test_correct_two_variant_binding_passes():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
        universe_summaries=[
            {"universe_id": "fuel_pin_a", "fuel_variant_ids": ["var_a"]},
            {"universe_id": "fuel_pin_b", "fuel_variant_ids": ["var_b"]},
        ],
    )
    mats = parse_patch_content("materials", {
        "patch_type": "materials",
        "materials": [_fuel_material("fuel_a", "var_a"), _fuel_material("fuel_b", "var_b")],
    })
    result = validate_patch(mats, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert len(errs) == 0, [i.message for i in errs]


# ---------------------------------------------------------------------------
# B. Two fuel variants, but universes only has variant A → fail
# ---------------------------------------------------------------------------

def test_missing_variant_in_universes_fails():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
    )
    univs = parse_patch_content("universes", {
        "patch_type": "universes",
        "universes": [_fuel_universe("fuel_pin_a", "fuel_a")],
    })
    result = validate_patch(univs, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("var_b" in i.message and "reachable" in i.message for i in errs), \
        [i.message for i in errs]
# ---------------------------------------------------------------------------

def test_fuel_material_unreachable_fails():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
    )
    univs = parse_patch_content("universes", {
        "patch_type": "universes",
        "universes": [
            _fuel_universe("fuel_pin_a", "fuel_a"),
            _fuel_universe("fuel_pin_b", "fuel_a"),
        ],
    })
    result = validate_patch(univs, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("var_b" in i.message for i in errs), [i.message for i in errs]


# ---------------------------------------------------------------------------
# D. Two assembly types wrongly sharing same fuel universe → fail
# ---------------------------------------------------------------------------

def test_distinct_fuel_variants_collapsed_fails():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
        ],
        universe_summaries=[
            {"universe_id": "fuel_pin_a", "fuel_variant_ids": ["var_a"]},
        ],
    )
    ac = parse_patch_content("assembly_catalog", {
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {"assembly_type_id": "type_a", "fuel_variant_id": "var_a",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fuel_pin_a"}},
            {"assembly_type_id": "type_b", "fuel_variant_id": "var_b",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fuel_pin_a"}},
        ],
    })
    result = validate_patch(ac, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("collapsed" in i.message.lower() or "expected" in i.message.lower() for i in errs), \
        [i.message for i in errs]


# ---------------------------------------------------------------------------
# E. Same variant → sharing is OK
# ---------------------------------------------------------------------------

def test_same_variant_sharing_universe_passes():
    ctx = PatchValidationContext(
        fuel_variant_requirements=[
            {"variant_id": "var_a", "assembly_type_ids": ["type_a", "type_b"]},
        ],
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
        ],
        universe_summaries=[
            {"universe_id": "fuel_pin_a", "fuel_variant_ids": ["var_a"]},
        ],
    )
    ac = parse_patch_content("assembly_catalog", {
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {"assembly_type_id": "type_a", "fuel_variant_id": "var_a",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fuel_pin_a"}},
            {"assembly_type_id": "type_b", "fuel_variant_id": "var_a",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fuel_pin_a"}},
        ],
    })
    result = validate_patch(ac, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert len(errs) == 0, [i.message for i in errs]


# ---------------------------------------------------------------------------
# F. fuel_variant_id mismatch with default universe → fail
# ---------------------------------------------------------------------------

def test_fuel_variant_mismatch_with_universe_fails():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
        universe_summaries=[
            {"universe_id": "fuel_pin_a", "fuel_variant_ids": ["var_a"]},
            {"universe_id": "fuel_pin_b", "fuel_variant_ids": ["var_b"]},
        ],
    )
    ac = parse_patch_content("assembly_catalog", {
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {"assembly_type_id": "type_a", "fuel_variant_id": "var_b",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fuel_pin_a"}},
        ],
    })
    result = validate_patch(ac, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("not match" in i.message.lower() or "expected" in i.message.lower() for i in errs), \
        [i.message for i in errs]


# ---------------------------------------------------------------------------
# G. Universe with two fuel materials from different variants → fail
# ---------------------------------------------------------------------------

def test_universe_with_multiple_fuel_variants_fails():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
    )
    univs = parse_patch_content("universes", {
        "patch_type": "universes",
        "universes": [
            {"universe_id": "bad_universe", "kind": "fuel_pin",
             "cells": [
                 {"id": "f1", "role": "fuel", "material_id": "fuel_a",
                  "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.3},
                 {"id": "f2", "role": "fuel", "material_id": "fuel_b",
                  "region_kind": "cylinder", "r_min_cm": 0.3, "r_max_cm": 0.4},
             ]},
        ],
    })
    result = validate_patch(univs, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("multiple" in i.message.lower() and "variant" in i.message.lower() for i in errs), \
        [i.message for i in errs]


# ---------------------------------------------------------------------------
# H-J: Additional checks
# ---------------------------------------------------------------------------

def test_required_fuel_variant_missing_in_materials_fails():
    ctx = _ctx_with_fuel_variants()
    mats = parse_patch_content("materials", {
        "patch_type": "materials",
        "materials": [_fuel_material("fuel_a", "var_a")],
    })
    result = validate_patch(mats, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("var_b" in i.message for i in errs)


def test_fuel_variant_collapsed_in_universes_detected():
    ctx = _ctx_with_fuel_variants(
        material_summaries=[
            {"material_id": "fuel_a", "role": "fuel", "source_variant_id": "var_a"},
            {"material_id": "fuel_b", "role": "fuel", "source_variant_id": "var_b"},
        ],
    )
    univs = parse_patch_content("universes", {
        "patch_type": "universes",
        "universes": [
            _fuel_universe("fuel_pin", "fuel_a"),
        ],
    })
    result = validate_patch(univs, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("collapsed" in i.message.lower() for i in errs), [i.message for i in errs]


def test_material_source_variant_id_not_in_facts_fails():
    ctx = _ctx_with_fuel_variants()
    mats = parse_patch_content("materials", {
        "patch_type": "materials",
        "materials": [_fuel_material("fuel_x", "var_unknown")],
    })
    result = validate_patch(mats, ctx)
    errs = [i for i in result.issues if i.severity == "error"]
    assert any("var_unknown" in i.message for i in errs)


# ---------------------------------------------------------------------------
# Prompt regression tests
# ---------------------------------------------------------------------------

def test_universes_prompt_mentions_distinct_variants():
    rules = _PATCH_RULES["universes"]
    assert "distinct" in rules.lower() and "fuel" in rules.lower()
    assert "variant" in rules.lower()


def test_assembly_catalog_prompt_no_unconditional_sharing():
    rules = _PATCH_RULES["assembly_catalog"]
    assert "CAN share universe IDs ONLY when" in rules
    assert "MUST NOT share" in rules


def test_assembly_catalog_prompt_has_fuel_variant_example():
    rules = _PATCH_RULES["assembly_catalog"]
    assert "fuel_variant_id" in rules
    assert "fuel_pin_low" in rules
    assert "fuel_pin_high" in rules


def test_materials_prompt_mentions_source_variant_id():
    rules = _PATCH_RULES["materials"]
    assert "source_variant_id" in rules


def test_facts_prompt_mentions_fuel_variant_requirements():
    rules = _PATCH_RULES["facts"]
    assert "fuel_variant_requirements" in rules


# ---------------------------------------------------------------------------
# Retry routing tests
# ---------------------------------------------------------------------------

def test_retry_routes_fuel_variant_missing_to_facts():
    decision = route_retry(
        failed_patch_type="materials",
        issues=[{"code": "materials.required_fuel_variant_missing", "severity": "error"}],
        state=_mock_state(),
    )
    assert decision.action == "retry_dependency_patch"
    assert decision.dependency_patch_type == "facts"


def test_retry_routes_universe_unreachable_to_materials():
    decision = route_retry(
        failed_patch_type="universes",
        issues=[{"code": "universes.fuel_material_unreachable", "severity": "error"}],
        state=_mock_state(),
    )
    assert decision.action == "retry_dependency_patch"
    assert decision.dependency_patch_type == "materials"


def test_retry_routes_assembly_mismatch_to_universes():
    decision = route_retry(
        failed_patch_type="assembly_catalog",
        issues=[{"code": "assembly_catalog.fuel_material_mismatch", "severity": "error"}],
        state=_mock_state(),
    )
    assert decision.action == "retry_dependency_patch"
    assert decision.dependency_patch_type == "universes"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_fuel_variant_requirement_schema():
    fv = FuelVariantRequirementPatchItem(
        variant_id="test_var",
        enrichment_wt_percent=3.5,
        assembly_type_ids=["a", "b"],
        expected_assembly_count=3,
    )
    assert fv.variant_id == "test_var"
    assert fv.enrichment_wt_percent == 3.5


def test_facts_patch_accepts_fuel_variant_requirements():
    facts = parse_patch_content("facts", {
        "patch_type": "facts",
        "fuel_variant_requirements": [
            {"variant_id": "v1", "enrichment_wt_percent": 2.0},
        ],
    })
    assert len(facts.fuel_variant_requirements) == 1
    assert facts.fuel_variant_requirements[0].variant_id == "v1"


def test_material_with_source_variant_id():
    mat = parse_patch_content("materials", {
        "patch_type": "materials",
        "materials": [_fuel_material("m1", "v1")],
    })
    assert mat.materials[0].source_variant_id == "v1"


def test_assembly_type_with_fuel_variant_id():
    ac = parse_patch_content("assembly_catalog", {
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {"assembly_type_id": "t1", "fuel_variant_id": "v1",
             "pin_map": {"lattice_size": [3, 3], "default_universe_id": "fp"}},
        ],
    })
    assert ac.assembly_types[0].fuel_variant_id == "v1"
