"""Tests for the material composition policy and resolver integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.material_policy import (
    DEFAULT_MATERIAL_POLICY,
    MaterialCompositionPolicy,
    apply_policy_to_material_patch,
    build_composition_report,
    evaluate_material_policy,
    policy_from_value,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import MaterialSpecPatch, MaterialsPatch


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _patch(
    material_id: str,
    *,
    name: str = "",
    role: str = "",
    composition: dict[str, float] | None = None,
    composition_status: str = "approximate",
    composition_basis: str = "weight_frac",
) -> MaterialSpecPatch:
    return MaterialSpecPatch(
        material_id=material_id,
        name=name or material_id,
        role=role or "structural",
        density_g_cm3=7.0,
        composition=composition if composition is not None else {"Zr": 1.0},
        composition_basis=composition_basis,
        composition_status=composition_status,
    )


# ---------------------------------------------------------------------------
# Default policy and parsing
# ---------------------------------------------------------------------------


def test_default_policy_is_apply_alloy_library() -> None:
    assert DEFAULT_MATERIAL_POLICY == MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY


def test_policy_from_value_accepts_string() -> None:
    assert policy_from_value("preserve_plan") == MaterialCompositionPolicy.PRESERVE_PLAN
    assert policy_from_value("apply_alloy_library") == MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY
    assert policy_from_value("strict_confirmed_only") == MaterialCompositionPolicy.STRICT_CONFIRMED_ONLY


def test_policy_from_value_accepts_none() -> None:
    assert policy_from_value(None) == DEFAULT_MATERIAL_POLICY


def test_policy_from_value_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        policy_from_value("bogus_policy")


# ---------------------------------------------------------------------------
# Per-material decisions
# ---------------------------------------------------------------------------


def test_pure_zr_zircaloy_gets_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="zircaloy4",
        name="Zircaloy-4",
        role="cladding",
        composition={"Zr": 1.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert decision.apply_library
    assert decision.alloy_id == "zircaloy4"
    assert decision.alloy is not None
    assert {"Sn", "Cr", "Fe", "O"}.issubset(set(decision.alloy.elements.keys()))


def test_pure_fe_ss304_gets_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="ss304",
        name="SS-304",
        role="ss304",
        composition={"Fe": 1.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert decision.apply_library
    assert decision.alloy_id == "ss304"
    assert {"Cr", "Ni"}.issubset(set(decision.alloy.elements.keys()))


def test_pure_ni_inconel_gets_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="inconel718",
        name="Inconel-718",
        role="grid_inconel",
        composition={"Ni": 1.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert decision.apply_library
    assert decision.alloy_id == "inconel718"
    assert {"Cr", "Fe", "Nb", "Mo"}.issubset(set(decision.alloy.elements.keys()))


def test_fuel_not_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="uo2_fuel",
        name="UO2 Fuel",
        role="fuel",
        composition={"U235": 0.03, "U238": 0.97, "O16": 2.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library
    assert decision.issue_code == "materials.alloy_library_skipped_protected"


def test_borated_water_not_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="borated_water",
        name="Borated Water",
        role="coolant",
        composition={"H1": 2.0, "O16": 1.0, "B-10": 1e-5},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library


def test_helium_not_replaced() -> None:
    decision = evaluate_material_policy(
        material_id="helium",
        name="Helium",
        role="helium",
        composition={"He4": 1.0},
        composition_status="confirmed",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library


def test_unknown_alloy_warns_but_does_not_block() -> None:
    decision = evaluate_material_policy(
        material_id="hastelloy_x",
        name="Hastelloy-X",
        role="structural",
        composition={"Ni": 1.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library
    # No exception raised is itself the pass condition; the resolver is silent.


def test_preserve_plan_policy_never_replaces() -> None:
    decision = evaluate_material_policy(
        material_id="zircaloy4",
        name="Zircaloy-4",
        role="cladding",
        composition={"Zr": 1.0},
        composition_status="needs_library",
        policy=MaterialCompositionPolicy.PRESERVE_PLAN,
    )
    assert not decision.apply_library
    assert decision.issue_code == "materials.alloy_library_preserve_plan"


def test_strict_confirmed_only_skips_unless_requested() -> None:
    # Status not requesting library -> skipped.
    decision_skip = evaluate_material_policy(
        material_id="zircaloy4",
        name="Zircaloy-4",
        role="cladding",
        composition={"Zr": 1.0},
        composition_status="confirmed",
        policy=MaterialCompositionPolicy.STRICT_CONFIRMED_ONLY,
    )
    assert not decision_skip.apply_library

    # Status requesting library -> applied.
    decision_apply = evaluate_material_policy(
        material_id="zircaloy4",
        name="Zircaloy-4",
        role="cladding",
        composition={"Zr": 1.0},
        composition_status="needs_library",
        policy=MaterialCompositionPolicy.STRICT_CONFIRMED_ONLY,
    )
    assert decision_apply.apply_library


def test_rich_composition_not_overwritten() -> None:
    """If the plan already provides a multi-element composition, do not clobber."""
    decision = evaluate_material_policy(
        material_id="zircaloy4",
        name="Zircaloy-4",
        role="cladding",
        composition={"Zr": 0.98, "Sn": 0.015, "Fe": 0.0025, "Cr": 0.001, "O": 0.0015},
        composition_status="confirmed",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library
    assert decision.issue_code == "materials.alloy_library_skipped_rich"


# ---------------------------------------------------------------------------
# Patch rewriting
# ---------------------------------------------------------------------------


def test_apply_policy_to_material_patch_rewrites_composition() -> None:
    mat = _patch("zircaloy4", name="Zircaloy-4", role="cladding")
    rewritten, decision = apply_policy_to_material_patch(
        mat, MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert decision.apply_library
    assert set(rewritten.composition.keys()) == {"Zr", "Sn", "Fe", "Cr", "O"}
    assert abs(sum(rewritten.composition.values()) - 1.0) < 1e-8
    assert "alloy_library" in rewritten.source_note or "alloy_library" in "".join(rewritten.warnings)
    # Original patch unchanged.
    assert mat.composition == {"Zr": 1.0}


def test_apply_policy_to_material_patch_preserves_fuel() -> None:
    mat = _patch(
        "uo2_fuel",
        name="UO2 Fuel",
        role="fuel",
        composition={"U235": 0.03, "U238": 0.97, "O16": 2.0},
        composition_status="confirmed",
    )
    rewritten, decision = apply_policy_to_material_patch(
        mat, MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library
    assert rewritten.composition == mat.composition


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def test_material_composition_report_generated() -> None:
    materials = [
        _patch("zircaloy4", name="Zircaloy-4", role="cladding"),
        _patch("uo2_fuel", name="UO2 Fuel", role="fuel",
               composition={"U235": 0.03, "U238": 0.97, "O16": 2.0},
               composition_status="approximate"),
    ]
    report = build_composition_report(
        materials, MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    by_id = {m.material_id: m for m in report.materials}
    assert by_id["zircaloy4"].alloy_library_applied is True
    assert by_id["zircaloy4"].alloy_id == "zircaloy4"
    assert {"Sn", "Cr", "Fe", "O"}.issubset(set(by_id["zircaloy4"].elements.keys()))
    assert by_id["uo2_fuel"].alloy_library_applied is False


def test_report_policy_field_round_trips() -> None:
    report = build_composition_report([], MaterialCompositionPolicy.PRESERVE_PLAN)
    assert report.composition_policy == "preserve_plan"


# ---------------------------------------------------------------------------
# Assembler integration
# ---------------------------------------------------------------------------


def _load_vera3(variant: str) -> list:
    path = FIXTURE_DIR / f"vera3_{variant}_patches.json"
    data = json.loads(path.read_text())
    from openmc_agent.plan_builder.patches import parse_patch_content
    return [parse_patch_content(p["patch_type"], p) for p in data["patches"]]


@pytest.mark.parametrize("variant", ["3a", "3b"])
def test_assembler_apply_alloy_library_preserves_confirmed_compositions(variant: str) -> None:
    """With confirmed multi-element compositions the alloy library is not applied."""
    patches = _load_vera3(variant)
    result = assemble_simulation_plan_from_patches(
        patches, material_policy="apply_alloy_library",
    )
    assert result.ok, [i.message for i in result.issues]
    assert result.material_composition_report is not None
    report = result.material_composition_report
    by_id = {m.material_id: m for m in report.materials}
    for alloy_id in ("zircaloy4", "ss304", "inconel718"):
        assert alloy_id in by_id, f"missing material {alloy_id}"
        entry = by_id[alloy_id]
        # Compositions are already rich (confirmed atom densities); library skipped.
        assert entry.alloy_library_applied is False, f"{alloy_id} should not be applied"
    # Fuel and water untouched.
    fuel_id = f"fuel_{variant}"
    water_id = f"borated_water_{variant}"
    assert by_id[fuel_id].alloy_library_applied is False
    assert by_id[water_id].alloy_library_applied is False


@pytest.mark.parametrize("variant", ["3a", "3b"])
def test_assembler_preserve_plan_keeps_full_compositions(variant: str) -> None:
    patches = _load_vera3(variant)
    result = assemble_simulation_plan_from_patches(
        patches, material_policy="preserve_plan",
    )
    assert result.ok
    by_id = {m.material_id: m for m in result.material_composition_report.materials}
    assert by_id["zircaloy4"].alloy_library_applied is False
    # The new fixtures provide full multi-element compositions (Zr, Sn, Cr, Fe, ...).
    elements = set(by_id["zircaloy4"].elements.keys())
    assert any(e.startswith("Zr") for e in elements)
    assert len(elements) > 1


def test_assembler_default_policy_is_apply_alloy_library() -> None:
    patches = _load_vera3("3a")
    result = assemble_simulation_plan_from_patches(patches)
    assert result.ok
    summary = result.summary
    assert summary["material_composition_policy"] == "apply_alloy_library"
    assert summary["material_composition_report_present"] is True


def test_assembler_no_alloy_applied_for_confirmed_compositions() -> None:
    """With confirmed multi-element compositions, no alloy_library_applied issues emitted."""
    patches = _load_vera3("3a")
    result = assemble_simulation_plan_from_patches(
        patches, material_policy="apply_alloy_library",
    )
    codes = [i.code for i in result.issues]
    # The new fixtures provide full compositions; the library is not applied.
    assert codes.count("materials.alloy_library_applied") == 0


def test_assembler_unknown_material_id_does_not_block() -> None:
    """A material that looks alloy-like but has no entry must not block assembly.

    The assembler needs other required patches, so we test directly against
    the policy resolver instead: it must return ``apply_library=False`` for
    an unknown alloy without raising.
    """
    decision = evaluate_material_policy(
        material_id="hastelloy_x",
        name="Hastelloy-X",
        role="structural",
        composition={"Ni": 1.0},
        composition_status="approximate",
        policy=MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY,
    )
    assert not decision.apply_library
    # Resolver did not raise; that itself is the pass condition.
