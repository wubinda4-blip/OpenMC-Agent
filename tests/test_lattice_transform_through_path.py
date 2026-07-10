"""Tests for nested component override and through-path preservation (Commit 3).

Covers (test IDs from the task spec):
- guide tube inner component replaced (14)
- guide wall preserved (15)
- outer moderator preserved (16)
- protected path deletion rejected (17)
- target component not found rejected (18)
- target component ambiguous rejected (19)
"""

from __future__ import annotations

import pytest

from openmc_agent.lattice_transform import (
    compose_lattice_loadings,
    validate_through_path_preservation,
)
from openmc_agent.schemas import (
    CellSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    LatticeTransformationOperation,
    UniverseSpec,
)


# ---------------------------------------------------------------------------
# Helpers — build a guide-tube-like universe with component roles
# ---------------------------------------------------------------------------


def _guide_tube_universe() -> tuple[list[CellSpec], UniverseSpec]:
    cells = [
        CellSpec(
            id="gt_inner_water", name="inner water",
            fill_type="material", fill_id="water",
            component_role="inner_flow",
        ),
        CellSpec(
            id="gt_wall", name="guide tube wall",
            fill_type="material", fill_id="zr4",
            component_role="tube_wall",
            protected_through_path=True,
        ),
        CellSpec(
            id="gt_outer_water", name="outer moderator",
            fill_type="material", fill_id="water",
            component_role="outer_moderator",
        ),
    ]
    universe = UniverseSpec(
        id="guide_tube", name="guide tube",
        cell_ids=["gt_inner_water", "gt_wall", "gt_outer_water"],
    )
    return cells, universe


def _insert_universe() -> tuple[list[CellSpec], UniverseSpec]:
    cells = [
        CellSpec(
            id="insert_solid", name="poison insert",
            fill_type="material", fill_id="poison",
            component_role="insert",
        ),
    ]
    universe = UniverseSpec(
        id="poison_insert", name="poison insert",
        cell_ids=["insert_solid"],
    )
    return cells, universe


def _fuel_universe() -> tuple[list[CellSpec], UniverseSpec]:
    cells = [
        CellSpec(id="fuel_pellet", name="fuel", fill_type="material", fill_id="uo2",
                 component_role="fuel_internal"),
        CellSpec(id="fuel_coolant", name="coolant", fill_type="material", fill_id="water",
                 component_role="outer_moderator"),
    ]
    universe = UniverseSpec(id="fuel_pin", name="fuel", cell_ids=["fuel_pellet", "fuel_coolant"])
    return cells, universe


def _3x3_lattice(uid: str = "fuel_pin") -> LatticeSpec:
    return LatticeSpec(
        id="base", name="base", kind="rect",
        pitch_cm=(1.26, 1.26),
        universe_pattern=[[uid] * 3 for _ in range(3)],
    )


def _nested_op(op_id: str, repl: str, coords: list[tuple[int, int]],
               role: str = "inner_flow",
               preserve: list[str] | None = None) -> LatticeTransformationOperation:
    return LatticeTransformationOperation(
        operation_id=op_id,
        operation_kind="nested_component_override",
        replacement_universe_id=repl,
        target_coordinates=coords,
        component_role=role,
        preserve_component_roles=preserve or ["tube_wall", "outer_moderator"],
    )


# ---------------------------------------------------------------------------
# Nested override: basic behavior
# ---------------------------------------------------------------------------


