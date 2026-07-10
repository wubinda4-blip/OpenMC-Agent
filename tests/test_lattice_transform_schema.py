"""Tests for lattice transformation schema extensions (Commit 1).

Covers:
- LatticeTransformationOperation validation per operation_kind.
- LatticeLoadingSpec backward compatibility (overrides → transformations).
- AxialLayerSpec loading_id / loading_ids migration.
- CellSpec component identity fields.
- Patch schema (LatticeTransformationPatchItem, loading_ids).
"""

from __future__ import annotations

import pytest

from openmc_agent.schemas import (
    AxialLayerSpec,
    CellSpec,
    FillRefSpec,
    LatticeLoadingSpec,
    LatticeTransformationOperation,
)
from openmc_agent.plan_builder.patches import (
    AxialLayerPatchItem,
    LatticeLoadingPatchItem,
    LatticeTransformationPatchItem,
)


# ---------------------------------------------------------------------------
# LatticeTransformationOperation validation
# ---------------------------------------------------------------------------


class TestOperationValidation:
    def test_family_replacement_requires_source(self):
        with pytest.raises(Exception, match="source_universe_id"):
            LatticeTransformationOperation(
                operation_id="f1",
                operation_kind="replace_universe_family",
                replacement_universe_id="plenum",
            )

    def test_family_replacement_rejects_coordinates(self):
        with pytest.raises(Exception, match="target_coordinates"):
            LatticeTransformationOperation(
                operation_id="f1",
                operation_kind="replace_universe_family",
                replacement_universe_id="plenum",
                source_universe_id="fuel_pin",
                target_coordinates=[(1, 2)],
            )

    def test_family_replacement_with_source_ids(self):
        op = LatticeTransformationOperation(
            operation_id="f1",
            operation_kind="replace_universe_family",
            replacement_universe_id="plenum",
            source_universe_ids=["fuel_a", "fuel_b"],
        )
        assert op.source_universe_ids == ["fuel_a", "fuel_b"]

    def test_coordinate_override_requires_coordinates(self):
        with pytest.raises(Exception, match="target_coordinates"):
            LatticeTransformationOperation(
                operation_id="c1",
                operation_kind="coordinate_override",
                replacement_universe_id="guide",
            )

    def test_nested_requires_component_or_path(self):
        with pytest.raises(Exception, match="component_role"):
            LatticeTransformationOperation(
                operation_id="n1",
                operation_kind="nested_component_override",
                replacement_universe_id="insert",
                target_coordinates=[(1, 2)],
            )

    def test_nested_with_component_role(self):
        op = LatticeTransformationOperation(
            operation_id="n1",
            operation_kind="nested_component_override",
            replacement_universe_id="insert",
            target_coordinates=[(1, 2)],
            component_role="inner_flow",
            preserve_component_roles=["tube_wall"],
        )
        assert op.component_role == "inner_flow"
        assert op.preserve_component_roles == ["tube_wall"]


# ---------------------------------------------------------------------------
# LatticeLoadingSpec backward compatibility
# ---------------------------------------------------------------------------


class TestLatticeLoadingSpecCompat:
    def test_legacy_overrides_loadable(self):
        loading = LatticeLoadingSpec(
            id="loading_1",
            base_lattice_id="assembly_lattice",
            overrides={"pyrex": [(1, 2), (3, 4)]},
        )
        assert loading.overrides == {"pyrex": [(1, 2), (3, 4)]}
        assert loading.transformations == []

    def test_transformations_and_overrides_coexist(self):
        op = LatticeTransformationOperation(
            operation_id="f1",
            operation_kind="replace_universe_family",
            replacement_universe_id="plenum",
            source_universe_id="fuel",
        )
        loading = LatticeLoadingSpec(
            id="loading_1",
            base_lattice_id="assembly_lattice",
            transformations=[op],
            overrides={"pyrex": [(1, 2)]},
        )
        assert len(loading.transformations) == 1
        # overrides still present for migration
        assert loading.overrides == {"pyrex": [(1, 2)]}

    def test_none_overrides_coerced(self):
        loading = LatticeLoadingSpec(
            id="loading_1",
            base_lattice_id="assembly_lattice",
            overrides=None,
        )
        assert loading.overrides == {}


# ---------------------------------------------------------------------------
# AxialLayerSpec loading_id / loading_ids
# ---------------------------------------------------------------------------


class TestAxialLayerLoadingIds:
    def test_loading_ids_stored(self):
        layer = _layer(loading_ids=["a", "b"])
        assert layer.loading_ids == ["a", "b"]
        assert layer.loading_id is None

    def test_legacy_loading_id_stored(self):
        layer = _layer(loading_id="old")
        assert layer.loading_ids == []
        assert layer.loading_id == "old"

    def test_both_stored(self):
        layer = _layer(loading_id="first", loading_ids=["first", "second"])
        assert layer.loading_id == "first"
        assert layer.loading_ids == ["first", "second"]


def _layer(**kwargs):
    return AxialLayerSpec(
        id="test_layer",
        name="test",
        z_min_cm=0.0,
        z_max_cm=1.0,
        fill=FillRefSpec(type="lattice", id="assembly_lattice"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# CellSpec component identity
# ---------------------------------------------------------------------------


class TestCellSpecComponentIdentity:
    def test_component_role_default_none(self):
        cell = CellSpec(id="c1", name="fuel", fill_type="material", fill_id="uo2")
        assert cell.component_role is None
        assert cell.protected_through_path is False

    def test_component_role_set(self):
        cell = CellSpec(
            id="c1", name="wall", fill_type="material", fill_id="zr4",
            component_role="tube_wall", protected_through_path=True,
        )
        assert cell.component_role == "tube_wall"
        assert cell.protected_through_path is True


# ---------------------------------------------------------------------------
# Patch schema
# ---------------------------------------------------------------------------


class TestPatchSchemaExtensions:
    def test_transformation_patch_item(self):
        item = LatticeTransformationPatchItem(
            operation_id="f1",
            operation_kind="replace_universe_family",
            replacement_universe_id="plenum",
            source_universe_id="fuel",
        )
        assert item.operation_kind == "replace_universe_family"

    def test_loading_patch_with_transformations(self):
        loading = LatticeLoadingPatchItem(
            loading_id="l1",
            base_lattice_id="assembly_lattice",
            transformations=[
                LatticeTransformationPatchItem(
                    operation_id="f1",
                    operation_kind="replace_universe_family",
                    replacement_universe_id="plenum",
                    source_universe_id="fuel",
                ),
            ],
        )
        assert len(loading.transformations) == 1

    def test_layer_patch_loading_ids(self):
        layer = AxialLayerPatchItem(
            layer_id="active",
            role="active_fuel",
            fill_type="lattice",
            fill_id="assembly_lattice",
            loading_ids=["a", "b"],
        )
        assert layer.loading_ids == ["a", "b"]

    def test_layer_patch_legacy_loading_id(self):
        layer = AxialLayerPatchItem(
            layer_id="active",
            role="active_fuel",
            fill_type="lattice",
            fill_id="assembly_lattice",
            loading_id="old",
        )
        assert layer.loading_id == "old"
        assert layer.loading_ids == []
