"""Tests for AssemblyCatalogPatch schema and parsing (P2-FULLCORE-1)."""

import pytest

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    LocalizedInsertIntentPatchItem,
    parse_patch_content,
)


def _make_simple_pin_map():
    return AssemblyPinMapPatchItem(
        lattice_size=(3, 3),
        default_universe_id="fuel_cell",
        guide_tube_coords=[(1, 1)],
    )


def test_assembly_catalog_basic():
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="type_a",
                name="fuel assembly A",
                role="fuel",
                pin_map=_make_simple_pin_map(),
            ),
        ],
    )
    assert len(catalog.assembly_types) == 1
    assert catalog.assembly_types[0].assembly_type_id == "type_a"
    assert catalog.assembly_types[0].pin_map.lattice_size == (3, 3)


def test_assembly_catalog_with_inserts():
    pm = AssemblyPinMapPatchItem(
        lattice_size=(5, 5),
        default_universe_id="fuel_cell",
        guide_tube_coords=[(1, 1), (3, 3)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="pyrex1",
                insert_kind="pyrex_rod",
                host_kind="guide_tube",
                insert_universe_id="pyrex",
                coordinates=[(1, 1)],
                application_mode="coordinate_override",
            ),
        ],
    )
    atype = AssemblyTypePatchItem(
        assembly_type_id="type_b",
        pin_map=pm,
    )
    catalog = AssemblyCatalogPatch(assembly_types=[atype])
    assert len(catalog.assembly_types[0].pin_map.localized_insert_intents) == 1


def test_assembly_catalog_empty_caught_by_validator():
    """Empty assembly_types passes Pydantic but is caught by validator."""
    from openmc_agent.plan_builder.validators import validate_patch
    catalog = AssemblyCatalogPatch(assembly_types=[])
    result = validate_patch(catalog)
    assert not result.ok
    assert any(i.code == "assembly_catalog.empty" for i in result.issues)


def test_assembly_catalog_extra_forbid():
    with pytest.raises(Exception):
        AssemblyCatalogPatch(
            assembly_types=[AssemblyTypePatchItem(
                assembly_type_id="x",
                pin_map=_make_simple_pin_map(),
            )],
            bogus_key=True,
        )


def test_parse_patch_content_assembly_catalog():
    content = {
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {
                "assembly_type_id": "type_a",
                "pin_map": {
                    "lattice_size": [3, 3],
                    "default_universe_id": "fuel",
                    "guide_tube_coords": [[1, 1]],
                },
            },
        ],
    }
    patch = parse_patch_content("assembly_catalog", content)
    assert isinstance(patch, AssemblyCatalogPatch)
    assert patch.assembly_types[0].assembly_type_id == "type_a"
    assert patch.assembly_types[0].pin_map.lattice_size == (3, 3)


def test_assembly_catalog_multiplicity_hint():
    atype = AssemblyTypePatchItem(
        assembly_type_id="type_a",
        multiplicity_hint=4,
        pin_map=_make_simple_pin_map(),
    )
    catalog = AssemblyCatalogPatch(assembly_types=[atype])
    assert catalog.assembly_types[0].multiplicity_hint == 4


def test_assembly_catalog_requires_human_confirmation():
    atype = AssemblyTypePatchItem(
        assembly_type_id="type_a",
        pin_map=_make_simple_pin_map(),
        requires_human_confirmation=True,
    )
    catalog = AssemblyCatalogPatch(assembly_types=[atype])
    assert catalog.assembly_types[0].requires_human_confirmation is True
