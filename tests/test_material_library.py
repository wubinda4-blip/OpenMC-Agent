"""Tests for the controlled alloy composition library."""

from __future__ import annotations

import pytest

from openmc_agent.material_library import (
    ALLOY_COMPOSITIONS,
    WEIGHT_SUM_TOL,
    AlloyComposition,
    canonical_alloy_id,
    get_alloy_composition,
    normalize_weight_fractions,
    register_alloy_composition,
)


REQUIRED_ALLOY_IDS = ["zircaloy4", "ss304", "inconel718"]


def test_registry_contains_required_alloys() -> None:
    for cid in REQUIRED_ALLOY_IDS:
        assert cid in ALLOY_COMPOSITIONS, f"missing canonical alloy {cid!r}"


@pytest.mark.parametrize("cid", REQUIRED_ALLOY_IDS)
def test_composition_sums_to_one(cid: str) -> None:
    entry = ALLOY_COMPOSITIONS[cid]
    total = sum(entry.elements.values())
    assert abs(total - 1.0) < 1e-8, f"{cid} sum {total!r} != 1.0"


@pytest.mark.parametrize("cid", REQUIRED_ALLOY_IDS)
def test_each_alloy_has_source_note(cid: str) -> None:
    entry = ALLOY_COMPOSITIONS[cid]
    assert entry.source_note
    assert "nominal" in entry.source_note.lower() or "approximation" in entry.source_note.lower(), (
        f"{cid} source_note should disclose it is a nominal approximation"
    )


@pytest.mark.parametrize("cid", REQUIRED_ALLOY_IDS)
def test_basis_is_weight_frac(cid: str) -> None:
    assert ALLOY_COMPOSITIONS[cid].basis == "weight_frac"


def test_zircaloy4_has_minor_constituents() -> None:
    elements = ALLOY_COMPOSITIONS["zircaloy4"].elements
    assert elements["Zr"] > 0.9
    assert "Sn" in elements and elements["Sn"] > 0.01
    assert "Cr" in elements and elements["Cr"] > 0
    assert "Fe" in elements and elements["Fe"] > 0


def test_ss304_has_chromium_and_nickel() -> None:
    elements = ALLOY_COMPOSITIONS["ss304"].elements
    assert elements["Fe"] > 0.5
    assert elements["Cr"] > 0.15
    assert elements["Ni"] > 0.05


def test_inconel718_has_nb_and_mo() -> None:
    elements = ALLOY_COMPOSITIONS["inconel718"].elements
    assert elements["Ni"] > 0.4
    assert elements["Cr"] > 0.15
    assert elements["Nb"] > 0.03
    assert elements["Mo"] > 0.02


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("zircaloy4", "zircaloy4"),
        ("zircaloy-4", "zircaloy4"),
        ("zircaloy_4", "zircaloy4"),
        ("zirc4", "zircaloy4"),
        ("grid_zircaloy4", "zircaloy4"),
        ("spacer_zircaloy4", "zircaloy4"),
        ("clad_zircaloy4", "zircaloy4"),
        ("ss304", "ss304"),
        ("ss-304", "ss304"),
        ("stainless_steel_304", "ss304"),
        ("stainless304", "ss304"),
        ("core_plate_ss304", "ss304"),
        ("inconel718", "inconel718"),
        ("inconel-718", "inconel718"),
        ("grid_inconel718", "inconel718"),
        ("spacer_inconel718", "inconel718"),
        ("in718", "inconel718"),
    ],
)
def test_aliases_resolve(alias: str, expected: str) -> None:
    assert canonical_alloy_id(alias) == expected


def test_unknown_alias_returns_none() -> None:
    assert canonical_alloy_id("unknown_alloy_xyz") is None
    assert canonical_alloy_id("") is None


def test_get_alloy_composition_returns_entry() -> None:
    entry = get_alloy_composition("grid_zircaloy4")
    assert entry is not None
    assert entry.alloy_id == "zircaloy4"


def test_get_alloy_composition_unknown_returns_none() -> None:
    assert get_alloy_composition("unobtainium") is None


def test_normalize_weight_fractions_from_percentages() -> None:
    out = normalize_weight_fractions({"Zr": 98.0, "Sn": 2.0})
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["Sn"] == pytest.approx(0.02, abs=1e-6)


def test_normalize_weight_fractions_already_fraction() -> None:
    out = normalize_weight_fractions({"Zr": 0.98, "Sn": 0.02})
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["Zr"] == pytest.approx(0.98, abs=1e-6)


def test_normalize_weight_fractions_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_weight_fractions({})


def test_normalize_weight_fractions_rejects_negative() -> None:
    with pytest.raises(ValueError):
        normalize_weight_fractions({"Zr": 1.2, "Sn": -0.2})


def test_normalize_weight_fractions_rejects_far_from_one() -> None:
    with pytest.raises(ValueError):
        normalize_weight_fractions({"Zr": 0.1, "Sn": 0.1})


def test_register_alloy_composition_replaces_entry() -> None:
    original = ALLOY_COMPOSITIONS.get("zircaloy4")
    try:
        custom = AlloyComposition(
            alloy_id="zircaloy4",
            display_name="Custom Zircaloy-4",
            elements={"Zr": 0.98, "Sn": 0.02},
            source_note="Custom nominal approximation for testing.",
        )
        register_alloy_composition(custom)
        assert ALLOY_COMPOSITIONS["zircaloy4"].display_name == "Custom Zircaloy-4"
        assert canonical_alloy_id("zircaloy4") == "zircaloy4"
    finally:
        if original is not None:
            register_alloy_composition(original)


def test_weight_sum_tol_is_tight() -> None:
    assert WEIGHT_SUM_TOL <= 1e-6
