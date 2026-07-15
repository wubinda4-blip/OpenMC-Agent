"""Tests for exact spacer-grid frame geometry and mass conservation (P2-FULLCORE-2D-A-HARDENING)."""

from __future__ import annotations

import math

from openmc_agent.plan_builder.axial_state_materializer import (
    _compute_grid_frame_exact,
    _back_calculate_mass,
)


class TestExactGridFormula:
    def test_inconel_end_grid(self):
        """Inconel-718 end grid: 1017g, density 8.19, 289 cells, height 3.866cm."""
        mass = 1017.0
        density = 8.19
        cells = 289
        height = 15.817 - 11.951  # 3.866
        pitch = 1.26

        a_cell, inner_side, ft = _compute_grid_frame_exact(mass, density, cells, height, pitch)

        assert a_cell > 0
        assert 0 < inner_side < pitch
        assert 0 < ft < pitch / 2.0

        # Back-calculate mass
        back_mass = _back_calculate_mass(inner_side, pitch, density, cells, height)
        rel_err = abs(back_mass - mass) / mass
        assert rel_err < 1e-6, f"Mass conservation error: {rel_err}"

    def test_zircaloy_middle_grid(self):
        """Zircaloy-4 middle grid: 875g, density 6.56, 289 cells, height 3.810cm."""
        mass = 875.0
        density = 6.56
        cells = 289
        height = 77.105 - 73.295  # 3.810
        pitch = 1.26

        a_cell, inner_side, ft = _compute_grid_frame_exact(mass, density, cells, height, pitch)

        assert a_cell > 0
        assert 0 < inner_side < pitch

        back_mass = _back_calculate_mass(inner_side, pitch, density, cells, height)
        rel_err = abs(back_mass - mass) / mass
        assert rel_err < 1e-6

    def test_zero_density_returns_zero(self):
        a_cell, inner_side, ft = _compute_grid_frame_exact(500.0, 0.0, 289, 3.0, 1.26)
        assert a_cell == 0.0

    def test_frame_thickness_positive_and_less_than_half_pitch(self):
        """Frame thickness must be in (0, pitch/2)."""
        a_cell, inner_side, ft = _compute_grid_frame_exact(875.0, 6.56, 289, 3.81, 1.26)
        assert ft > 0
        assert ft < 1.26 / 2.0

    def test_exact_formula_not_approximate(self):
        """The exact formula must differ from the old A/(4*pitch) approximation."""
        mass = 1017.0
        density = 8.19
        cells = 289
        height = 3.866
        pitch = 1.26

        a_cell, inner_side, ft_exact = _compute_grid_frame_exact(mass, density, cells, height, pitch)
        ft_approx = a_cell / (4.0 * pitch)

        # The formulas are different (exact uses sqrt, approx uses linear)
        # They should produce different results
        assert abs(ft_exact - ft_approx) > 1e-8

    def test_mass_conservation_all_8_bands(self):
        """All 8 VERA4 grid bands should pass mass conservation."""
        grid_bands = [
            (11.951, 15.817, "inconel718", 8.19, 1017.0),
            (73.295, 77.105, "zircaloy4", 6.56, 875.0),
            (125.495, 129.305, "zircaloy4", 6.56, 875.0),
            (177.695, 181.505, "zircaloy4", 6.56, 875.0),
            (229.895, 233.705, "zircaloy4", 6.56, 875.0),
            (282.095, 285.905, "zircaloy4", 6.56, 875.0),
            (334.295, 338.105, "zircaloy4", 6.56, 875.0),
            (386.267, 390.133, "inconel718", 8.19, 1017.0),
        ]
        for z_min, z_max, mat, density, mass in grid_bands:
            height = z_max - z_min
            a_cell, inner_side, ft = _compute_grid_frame_exact(mass, density, 289, height, 1.26)
            back_mass = _back_calculate_mass(inner_side, 1.26, density, 289, height)
            rel_err = abs(back_mass - mass) / mass
            assert rel_err < 1e-6, f"Band {z_min}-{z_max} mass error: {rel_err}"
