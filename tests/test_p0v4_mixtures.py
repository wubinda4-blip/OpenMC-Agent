"""Tests for P0-V4: VERA3 Variant-Specific Nozzle and Core-Plate Materials.

Covers exact atom-density material schema, variant isolation,
layer fill updates, and regression.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_model(variant: str):
    data = json.loads((FIXTURE_DIR / f"vera3_{variant}_patches.json").read_text())
    patches = [parse_patch_content(e["patch_type"], e) for e in data["patches"]]
    result = assemble_simulation_plan_from_patches(patches)
    return result.plan.complex_model


_HOMOGENIZED_PREFIXES = ["lower_nozzle", "upper_nozzle", "core_plate"]


# ---------------------------------------------------------------------------
# 1-2: Homogenized material composition valid
# ---------------------------------------------------------------------------


class TestHomogenizedMaterialComposition:
    def test_compositions_non_empty(self):
        model = _load_model("3a")
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_3a")
            assert len(mat.composition) > 0

    def test_all_atom_densities_positive(self):
        model = _load_model("3a")
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_3a")
            for nuclide in mat.composition:
                assert nuclide.percent > 0


# ---------------------------------------------------------------------------
# 3-5: Exact atom-density basis
# ---------------------------------------------------------------------------


class TestHomogenizedMaterialBasis:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_density_unit_is_sum(self, variant):
        """Atom-density materials use density_unit='sum' (absolute atom densities)."""
        model = _load_model(variant)
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_{variant}")
            assert mat.density_unit == "sum"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_not_mixture(self, variant):
        """The new materials are exact atom-density materials, not volume-fraction mixtures."""
        model = _load_model(variant)
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_{variant}")
            assert mat.is_mixture is False

    def test_core_plate_has_fewer_nuclides(self):
        """Core plate is a simpler composition (water + SS304 carbon)."""
        model = _load_model("3a")
        cp = next(m for m in model.materials if m.id == "core_plate_3a")
        nozzle = next(m for m in model.materials if m.id == "lower_nozzle_3a")
        assert len(cp.composition) < len(nozzle.composition)


# ---------------------------------------------------------------------------
# 6-9: Layer fills
# ---------------------------------------------------------------------------


class TestLayerFills:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_lower_nozzle_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "lower_nozzle")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"lower_nozzle_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_upper_nozzle_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "upper_nozzle")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"upper_nozzle_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_lower_core_plate_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "lower_core_plate")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"core_plate_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_upper_core_plate_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "upper_core_plate")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"core_plate_{variant}"

    @pytest.mark.parametrize("variant,z_min,z_max", [
        ("3a", -5.0, 0.0), ("3a", 0.0, 6.053),
        ("3a", 397.510, 406.337), ("3a", 406.337, 413.937),
    ])
    def test_z_boundaries_unchanged(self, variant, z_min, z_max):
        model = _load_model(variant)
        layers = model.core.axial_layers
        z_mins = [l.z_min_cm for l in layers]
        z_maxs = [l.z_max_cm for l in layers]
        assert z_min in z_mins
        assert z_max in z_maxs


# ---------------------------------------------------------------------------
# 10-11: Variant isolation
# ---------------------------------------------------------------------------


class TestVariantIsolation:
    def test_homogenized_ids_differ(self):
        model_a = _load_model("3a")
        model_b = _load_model("3b")
        for prefix in _HOMOGENIZED_PREFIXES:
            assert f"{prefix}_3a" in {m.id for m in model_a.materials}
            assert f"{prefix}_3b" in {m.id for m in model_b.materials}
            assert f"{prefix}_3a" not in {m.id for m in model_b.materials}
            assert f"{prefix}_3b" not in {m.id for m in model_a.materials}

    def test_no_shared_variant_specific_ids(self):
        model_a = _load_model("3a")
        model_b = _load_model("3b")
        ids_a = {m.id for m in model_a.materials if m.id.endswith("_3a")}
        ids_b = {m.id for m in model_b.materials if m.id.endswith("_3b")}
        assert ids_a.isdisjoint(ids_b)

    def test_3a_has_3_homogenized_materials(self):
        model = _load_model("3a")
        homogenized = [
            m for m in model.materials
            if m.id.endswith("_3a") and any(p in m.id for p in _HOMOGENIZED_PREFIXES)
        ]
        assert len(homogenized) == 3

    def test_3b_has_3_homogenized_materials(self):
        model = _load_model("3b")
        homogenized = [
            m for m in model.materials
            if m.id.endswith("_3b") and any(p in m.id for p in _HOMOGENIZED_PREFIXES)
        ]
        assert len(homogenized) == 3


# ---------------------------------------------------------------------------
# 12: Composition status — exact materials are confirmed
# ---------------------------------------------------------------------------


class TestCompositionStatus:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_homogenized_not_mixture(self, variant):
        """The new exact materials are not volume-fraction mixtures."""
        model = _load_model(variant)
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_{variant}")
            assert mat.is_mixture is False
            assert len(mat.composition) > 0


# ---------------------------------------------------------------------------
# 13: Material existence and density basis
# ---------------------------------------------------------------------------


class TestMaterialExistence:
    def test_lower_nozzle_exists_with_sum_density(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "lower_nozzle_3a")
        assert mat.density_unit == "sum"
        assert len(mat.composition) > 0

    def test_upper_nozzle_exists_with_sum_density(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "upper_nozzle_3a")
        assert mat.density_unit == "sum"
        assert len(mat.composition) > 0


# ---------------------------------------------------------------------------
# 14: Pin counts preserved
# ---------------------------------------------------------------------------


class TestPinCounts:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_pin_counts(self, variant):
        model = _load_model(variant)
        lattice = model.lattices[0]
        from collections import Counter
        counts = Counter(uid for row in lattice.universe_pattern for uid in row)
        assert counts.get("fuel_pin", 0) == 264
        assert counts.get("guide_tube", 0) == 24
        assert counts.get("instrument_tube", 0) == 1


# ---------------------------------------------------------------------------
# 15: Composition validity (total positive)
# ---------------------------------------------------------------------------


class TestCompositionValidity:
    def test_total_atom_density_positive(self):
        """The total atom density of each homogenized material is positive."""
        model = _load_model("3a")
        for prefix in _HOMOGENIZED_PREFIXES:
            mat = next(m for m in model.materials if m.id == f"{prefix}_3a")
            total = sum(n.percent for n in mat.composition)
            assert total > 0
