"""Tests for lattice transformation normalization, composition, and conflicts.

Covers (test IDs from the task spec):
- legacy overrides migration (2, 3)
- loading_id / loading_ids migration (3, 4)
- family replacement (6-9)
- sparse coordinate override (10-13)
- multiple loadings (20-25)
- cache key stability (24, 25)
"""

from __future__ import annotations

import pytest

from openmc_agent.lattice_transform import (
    NormalizedLatticeLoading,
    compose_lattice_loadings,
    compute_cache_key,
    layer_loading_id_conflict,
    normalize_lattice_loading,
    normalized_layer_loading_ids,
)
from openmc_agent.schemas import (
    AxialLayerSpec,
    CellSpec,
    FillRefSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    LatticeTransformationOperation,
    UniverseSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_lattice(size: int = 3, default: str = "fuel") -> LatticeSpec:
    pattern = [[default for _ in range(size)] for _ in range(size)]
    return LatticeSpec(
        id="base", name="base", kind="rect",
        pitch_cm=(1.26, 1.26), universe_pattern=pattern,
    )


def _universes(*ids: str) -> list[UniverseSpec]:
    return [UniverseSpec(id=uid, name=uid, cell_ids=[f"{uid}_cell"]) for uid in ids]


def _cells(*ids: str) -> list[CellSpec]:
    return [CellSpec(id=f"{uid}_cell", name=uid, fill_type="material", fill_id="m") for uid in ids]


def _family_op(op_id: str, src: str, repl: str) -> LatticeTransformationOperation:
    return LatticeTransformationOperation(
        operation_id=op_id,
        operation_kind="replace_universe_family",
        replacement_universe_id=repl,
        source_universe_id=src,
    )


def _coord_op(op_id: str, repl: str, coords: list[tuple[int, int]]) -> LatticeTransformationOperation:
    return LatticeTransformationOperation(
        operation_id=op_id,
        operation_kind="coordinate_override",
        replacement_universe_id=repl,
        target_coordinates=coords,
    )


# ---------------------------------------------------------------------------
# Normalization / migration
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_legacy_overrides_migrated(self):
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            overrides={"pyrex": [(0, 1), (2, 3)]},
        )
        nl = normalize_lattice_loading(loading)
        assert len(nl.operations) == 1
        assert nl.operations[0].operation_kind == "coordinate_override"
        assert nl.operations[0].replacement_universe_id == "pyrex"
        assert len(nl.operations[0].target_coordinates) == 2
        assert len(nl.migration_warnings) == 1

    def test_transformations_preserved(self):
        op = _family_op("f1", "fuel", "plenum")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            transformations=[op],
        )
        nl = normalize_lattice_loading(loading)
        assert len(nl.operations) == 1
        assert nl.operations[0].operation_kind == "replace_universe_family"
        assert nl.migration_warnings == []

    def test_auto_derived_lattice_id(self):
        loading = LatticeLoadingSpec(id="L1", base_lattice_id="base")
        nl = normalize_lattice_loading(loading)
        assert nl.derived_lattice_id == "base__L1"

    def test_explicit_derived_lattice_id_kept(self):
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base",
            derived_lattice_id="custom_derived",
        )
        nl = normalize_lattice_loading(loading)
        assert nl.derived_lattice_id == "custom_derived"


# ---------------------------------------------------------------------------
# loading_id / loading_ids normalization
# ---------------------------------------------------------------------------


class TestLoadingIdsNormalization:
    def test_loading_ids_used(self):
        layer = AxialLayerSpec(
            id="l", name="l", z_min_cm=0, z_max_cm=1,
            fill=FillRefSpec(type="lattice", id="base"),
            loading_ids=["a", "b"],
        )
        assert normalized_layer_loading_ids(layer) == ["a", "b"]

    def test_legacy_loading_id_migrated(self):
        layer = AxialLayerSpec(
            id="l", name="l", z_min_cm=0, z_max_cm=1,
            fill=FillRefSpec(type="lattice", id="base"),
            loading_id="old",
        )
        assert normalized_layer_loading_ids(layer) == ["old"]

    def test_both_empty(self):
        layer = AxialLayerSpec(
            id="l", name="l", z_min_cm=0, z_max_cm=1,
            fill=FillRefSpec(type="lattice", id="base"),
        )
        assert normalized_layer_loading_ids(layer) == []

    def test_conflict_detected(self):
        layer = AxialLayerSpec(
            id="l", name="l", z_min_cm=0, z_max_cm=1,
            fill=FillRefSpec(type="lattice", id="base"),
            loading_id="a", loading_ids=["b", "c"],
        )
        assert layer_loading_id_conflict(layer) is True

    def test_no_conflict_when_consistent(self):
        layer = AxialLayerSpec(
            id="l", name="l", z_min_cm=0, z_max_cm=1,
            fill=FillRefSpec(type="lattice", id="base"),
            loading_id="a", loading_ids=["a", "b"],
        )
        assert layer_loading_id_conflict(layer) is False


