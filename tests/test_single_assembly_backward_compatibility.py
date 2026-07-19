"""Tests for single-assembly backward compatibility (P2-FULLCORE-1).

Verifies that the single-assembly path (PinMapPatch, no catalog/layout)
continues to work unchanged.
"""

from openmc_agent.plan_builder.patches import (
    FactsPatch,
    PinMapPatch,
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    lift_single_pin_map_to_catalog,
)
from openmc_agent.plan_builder.scoped_counts import normalize_scoped_counts


def test_single_assembly_facts_default_scope():
    """Phase 8C Step 0: schema default must be ``unknown`` to avoid silently
    locking the single-assembly patch family when the LLM omits the field.
    Single-assembly scope must come from source evidence or the planning
    feature contract, not from a Python default.
    """
    facts = FactsPatch()
    assert facts.model_scope == "unknown"


def test_single_assembly_legacy_counts_normalized():
    """Legacy counts on single_assembly should normalize to pin_map scope."""
    facts = FactsPatch(
        model_scope="single_assembly",
        expected_pin_count=264,
        expected_guide_tube_count=24,
    )
    counts = normalize_scoped_counts(facts)
    assert len(counts) == 2
    assert all(c.scope == "pin_map" for c in counts)


def test_single_pin_map_lift_preserves_coords():
    """Lifting a single pin_map preserves all coordinate groups."""
    pin_map = PinMapPatch(
        lattice_size=(17, 17),
        default_universe_id="fuel",
        guide_tube_coords=[(0, 0), (16, 16)],
        instrument_tube_coords=[(8, 8)],
    )
    catalog = lift_single_pin_map_to_catalog(pin_map)
    pm = catalog.assembly_types[0].pin_map
    assert pm.lattice_size == (17, 17)
    assert pm.default_universe_id == "fuel"
    assert len(pm.guide_tube_coords) == 2
    assert len(pm.instrument_tube_coords) == 1


def test_single_pin_map_lift_preserves_inserts():
    """Lifting preserves localized insert intents."""
    from openmc_agent.plan_builder.patches import LocalizedInsertIntentPatchItem
    pin_map = PinMapPatch(
        lattice_size=(17, 17),
        default_universe_id="fuel",
        guide_tube_coords=[(4, 4)],
        localized_insert_intents=[
            LocalizedInsertIntentPatchItem(
                insert_id="py1",
                insert_kind="pyrex_rod",
                insert_universe_id="pyrex",
                coordinates=[(4, 4)],
            ),
        ],
    )
    catalog = lift_single_pin_map_to_catalog(pin_map)
    pm = catalog.assembly_types[0].pin_map
    assert len(pm.localized_insert_intents) == 1
    assert pm.localized_insert_intents[0].insert_id == "py1"


def test_lifted_catalog_has_auto_marker():
    """Lifted catalog should be marked as auto-lifted."""
    pin_map = PinMapPatch(lattice_size=(3, 3), default_universe_id="fuel")
    catalog = lift_single_pin_map_to_catalog(pin_map)
    assert "auto_lifted_from_single_pin_map" in catalog.assumptions


def test_single_assembly_does_not_require_core_layout():
    """Single-assembly models should not require assembly_catalog/core_layout."""
    from openmc_agent.plan_builder.state import create_initial_component_tasks
    feature_summary = {
        "has_axial_geometry": True,
        "has_spacer_grid": True,
        "has_special_pin_map": True,
    }
    tasks = create_initial_component_tasks(feature_summary)
    task_types = [t.patch_type for t in tasks]
    assert "pin_map" in task_types
    assert "assembly_catalog" not in task_types
    assert "core_layout" not in task_types


def test_multi_assembly_includes_catalog_and_layout():
    """Multi-assembly models should include catalog and layout tasks."""
    from openmc_agent.plan_builder.state import create_initial_component_tasks
    feature_summary = {
        "has_axial_geometry": True,
        "multi_assembly_core": True,
    }
    tasks = create_initial_component_tasks(feature_summary)
    task_types = [t.patch_type for t in tasks]
    assert "assembly_catalog" in task_types
    assert "core_layout" in task_types
    assert "pin_map" not in task_types  # replaced by catalog
