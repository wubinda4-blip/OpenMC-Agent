"""Tests for nested component override Method B (bounded universe fill).

Covers (task spec test items):
- nested target cell uses universe fill
- parent region preserved
- replacement background bounded by parent region
- base guide wall still exists
- base outer moderator still exists
- base objects unchanged
- nested universe cycle rejected
"""
from __future__ import annotations

import pytest

from openmc_agent.lattice_transform import (
    compose_lattice_loadings,
)
from openmc_agent.schemas import (
    CellSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    LatticeTransformationOperation,
    UniverseSpec,
)


def _guide_tube_universe() -> tuple[list[CellSpec], UniverseSpec]:
    cells = [
        CellSpec(
            id="gt_inner_water", name="inner water",
            fill_type="material", fill_id="water",
            region_id="reg_gt_inner",
            component_role="inner_flow",
        ),
        CellSpec(
            id="gt_wall", name="guide tube wall",
            fill_type="material", fill_id="zr4",
            region_id="reg_gt_wall",
            component_role="tube_wall",
            protected_through_path=True,
        ),
        CellSpec(
            id="gt_outer_water", name="outer moderator",
            fill_type="material", fill_id="water",
            region_id="reg_gt_outer",
            component_role="outer_moderator",
        ),
    ]
    universe = UniverseSpec(
        id="guide_tube", name="guide tube",
        cell_ids=["gt_inner_water", "gt_wall", "gt_outer_water"],
    )
    return cells, universe


def _insert_universe(uid: str = "poison_insert") -> tuple[list[CellSpec], UniverseSpec]:
    cells = [
        CellSpec(id=f"{uid}_solid", name="poison", fill_type="material", fill_id="poison",
                 component_role="poison"),
        CellSpec(id=f"{uid}_bg", name="background", fill_type="material", fill_id="water",
                 component_role="inner_flow_background"),
    ]
    universe = UniverseSpec(id=uid, name="poison insert", cell_ids=[f"{uid}_solid", f"{uid}_bg"])
    return cells, universe


def _lattice(uid: str = "guide_tube", size: int = 3) -> LatticeSpec:
    return LatticeSpec(
        id="base", name="base", kind="rect",
        pitch_cm=(1.26, 1.26),
        universe_pattern=[[uid] * size for _ in range(size)],
    )


def _nested_op(op_id: str, repl: str, coords: list[tuple[int, int]]) -> LatticeTransformationOperation:
    return LatticeTransformationOperation(
        operation_id=op_id,
        operation_kind="nested_component_override",
        replacement_universe_id=repl,
        target_coordinates=coords,
        component_role="inner_flow",
        preserve_component_roles=["tube_wall", "outer_moderator"],
    )


class TestNestedMethodB:
    def test_nested_target_cell_uses_universe_fill(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(1, 1)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result.ok
        assert len(result.derived_cells) == 3
        fill_cell = next(dc for dc in result.derived_cells if dc.fill_type == "universe")
        assert fill_cell.fill_id == "poison_insert"

    def test_parent_region_preserved(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(1, 1)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result.ok
        cloned = next(dc for dc in result.derived_cells if dc.fill_type == "universe")
        assert cloned.region_id == "reg_gt_inner"

    def test_base_guide_wall_preserved(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(0, 0)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result.ok
        derived = result.derived_universes[0]
        assert any("gt_wall" in cell_id for cell_id in derived.cell_ids)

    def test_base_outer_moderator_preserved(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(2, 2)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result.ok
        derived = result.derived_universes[0]
        assert any("gt_outer_water" in cell_id for cell_id in derived.cell_ids)

    def test_base_objects_unchanged(self):
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(1, 1)])],
        )
        import copy
        gt_univ_before = copy.deepcopy(gt_univ)
        gt_cells_before = copy.deepcopy(gt_cells)

        compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert gt_univ == gt_univ_before
        assert gt_cells == gt_cells_before

    def test_no_replacement_cells_in_derived_universe(self):
        """Method B must NOT add replacement universe cells directly — only the
        cloned fill cell. The replacement is bounded by the parent cell region."""
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(1, 1)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result.ok
        derived = result.derived_universes[0]
        # poison_insert_solid must NOT appear as a cell in the derived universe
        assert "poison_insert_solid" not in derived.cell_ids
        assert "poison_insert_bg" not in derived.cell_ids

    def test_nested_universe_cycle_rejected(self):
        """Replacement universe must not be a derived universe."""
        gt_cells, gt_univ = _guide_tube_universe()
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice()
        # First create a derived universe
        loading1 = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(0, 0)])],
        )
        result1 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading1},
            universes=[gt_univ, ins_univ],
            cells=gt_cells + ins_cells,
        )
        assert result1.ok
        derived_uid = result1.derived_universes[0].id

        # Now try to use the derived universe as a replacement
        loading2 = LatticeLoadingSpec(
            id="L2", base_lattice_id="base",
            transformations=[LatticeTransformationOperation(
                operation_id="n2",
                operation_kind="nested_component_override",
                replacement_universe_id=derived_uid,
                target_coordinates=[(2, 2)],
                component_role="inner_flow",
                preserve_component_roles=["tube_wall", "outer_moderator"],
            )],
        )
        result2 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L2"],
            loading_by_id={"L2": loading2},
            universes=[gt_univ, ins_univ, result1.derived_universes[0]],
            cells=gt_cells + ins_cells + result1.derived_cells,
        )
        codes = [i.code for i in result2.issues]
        assert "lattice_transform.nested_universe_cycle" in codes

    def test_nested_fill_region_missing_rejected(self):
        """Target cell without region_id must be rejected — Method B requires a region."""
        cells = [
            CellSpec(id="no_region", name="inner", fill_type="material", fill_id="water",
                     component_role="inner_flow"),
            CellSpec(id="wall", name="wall", fill_type="material", fill_id="zr4",
                     component_role="tube_wall", protected_through_path=True),
        ]
        universe = UniverseSpec(id="test_u", name="test", cell_ids=["no_region", "wall"])
        ins_cells, ins_univ = _insert_universe()
        lattice = _lattice("test_u")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "poison_insert", [(0, 0)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[universe, ins_univ],
            cells=cells + ins_cells,
        )
        codes = [i.code for i in result.issues]
        assert "lattice_transform.nested_fill_region_missing" in codes

    def test_nested_replacement_empty_rejected(self):
        """Replacement universe with no cells must be rejected."""
        gt_cells, gt_univ = _guide_tube_universe()
        empty_univ = UniverseSpec(id="empty", name="empty", cell_ids=[])
        lattice = _lattice()
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[_nested_op("n1", "empty", [(0, 0)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=[gt_univ, empty_univ],
            cells=gt_cells,
        )
        codes = [i.code for i in result.issues]
        assert "lattice_transform.nested_replacement_empty" in codes
