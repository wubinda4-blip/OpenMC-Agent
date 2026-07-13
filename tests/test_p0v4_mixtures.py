"""Tests for P0-V4: VERA3 Variant-Specific Nozzle and Core-Plate Homogenized Mixtures.

Covers mixture schema, volume-fraction flattening, variant isolation,
layer fill updates, and regression.
"""

from __future__ import annotations

import json
import math
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


# ---------------------------------------------------------------------------
# Source facts
# ---------------------------------------------------------------------------

PITCH = 21.50
SS304_MASS = 6250.0
SS304_DENSITY = 8.00
LOWER_H = 6.053
UPPER_H = 8.827


def _f_lower_ss304() -> float:
    v_ss = SS304_MASS / SS304_DENSITY
    v_total = PITCH ** 2 * LOWER_H
    return v_ss / v_total


def _f_upper_ss304() -> float:
    v_ss = SS304_MASS / SS304_DENSITY
    v_total = PITCH ** 2 * UPPER_H
    return v_ss / v_total


# ---------------------------------------------------------------------------
# 1-3: Mixture fractions sum to 1
# ---------------------------------------------------------------------------


class TestMixtureFractions:
    def test_fractions_sum_to_1(self):
        model = _load_model("3a")
        for mat in model.materials:
            if mat.is_mixture:
                assert len(mat.mixture_component_ids) == len(mat.mixture_volume_fractions)
                total = sum(mat.mixture_volume_fractions)
                assert total == pytest.approx(1.0, abs=1e-6)

    def test_no_duplicate_components(self):
        model = _load_model("3a")
        for mat in model.materials:
            if mat.is_mixture:
                ids = mat.mixture_component_ids
                assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# 4-6: Formula correctness
# ---------------------------------------------------------------------------


class TestFormulaCorrectness:
    def test_lower_nozzle_fraction(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "lower_nozzle_mixture_3a")
        ss_frac = mat.mixture_volume_fractions[0]  # ss304 is first component
        assert ss_frac == pytest.approx(_f_lower_ss304(), rel=1e-8)
        assert ss_frac == pytest.approx(0.2792173729, rel=1e-6)

    def test_upper_nozzle_fraction(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "upper_nozzle_mixture_3a")
        ss_frac = mat.mixture_volume_fractions[0]
        assert ss_frac == pytest.approx(_f_upper_ss304(), rel=1e-8)
        assert ss_frac == pytest.approx(0.1914696679, rel=1e-6)

    def test_core_plate_50_50(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "core_plate_mixture_3a")
        assert mat.mixture_volume_fractions[0] == pytest.approx(0.5)
        assert mat.mixture_volume_fractions[1] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 7-9: Layer fills
# ---------------------------------------------------------------------------


class TestLayerFills:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_lower_nozzle_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "lower_nozzle")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"lower_nozzle_mixture_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_upper_nozzle_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "upper_nozzle")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"upper_nozzle_mixture_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_lower_core_plate_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "lower_core_plate")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"core_plate_mixture_{variant}"

    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_upper_core_plate_fill(self, variant):
        model = _load_model(variant)
        layer = next(l for l in model.core.axial_layers if l.id == "upper_core_plate")
        assert layer.fill.type == "material"
        assert layer.fill.id == f"core_plate_mixture_{variant}"

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
    def test_mixture_ids_differ(self):
        model_a = _load_model("3a")
        model_b = _load_model("3b")
        ids_a = {m.id for m in model_a.materials if m.is_mixture}
        ids_b = {m.id for m in model_b.materials if m.is_mixture}
        assert ids_a != ids_b
        assert ids_a.isdisjoint(ids_b)

    def test_no_shared_mixture_ids(self):
        model_a = _load_model("3a")
        model_b = _load_model("3b")
        ids_a = {m.id for m in model_a.materials if m.is_mixture}
        ids_b = {m.id for m in model_b.materials if m.is_mixture}
        # No 3A mixture should appear in 3B
        for mid in ids_a:
            assert mid not in ids_b

    def test_3a_has_3_mixtures(self):
        model = _load_model("3a")
        mixtures = [m for m in model.materials if m.is_mixture]
        assert len(mixtures) == 3

    def test_3b_has_3_mixtures(self):
        model = _load_model("3b")
        mixtures = [m for m in model.materials if m.is_mixture]
        assert len(mixtures) == 3


# ---------------------------------------------------------------------------
# 12: Composition status
# ---------------------------------------------------------------------------


class TestCompositionStatus:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_mixture_not_confirmed(self, variant):
        model = _load_model(variant)
        for mat in model.materials:
            if mat.is_mixture:
                # Derived mixtures must not be "confirmed"
                assumptions_text = " ".join(mat.assumptions)
                assert "mixture" in assumptions_text.lower()


# ---------------------------------------------------------------------------
# 13: Mass conservation
# ---------------------------------------------------------------------------


class TestMassConservation:
    def test_lower_nozzle_mass_reconstruction(self):
        """Reconstruct SS304 mass from volume fraction and verify."""
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "lower_nozzle_mixture_3a")
        f_ss = mat.mixture_volume_fractions[0]
        rho_ss = 8.0
        v_total = PITCH ** 2 * LOWER_H
        recon_mass = f_ss * rho_ss * v_total
        assert recon_mass == pytest.approx(SS304_MASS, rel=1e-8)

    def test_upper_nozzle_mass_reconstruction(self):
        model = _load_model("3a")
        mat = next(m for m in model.materials if m.id == "upper_nozzle_mixture_3a")
        f_ss = mat.mixture_volume_fractions[0]
        rho_ss = 8.0
        v_total = PITCH ** 2 * UPPER_H
        recon_mass = f_ss * rho_ss * v_total
        assert recon_mass == pytest.approx(SS304_MASS, rel=1e-8)


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
# 15: Mixture flattening
# ---------------------------------------------------------------------------


class TestMixtureFlattening:
    def test_flatten_density_positive(self):
        from openmc_agent.executor import _flatten_volume_mixture, _ACTIVE_MATERIALS_BY_ID
        model = _load_model("3a")
        global _ACTIVE_MATERIALS_BY_ID
        from openmc_agent import executor
        executor._ACTIVE_MATERIALS_BY_ID = {m.id: m for m in model.materials}

        mat = next(m for m in model.materials if m.id == "lower_nozzle_mixture_3a")
        components = [
            (executor._ACTIVE_MATERIALS_BY_ID[cid], frac)
            for cid, frac in zip(mat.mixture_component_ids, mat.mixture_volume_fractions)
        ]
        composition, rho = executor._flatten_volume_mixture(components)
        assert rho > 0
        assert rho < SS304_DENSITY  # should be between water (0.743) and SS304 (8.0)
        assert rho > 0.743
        assert len(composition) > 0
        # All fractions should sum to ~1
        total = sum(f for _, f, _ in composition)
        assert total == pytest.approx(1.0, abs=1e-6)