# ---------------------------------------------------------------------------
# Family replacement
# ---------------------------------------------------------------------------


class TestFamilyReplacement:
    def test_all_fuel_replaced(self):
        lattice = _base_lattice(3, "fuel")
        lattice.universe_pattern[1][1] = "guide"
        op = _family_op("f1", "fuel", "plenum")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "guide", "plenum"),
            cells=_cells("fuel", "guide", "plenum"),
        )
        assert result.ok
        pattern = result.derived_lattice.universe_pattern
        assert pattern[1][1] == "guide"  # guide unaffected
        assert all(pattern[r][c] == "plenum" for r in range(3) for c in range(3) if (r, c) != (1, 1))

    def test_no_enumeration_needed(self):
        lattice = _base_lattice(17, "fuel")
        op = _family_op("f1", "fuel", "plenum")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "plenum"),
            cells=_cells("fuel", "plenum"),
        )
        assert result.ok
        pattern = result.derived_lattice.universe_pattern
        assert all(pattern[r][c] == "plenum" for r in range(17) for c in range(17))

    def test_source_not_found_warning(self):
        lattice = _base_lattice(3, "fuel")
        op = _family_op("f1", "nonexistent", "plenum")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "nonexistent", "plenum"),
            cells=_cells("fuel", "nonexistent", "plenum"),
        )
        assert result.ok  # warning, not error
        codes = [i.code for i in result.issues]
        assert "lattice_transform.family_replacement_no_match" in codes


# ---------------------------------------------------------------------------
# Coordinate override
# ---------------------------------------------------------------------------


class TestCoordinateOverride:
    def test_sparse_replacement(self):
        lattice = _base_lattice(3, "fuel")
        op = _coord_op("c1", "pyrex", [(0, 1), (2, 2)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "pyrex"),
            cells=_cells("fuel", "pyrex"),
        )
        assert result.ok
        pattern = result.derived_lattice.universe_pattern
        assert pattern[0][1] == "pyrex"
        assert pattern[2][2] == "pyrex"
        assert pattern[0][0] == "fuel"

    def test_oob_rejected(self):
        lattice = _base_lattice(3, "fuel")
        op = _coord_op("c1", "pyrex", [(5, 5)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "pyrex"),
            cells=_cells("fuel", "pyrex"),
        )
        assert not result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.coordinate_oob" in codes

    def test_duplicate_coordinate_warned(self):
        lattice = _base_lattice(3, "fuel")
        op = _coord_op("c1", "pyrex", [(0, 0), (0, 0)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "pyrex"),
            cells=_cells("fuel", "pyrex"),
        )
        assert result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.duplicate_coordinate" in codes

    def test_same_priority_conflict_rejected(self):
        lattice = _base_lattice(3, "fuel")
        op1 = _coord_op("c1", "pyrex", [(0, 0)])
        op2 = _coord_op("c2", "plug", [(0, 0)])
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op1, op2],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "pyrex", "plug"),
            cells=_cells("fuel", "pyrex", "plug"),
        )
        assert not result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.coordinate_conflict" in codes


# ---------------------------------------------------------------------------
# Multiple loadings
# ---------------------------------------------------------------------------


