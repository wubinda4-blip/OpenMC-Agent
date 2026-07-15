"""Tests for catalog-layout cross-validation (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.plan_builder.validators import validate_catalog_layout_cross_references


def _make_catalog(type_ids):
    types = []
    for tid in type_ids:
        types.append(AssemblyTypePatchItem(
            assembly_type_id=tid,
            pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
        ))
    return AssemblyCatalogPatch(assembly_types=types)


def test_cross_valid_match():
    catalog = _make_catalog(["a", "b"])
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["a", "b"], ["b", "a"]],
        boundary="reflective",
    )
    result = validate_catalog_layout_cross_references(catalog, layout)
    assert result.ok


def test_cross_valid_unknown_type_in_layout():
    catalog = _make_catalog(["a", "b"])
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pattern=[["c"]],  # "c" not in catalog
        boundary="vacuum",
    )
    result = validate_catalog_layout_cross_references(catalog, layout)
    assert not result.ok
    assert any(i.code == "core_layout.assembly_type_missing" for i in result.issues)


def test_cross_valid_outer_type_missing():
    catalog = _make_catalog(["a"])
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pattern=[["a"]],
        outer_assembly_type_id="reflector",  # not in catalog
        boundary="vacuum",
    )
    result = validate_catalog_layout_cross_references(catalog, layout)
    assert not result.ok