class TestNestedOverride:
    def test_inner_component_replaced_wall_preserved(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        fuel_cells, fuel_univ = _fuel_universe()

        lattice = _3x3_lattice("guide_tube")
        op = _nested_op("n1", "poison_insert", [(1, 1)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ, fuel_univ],
            cells=gt_cells + ins_cells + fuel_cells,
        )
        assert result.ok
        # A derived universe was created
        assert len(result.derived_universes) == 1
        derived = result.derived_universes[0]
        # Wall cell preserved
        assert "gt_wall" in derived.cell_ids
        # Inner water removed, insert added
        assert "gt_inner_water" not in derived.cell_ids
        assert "insert_solid" in derived.cell_ids
        # Outer moderator preserved
        assert "gt_outer_water" in derived.cell_ids
        # Lattice position points to derived universe
        pattern = result.derived_lattice.universe_pattern
        assert pattern[1][1].startswith("guide_tube__nested_")

    def test_outer_moderator_preserved(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        fuel_cells, fuel_univ = _fuel_universe()

        lattice = _3x3_lattice("guide_tube")
        op = _nested_op("n1", "poison_insert", [(0, 0), (2, 2)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ, fuel_univ],
            cells=gt_cells + ins_cells + fuel_cells,
        )
        assert result.ok
        for du in result.derived_universes:
            assert "gt_outer_water" in du.cell_ids
            assert "gt_wall" in du.cell_ids


class TestNestedOverrideValidation:
    def test_protected_path_removal_rejected(self):
        """If preserve_component_roles does not list tube_wall but it's protected,
        the override should flag the issue."""
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()

        lattice = _3x3_lattice("guide_tube")
        op = LatticeTransformationOperation(
            operation_id="n1",
            operation_kind="nested_component_override",
            replacement_universe_id="poison_insert",
            target_coordinates=[(1, 1)],
            component_role="inner_flow",
            preserve_component_roles=[],  # NOT preserving tube_wall!
        )
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        codes = [i.code for i in result.issues]
        assert "lattice_transform.protected_path_removed" in codes

    def test_target_component_not_found_rejected(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()

        lattice = _3x3_lattice("guide_tube")
        op = _nested_op("n1", "poison_insert", [(1, 1)], role="nonexistent_role")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        codes = [i.code for i in result.issues]
        assert "lattice_transform.component_target_missing" in codes

    def test_target_component_ambiguous_rejected(self):
        """Two cells with the same component_role → ambiguous."""
        cells = [
            CellSpec(id="c1", name="a", fill_type="material", fill_id="water",
                     component_role="inner_flow"),
            CellSpec(id="c2", name="b", fill_type="material", fill_id="water",
                     component_role="inner_flow"),
            CellSpec(id="c3", name="wall", fill_type="material", fill_id="zr4",
                     component_role="tube_wall", protected_through_path=True),
        ]
        universe = UniverseSpec(id="ambiguous", name="amb", cell_ids=["c1", "c2", "c3"])
        ins_cells, ins_univ = _insert_universe()

        lattice = LatticeSpec(
            id="base", name="base", kind="rect", pitch_cm=(1.26, 1.26),
            universe_pattern=[["ambiguous"]],
        )
        op = _nested_op("n1", "poison_insert", [(0, 0)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[universe, ins_univ],
            cells=cells + ins_cells,
        )
        codes = [i.code for i in result.issues]
        assert "lattice_transform.component_target_ambiguous" in codes


# ---------------------------------------------------------------------------
# validate_through_path_preservation standalone
# ---------------------------------------------------------------------------


class TestThroughPathValidator:
    def test_preserved_roles_present(self):
        gt_cells, gt_univ = _guide_tube_universe()
        cells_by_id = {c.id: c for c in gt_cells}
        issues = validate_through_path_preservation(
            base_universe=gt_univ,
            derived_universe=gt_univ,
            preserve_component_roles=["tube_wall", "outer_moderator"],
            preserve_path_ids=[],
            cells_by_id=cells_by_id,
        )
        assert issues == []

    def test_protected_cell_removed(self):
        gt_cells, gt_univ = _guide_tube_universe()
        cells_by_id = {c.id: c for c in gt_cells}
        derived = UniverseSpec(
            id="derived", name="d",
            cell_ids=["gt_inner_water", "gt_outer_water"],  # wall removed!
        )
        issues = validate_through_path_preservation(
            base_universe=gt_univ,
            derived_universe=derived,
            preserve_component_roles=["outer_moderator"],
            preserve_path_ids=[],
            cells_by_id=cells_by_id,
        )
        codes = [i.code for i in issues]
        assert "lattice_transform.protected_path_removed" in codes

    def test_preserved_role_missing(self):
        gt_cells, gt_univ = _guide_tube_universe()
        cells_by_id = {c.id: c for c in gt_cells}
        derived = UniverseSpec(
            id="derived", name="d",
            cell_ids=["gt_inner_water", "gt_wall"],  # outer_water removed!
        )
        issues = validate_through_path_preservation(
            base_universe=gt_univ,
            derived_universe=derived,
            preserve_component_roles=["tube_wall", "outer_moderator"],
            preserve_path_ids=[],
            cells_by_id=cells_by_id,
        )
        codes = [i.code for i in issues]
        assert "lattice_transform.preserved_component_missing" in codes