class TestMultipleLoadings:
    def test_family_plus_coordinate_compose(self):
        lattice = _base_lattice(3, "fuel")
        lattice.universe_pattern[1][1] = "guide"
        family = LatticeLoadingSpec(
            id="family", base_lattice_id="base",
            transformations=[_family_op("f1", "fuel", "plenum")],
        )
        pyrex = LatticeLoadingSpec(
            id="pyrex", base_lattice_id="base",
            transformations=[_coord_op("c1", "insert", [(1, 1)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["family", "pyrex"],
            loading_by_id={"family": family, "pyrex": pyrex},
            universes=_universes("fuel", "guide", "plenum", "insert"),
            cells=_cells("fuel", "guide", "plenum", "insert"),
        )
        assert result.ok
        pattern = result.derived_lattice.universe_pattern
        assert pattern[1][1] == "insert"  # coordinate override wins

    def test_non_overlapping_coordinates_compose(self):
        lattice = _base_lattice(3, "fuel")
        loading_a = LatticeLoadingSpec(
            id="A", base_lattice_id="base",
            transformations=[_coord_op("a1", "pyrex", [(0, 0)])],
        )
        loading_b = LatticeLoadingSpec(
            id="B", base_lattice_id="base",
            transformations=[_coord_op("b1", "plug", [(2, 2)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["A", "B"],
            loading_by_id={"A": loading_a, "B": loading_b},
            universes=_universes("fuel", "pyrex", "plug"),
            cells=_cells("fuel", "pyrex", "plug"),
        )
        assert result.ok
        pattern = result.derived_lattice.universe_pattern
        assert pattern[0][0] == "pyrex"
        assert pattern[2][2] == "plug"

    def test_overlapping_coordinates_conflict(self):
        lattice = _base_lattice(3, "fuel")
        loading_a = LatticeLoadingSpec(
            id="A", base_lattice_id="base",
            transformations=[_coord_op("a1", "pyrex", [(1, 1)])],
        )
        loading_b = LatticeLoadingSpec(
            id="B", base_lattice_id="base",
            transformations=[_coord_op("b1", "plug", [(1, 1)])],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["A", "B"],
            loading_by_id={"A": loading_a, "B": loading_b},
            universes=_universes("fuel", "pyrex", "plug"),
            cells=_cells("fuel", "pyrex", "plug"),
        )
        assert not result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.coordinate_conflict" in codes

    def test_base_lattice_mismatch_rejected(self):
        lattice = _base_lattice(3, "fuel")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="different_base",
            transformations=[_family_op("f1", "fuel", "plenum")],
        )
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "plenum"),
            cells=_cells("fuel", "plenum"),
        )
        assert not result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.base_lattice_mismatch" in codes

    def test_loading_ref_missing(self):
        lattice = _base_lattice(3, "fuel")
        result = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["nonexistent"],
            loading_by_id={},
            universes=_universes("fuel"),
            cells=_cells("fuel"),
        )
        assert not result.ok
        codes = [i.code for i in result.issues]
        assert "lattice_transform.loading_ref_missing" in codes


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_same_content_same_key(self):
        lattice = _base_lattice(3, "fuel")
        op = _family_op("f1", "fuel", "plenum")
        loading = LatticeLoadingSpec(
            id="L1", base_lattice_id="base", transformations=[op],
        )
        r1 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "plenum"),
            cells=_cells("fuel", "plenum"),
        )
        r2 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["L1"],
            loading_by_id={"L1": loading},
            universes=_universes("fuel", "plenum"),
            cells=_cells("fuel", "plenum"),
        )
        assert r1.cache_key == r2.cache_key

    def test_different_order_different_key(self):
        lattice = _base_lattice(3, "fuel")
        loading_a = LatticeLoadingSpec(
            id="A", base_lattice_id="base",
            transformations=[_coord_op("a1", "pyrex", [(0, 0)])],
        )
        loading_b = LatticeLoadingSpec(
            id="B", base_lattice_id="base",
            transformations=[_coord_op("b1", "plug", [(2, 2)])],
        )
        r1 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["A", "B"],
            loading_by_id={"A": loading_a, "B": loading_b},
            universes=_universes("fuel", "pyrex", "plug"),
            cells=_cells("fuel", "pyrex", "plug"),
        )
        r2 = compose_lattice_loadings(
            base_lattice=lattice, loading_ids=["B", "A"],
            loading_by_id={"A": loading_a, "B": loading_b},
            universes=_universes("fuel", "pyrex", "plug"),
            cells=_cells("fuel", "pyrex", "plug"),
        )
        assert r1.cache_key != r2.cache_key
