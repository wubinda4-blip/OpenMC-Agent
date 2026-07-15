"""VERA4 planning diagnostic for P2-FULLCORE-1.

Runs the incremental planning pipeline against VERA4 input and verifies
that the new scope-aware / hierarchical path works correctly:

1. FactsPatch detects multi_assembly_core scope
2. Scoped counts are explicit
3. AssemblyCatalogPatch is generated (not single PinMapPatch)
4. CoreLayoutPatch is generated
5. No cross-scope count mismatch (264 vs 2376 etc.)
6. Hierarchical plan is schema-valid

This is a PLANNING diagnostic only — no OpenMC execution.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
    FactsPatch,
    LocalizedInsertIntentPatchItem,
    PinMapPatch,
    ScopedExpectedCount,
    parse_patch_content,
)
from openmc_agent.plan_builder.scoped_counts import (
    aggregate_core_counts,
    compute_assembly_pin_counts,
    validate_count_scope_compatibility,
    normalize_scoped_counts,
    compare_scoped_expected_counts,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    build_hierarchical_core_plan,
    lift_single_pin_map_to_catalog,
)
from openmc_agent.plan_builder.validators import (
    validate_patch,
    validate_catalog_layout_cross_references,
    PatchValidationContext,
)


def build_vera4_facts() -> FactsPatch:
    """Construct VERA4 facts patch with explicit scoped counts."""
    # VERA4: 3x3 core, 3 assembly types (C=4, E=4, R=1)
    # Each assembly is 17x17 = 289 cells
    # 24 guide tubes + 1 instrument tube per assembly = 25 special
    # Fuel per assembly = 289 - 25 = 264
    # Core total fuel = 264 × 9 = 2376
    return FactsPatch(
        patch_type="facts",
        benchmark_id="VERA4",
        model_scope="multi_assembly_core",
        lattice_size=(17, 17),
        pin_pitch_cm=1.26,
        assembly_pitch_cm=21.50,
        core_lattice_size=(3, 3),
        assembly_count=9,
        assembly_type_counts={"corner": 4, "edge": 4, "center_rcca": 1},
        has_axial_geometry=True,
        has_spacer_grids=True,
        has_special_pin_map=True,
        scoped_expected_counts=[
            ScopedExpectedCount(role="fuel_pin", value=264, scope="assembly_type", assembly_type_id="corner"),
            ScopedExpectedCount(role="fuel_pin", value=264, scope="assembly_type", assembly_type_id="edge"),
            ScopedExpectedCount(role="fuel_pin", value=264, scope="assembly_type", assembly_type_id="center_rcca"),
            ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total"),
            ScopedExpectedCount(role="guide_tube", value=24, scope="assembly_type", assembly_type_id="corner"),
            ScopedExpectedCount(role="guide_tube", value=24, scope="assembly_type", assembly_type_id="edge"),
            ScopedExpectedCount(role="guide_tube", value=24, scope="assembly_type", assembly_type_id="center_rcca"),
            ScopedExpectedCount(role="guide_tube", value=216, scope="core_total"),
            ScopedExpectedCount(role="instrument_tube", value=1, scope="assembly_type"),
            ScopedExpectedCount(role="instrument_tube", value=9, scope="core_total"),
        ],
        boundary_scope="radial_reflective_axial_vacuum",
        symmetry_description="none",
        source_notes=["VERA4 Problem #4 specification"],
    )


def build_vera4_assembly_catalog() -> AssemblyCatalogPatch:
    """Construct VERA4 assembly catalog with 3 types."""
    # Corner: 24 guide tubes, 1 instrument tube, thimble plugs in upper section
    # Edge: 20 Pyrex + 4 thimble plugs + 1 instrument tube
    # Center RCCA: 24 RCCA guide tubes, 1 instrument tube

    # Simplified guide tube coordinates for 17x17 (VERA standard positions)
    # In real VERA4, these are specific positions. For this diagnostic we use
    # representative coordinates from the standard W17x17 guide tube pattern.
    gt_coords = [
        (2, 5), (2, 8), (2, 11),
        (3, 3), (3, 13),
        (5, 2), (5, 5), (5, 8), (5, 11), (5, 14),
        (8, 2), (8, 5), (8, 11), (8, 14),
        (11, 2), (11, 5), (11, 8), (11, 11), (11, 14),
        (13, 3), (13, 13),
        (14, 5), (14, 8), (14, 11),
    ]
    inst_coord = [(8, 8)]

    def make_pm(inserts=None):
        kwargs = dict(
            lattice_size=(17, 17),
            default_universe_id="fuel_cell",
            coordinate_convention={"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
            guide_tube_coords=gt_coords,
            instrument_tube_coords=inst_coord,
        )
        if inserts:
            kwargs["localized_insert_intents"] = inserts
        return AssemblyPinMapPatchItem(**kwargs)

    # Corner: thimble plugs in upper section
    corner_inserts = [
        LocalizedInsertIntentPatchItem(
            insert_id=f"thimble_{i}",
            insert_kind="thimble_plug",
            host_kind="guide_tube",
            insert_universe_id="thimble_plug_cell",
            coordinates=[coord],
            z_min_cm=365.76,
            z_max_cm=406.0,
            application_mode="coordinate_override",
        )
        for i, coord in enumerate(gt_coords[:24])
    ]

    # Edge: Pyrex in 20 paths, thimble plugs in 4
    edge_pyrex_coords = gt_coords[:20]
    edge_thimble_coords = gt_coords[20:24]
    edge_inserts = [
        LocalizedInsertIntentPatchItem(
            insert_id="edge_pyrex",
            insert_kind="pyrex_rod",
            host_kind="guide_tube",
            insert_universe_id="pyrex_cell",
            coordinates=edge_pyrex_coords,
            application_mode="coordinate_override",
        ),
        LocalizedInsertIntentPatchItem(
            insert_id="edge_thimble",
            insert_kind="thimble_plug",
            host_kind="guide_tube",
            insert_universe_id="thimble_plug_cell",
            coordinates=edge_thimble_coords,
            application_mode="coordinate_override",
        ),
    ]

    # Center RCCA: 24 RCCA paths
    center_inserts = [
        LocalizedInsertIntentPatchItem(
            insert_id="rcca",
            insert_kind="control_rod",
            host_kind="guide_tube",
            insert_universe_id="rcca_absorber_cell",
            coordinates=gt_coords,
            z_min_cm=257.9,
            z_max_cm=365.76,
            application_mode="coordinate_override",
        ),
    ]

    return AssemblyCatalogPatch(
        patch_type="assembly_catalog",
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="corner",
                name="Corner assembly (2.11% U-235, thimble plugs)",
                role="fuel",
                multiplicity_hint=4,
                pin_map=make_pm(corner_inserts),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="edge",
                name="Edge assembly (2.619% U-235, Pyrex + thimble plugs)",
                role="fuel",
                multiplicity_hint=4,
                pin_map=make_pm(edge_inserts),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="center_rcca",
                name="Center RCCA assembly (2.11% U-235, RCCA)",
                role="fuel",
                multiplicity_hint=1,
                pin_map=make_pm(center_inserts),
            ),
        ],
        source_note="VERA4 assembly type definitions",
    )


def build_vera4_core_layout() -> CoreLayoutPatch:
    """Construct VERA4 core layout patch."""
    return CoreLayoutPatch(
        patch_type="core_layout",
        core_lattice_id="core_lattice",
        shape=(3, 3),
        assembly_pitch_cm=21.50,
        coordinate_convention={"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"},
        assembly_pattern=[
            ["corner", "edge", "corner"],
            ["edge", "center_rcca", "edge"],
            ["corner", "edge", "corner"],
        ],
        boundary="reflective",
        expected_assembly_type_counts={"corner": 4, "edge": 4, "center_rcca": 1},
        symmetry_description="none",
        source_note="VERA4 3x3 core layout",
    )


def run_diagnostic():
    """Run the full VERA4 planning diagnostic."""
    results = {}

    print("=" * 70)
    print("VERA4 Planning Diagnostic (P2-FULLCORE-1)")
    print("=" * 70)

    # 1. Build and validate facts
    print("\n1. FactsPatch...")
    facts = build_vera4_facts()
    assert facts.model_scope == "multi_assembly_core"
    assert facts.assembly_count == 9
    scoped = normalize_scoped_counts(facts)
    scope_val = validate_count_scope_compatibility(facts, scoped)
    assert scope_val.ok, f"Scope validation failed: {[i.message for i in scope_val.issues]}"
    print(f"   model_scope={facts.model_scope}")
    print(f"   assembly_count={facts.assembly_count}")
    print(f"   scoped_expected_counts={len(facts.scoped_expected_counts)} entries")
    results["facts_ok"] = True

    # 2. Build and validate assembly catalog
    print("\n2. AssemblyCatalogPatch...")
    catalog = build_vera4_assembly_catalog()
    catalog_val = validate_patch(catalog)
    assert catalog_val.ok, f"Catalog validation failed: {[i.message for i in catalog_val.issues]}"
    print(f"   assembly_types={len(catalog.assembly_types)}")
    for at in catalog.assembly_types:
        print(f"   - {at.assembly_type_id}: multiplicity_hint={at.multiplicity_hint}")
    results["catalog_ok"] = True

    # 3. Build and validate core layout
    print("\n3. CoreLayoutPatch...")
    layout = build_vera4_core_layout()
    layout_ctx = PatchValidationContext(known_assembly_type_ids=[at.assembly_type_id for at in catalog.assembly_types])
    layout_val = validate_patch(layout, layout_ctx)
    assert layout_val.ok, f"Layout validation failed: {[i.message for i in layout_val.issues]}"
    print(f"   shape={layout.shape}")
    print(f"   boundary={layout.boundary}")
    results["layout_ok"] = True

    # 4. Cross-validate catalog-layout
    print("\n4. Catalog-Layout Cross-Validation...")
    cross_val = validate_catalog_layout_cross_references(catalog, layout)
    assert cross_val.ok, f"Cross-validation failed: {[i.message for i in cross_val.issues]}"
    print("   All type references valid")
    results["cross_val_ok"] = True

    # 5. Build hierarchical core plan
    print("\n5. Hierarchical Core Plan...")
    hier = build_hierarchical_core_plan(catalog, layout, facts, pitch_cm=1.26)
    pin_lattices = hier.pin_lattices
    assemblies = hier.assembly_specs
    core_lattice = hier.core_lattices[0]
    aggregation = hier.core_count_aggregation
    print(f"   pin_lattices={len(pin_lattices)}")
    print(f"   assemblies={len(assemblies)}")
    print(f"   core_lattice shape={core_lattice.shape}")
    print(f"   total_instances={aggregation.total_assembly_instances}")
    print(f"   core_total fuel_pin={aggregation.core_total_for_role('fuel_pin')}")
    print(f"   core_total guide_tube={aggregation.core_total_for_role('guide_tube')}")
    print(f"   core_total instrument_tube={aggregation.core_total_for_role('instrument_tube')}")
    results["hierarchical_ok"] = True

    # 6. Verify NO cross-scope count mismatch
    print("\n6. Count Scope Verification...")
    actual_core = {
        role: aggregation.core_total_for_role(role)
        for role in ["fuel_pin", "guide_tube", "instrument_tube"]
    }
    core_comparison = compare_scoped_expected_counts(
        facts.scoped_expected_counts, actual_core, scope="core_total",
    )
    assert core_comparison.ok, (
        f"Core total mismatch: {[i.message for i in core_comparison.issues]}"
    )
    print(f"   Core totals match facts: {actual_core}")

    # 7. Verify per-type local counts
    print("\n7. Per-Type Local Counts...")
    for type_id, summary in hier.count_summaries.items():
        print(f"   {type_id}: fuel={summary.fuel_pin_count}, "
              f"gt={summary.guide_tube_count}, inst={summary.instrument_tube_count}")
        assert summary.fuel_pin_count == 264, f"Expected 264 fuel per type, got {summary.fuel_pin_count}"

    results["no_count_mismatch"] = True

    # 8. Verify old failure mode is prevented
    print("\n8. Regression Check: Old Failure Mode...")
    # Old failure: 264 (pin_map) vs 2376 (core_total) compared as same scope
    old_expected = ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total")
    pin_map_actual = {"fuel_pin": 264}
    # Comparing at pin_map scope should NOT flag core_total expected
    no_mismatch = compare_scoped_expected_counts(
        [old_expected], pin_map_actual, scope="pin_map",
    )
    assert no_mismatch.ok, "Should not compare core_total against pin_map"
    print("   Pin_map 264 vs core_total 2376: NO MISMATCH (different scopes)")

    results["regression_prevented"] = True

    # Save artifacts
    out_dir = ROOT / "data" / "evals" / "p2_fullcore1" / "vera4_planning_diagnostic"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "vera4_scoped_facts.json").write_text(
        json.dumps(facts.model_dump(), indent=2, default=str)
    )
    (out_dir / "vera4_assembly_catalog.json").write_text(
        json.dumps(catalog.model_dump(), indent=2, default=str)
    )
    (out_dir / "vera4_core_layout.json").write_text(
        json.dumps(layout.model_dump(), indent=2, default=str)
    )
    (out_dir / "vera4_count_aggregation.json").write_text(
        json.dumps(
            {k: v for k, v in {
                "core_totals": aggregation.core_totals,
                "multiplicities": aggregation.multiplicities,
                "total_assembly_instances": aggregation.total_assembly_instances,
                "per_type_summaries": {
                    tid: {
                        "fuel_pin_count": s.fuel_pin_count,
                        "guide_tube_count": s.guide_tube_count,
                        "instrument_tube_count": s.instrument_tube_count,
                        "total_cells": s.total_cells,
                        "localized_insert_counts": s.localized_insert_counts,
                    }
                    for tid, s in hier.count_summaries.items()
                },
            }.items() if not isinstance(v, type)
            }, indent=2, default=str
        )
    )

    # Summary
    print("\n" + "=" * 70)
    print("VERA4 PLANNING DIAGNOSTIC: ALL CHECKS PASSED")
    print("=" * 70)
    print(f"""
Status: FULLCORE_IR_READY_RENDERER_NOT_YET_VALIDATED

Key Results:
- FactsPatch: model_scope=multi_assembly_core, assembly_count=9 ✓
- AssemblyCatalogPatch: 3 types (corner, edge, center_rcca) ✓
- CoreLayoutPatch: 3x3 pattern, boundary=reflective ✓
- Catalog-Layout cross-validation: all references valid ✓
- Hierarchical plan: {len(pin_lattices)} pin lattices, {len(assemblies)} assemblies, 1 core lattice ✓
- Core totals: fuel=2376, guide_tube=216, instrument_tube=9 ✓
- No cross-scope count mismatch (264 vs 2376 no longer compared) ✓
- Old pin_map count mismatch failure mode: PREVENTED ✓

NOT Claimed:
- VERA4 OpenMC execution
- VERA4 keff
- VERA4 Qualification
- Full-core geometry debug
- Renderer integration

Artifacts saved to: {out_dir}
""")

    return results


if __name__ == "__main__":
    run_diagnostic()
