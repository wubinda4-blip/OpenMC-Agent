"""Tests for assembly catalog validator (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)


def _make_catalog(**kwargs):
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel_cell",
        guide_tube_coords=[(1, 1)],
    )
    return AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(assembly_type_id="type_a", pin_map=pm, **kwargs),
        ],
    )


def test_valid_assembly_catalog():
    catalog = _make_catalog()
    result = validate_patch(catalog)
    assert result.ok
    assert len(result.issues) == 0


def test_empty_assembly_catalog():
    catalog = AssemblyCatalogPatch(assembly_types=[])
    result = validate_patch(catalog)
    assert not result.ok
    assert any(i.code == "assembly_catalog.empty" for i in result.issues)


def test_duplicate_type_id():
    pm = AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel")
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(assembly_type_id="dup", pin_map=pm),
            AssemblyTypePatchItem(assembly_type_id="dup", pin_map=pm),
        ],
    )
    result = validate_patch(catalog)
    assert not result.ok
    assert any(i.code == "assembly_catalog.duplicate_type_id" for i in result.issues)


def test_universe_missing():
    catalog = _make_catalog()
    ctx = PatchValidationContext(known_universe_ids=["other_universe"])
    result = validate_patch(catalog, ctx)
    assert not result.ok
    assert any(i.code == "assembly_catalog.universe_missing" for i in result.issues)


def test_too_many_special_coords():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(2, 2),
        default_universe_id="fuel",
        guide_tube_coords=[(0, 0), (0, 1), (1, 0), (1, 1), (0, 0)],
    )
    catalog = AssemblyCatalogPatch(
        assembly_types=[AssemblyTypePatchItem(assembly_type_id="x", pin_map=pm)],
    )
    result = validate_patch(catalog)
    # 5 coords for 4 cells → error
    assert any(i.code == "assembly_catalog.local_count_mismatch" for i in result.issues)


def test_insert_universe_missing():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel",
        guide_tube_coords=[(1, 1)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="i1",
                insert_kind="pyrex_rod",
                insert_universe_id="missing_pyrex",
                coordinates=[(1, 1)],
            ),
        ],
    )
    catalog = AssemblyCatalogPatch(
        assembly_types=[AssemblyTypePatchItem(assembly_type_id="x", pin_map=pm)],
    )
    ctx = PatchValidationContext(known_universe_ids=["fuel"])
    result = validate_patch(catalog, ctx)
    assert any(i.code == "assembly_catalog.universe_missing" for i in result.issues)
