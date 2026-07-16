"""Tests for the generic grid geometry materialization validator.

Covers both positive (valid grid injection) and negative (corrupted plan)
scenarios using reactor-neutral synthetic fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synthetic_grid_fixture import (
    build_synthetic_grid_plan,
    build_synthetic_no_grid_plan,
    corrupt_plan_make_identical_digest,
    corrupt_plan_remove_decorated_universes,
    corrupt_plan_remove_ir_merge,
    corrupt_plan_remove_lattice_refs,
    corrupt_plan_remove_material_reachability,
)

from openmc_agent.plan_builder.grid_geometry_validation import (
    PRIMARY_CODE,
    CODE_DECORATED_UNIVERSE_MISSING,
    CODE_LATTICE_REFERENCE_MISSING,
    CODE_FRAME_CELL_MISSING,
    CODE_MATERIAL_UNREACHABLE,
    CODE_DIGEST_UNCHANGED,
    CODE_DANGLING_REFERENCE,
    compute_geometry_structural_digest,
    validate_grid_geometry_materialization,
)


class TestPositiveValidation:
    """Valid grid injection must pass the validator."""

    def test_synthetic_grid_on_passes(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        result = validate_grid_geometry_materialization(plan)
        assert result.ok
        assert len(result.errors) == 0

    def test_no_grid_overlays_passes(self):
        plan = build_synthetic_no_grid_plan()
        result = validate_grid_geometry_materialization(plan)
        assert result.ok
        assert result.summary["active_grid_overlays"] == 0

    def test_grid_on_has_decorated_universes(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        result = validate_grid_geometry_materialization(plan)
        assert result.summary["decorated_universe_count"] > 0

    def test_grid_on_has_frame_cells(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        result = validate_grid_geometry_materialization(plan)
        assert result.summary["frame_cell_count"] > 0

    def test_grid_on_has_lattices_with_grid(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        result = validate_grid_geometry_materialization(plan)
        assert len(result.summary["lattices_with_grid"]) > 0


class TestNegativeFalsePositiveRejection:
    """Each corruption must be detected and fail-closed."""

    def test_A_overlay_exists_but_no_decorated(self):
        """Scenario A: overlay exists but decorated universes are empty."""
        plan = build_synthetic_grid_plan(grid_on=True)
        corrupted = corrupt_plan_remove_decorated_universes(plan)
        result = validate_grid_geometry_materialization(corrupted)
        assert not result.ok
        codes = {i.code for i in result.errors}
        assert PRIMARY_CODE in codes
        assert CODE_DECORATED_UNIVERSE_MISSING in codes

    def test_B_decorated_exists_but_lattice_not_referencing(self):
        """Scenario B: decorated universes exist but no lattice references them."""
        plan = build_synthetic_grid_plan(grid_on=True)
        corrupted = corrupt_plan_remove_lattice_refs(plan)
        result = validate_grid_geometry_materialization(corrupted)
        assert not result.ok
        codes = {i.code for i in result.errors}
        assert CODE_LATTICE_REFERENCE_MISSING in codes

    def test_C_lattice_refs_but_universe_not_in_catalog(self):
        """Scenario C: lattice references decorated ID but universe missing from IR."""
        plan = build_synthetic_grid_plan(grid_on=True)
        corrupted = corrupt_plan_remove_ir_merge(plan)
        result = validate_grid_geometry_materialization(corrupted)
        assert not result.ok
        codes = {i.code for i in result.errors}
        assert CODE_DANGLING_REFERENCE in codes

    def test_D_frame_cell_material_not_in_catalog(self):
        """Scenario D: frame cells exist but grid material removed from catalog."""
        plan = build_synthetic_grid_plan(grid_on=True)
        corrupted = corrupt_plan_remove_material_reachability(plan)
        result = validate_grid_geometry_materialization(corrupted)
        assert not result.ok
        codes = {i.code for i in result.errors}
        assert CODE_MATERIAL_UNREACHABLE in codes

    def test_E_grid_on_grid_off_identical_digest(self):
        """Scenario E: grid-on and grid-off geometry have identical digest."""
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        corrupted = corrupt_plan_make_identical_digest(plan_on)
        result = validate_grid_geometry_materialization(
            corrupted, grid_off_model=plan_off.complex_model,
        )
        assert not result.ok
        codes = {i.code for i in result.errors}
        assert CODE_DIGEST_UNCHANGED in codes


class TestDigestComparison:
    """Structural digest must differ between grid-on and grid-off."""

    def test_digest_differs(self):
        plan_on = build_synthetic_grid_plan(grid_on=True)
        plan_off = build_synthetic_no_grid_plan()
        d_on = compute_geometry_structural_digest(plan_on.complex_model)
        d_off = compute_geometry_structural_digest(plan_off.complex_model)
        assert d_on != d_off

    def test_digest_deterministic(self):
        plan = build_synthetic_grid_plan(grid_on=True)
        d1 = compute_geometry_structural_digest(plan.complex_model)
        d2 = compute_geometry_structural_digest(plan.complex_model)
        assert d1 == d2


class TestNoFalsePositiveWithoutGrid:
    """Validator must not flag models without grid overlays."""

    def test_skeleton_overlay_not_flagged(self):
        plan = build_synthetic_no_grid_plan()
        result = validate_grid_geometry_materialization(plan)
        assert result.ok
        assert result.summary["active_grid_overlays"] == 0
