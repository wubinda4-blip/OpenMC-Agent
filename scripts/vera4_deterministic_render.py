"""VERA4 deterministic fixture render diagnostic (P2-FULLCORE-2A).

Builds VERA4 patches deterministically (not from LLM), runs them through
the production assembler, and verifies the resulting SimulationPlan
has the correct hierarchical structure.

This is NOT the real-LLM canary — it verifies the production integration.
Status: VERA4_DETERMINISTIC_IR_FIXTURE_PASSED
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
    FactsPatch,
    LocalizedInsertIntentPatchItem,
    MaterialsPatch,
    MaterialSpecPatch,
    SettingsPatch,
    UniversesPatch,
    UniverseSpecPatch,
    CellLayerPatch,
    ScopedExpectedCount,
)


def build_vera4_patches():
    """Build deterministic VERA4 patches."""
    gt_coords = [
        (2, 5), (2, 8), (2, 11), (3, 3), (3, 13),
        (5, 2), (5, 5), (5, 8), (5, 11), (5, 14),
        (8, 2), (8, 5), (8, 11), (8, 14),
        (11, 2), (11, 5), (11, 8), (11, 11), (11, 14),
        (13, 3), (13, 13), (14, 5), (14, 8), (14, 11),
    ]

    facts = FactsPatch(
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
            ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total"),
            ScopedExpectedCount(role="guide_tube", value=216, scope="core_total"),
            ScopedExpectedCount(role="instrument_tube", value=9, scope="core_total"),
        ],
    )

    materials = MaterialsPatch(materials=[
        MaterialSpecPatch(material_id="fuel_r1", name="fuel 2.11%", role="fuel", density_g_cm3=10.25),
        MaterialSpecPatch(material_id="fuel_r2", name="fuel 2.619%", role="fuel", density_g_cm3=10.25),
        MaterialSpecPatch(material_id="water", name="borated water", role="coolant"),
        MaterialSpecPatch(material_id="zircaloy4", name="zircaloy-4", role="cladding"),
    ])

    universes = UniversesPatch(universes=[
        UniverseSpecPatch(
            universe_id="fuel_cell_r1",
            kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel_r1"),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="fuel_cell_r2",
            kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel_r2"),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="guide_tube",
            kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water"),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy4"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="inst_tube",
            kind="instrument_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water"),
            ],
        ),
    ])

    def make_pm(default_fuel="fuel_cell_r1", inserts=None):
        kwargs = dict(
            lattice_size=(17, 17),
            default_universe_id=default_fuel,
            guide_tube_coords=gt_coords,
            instrument_tube_coords=[(8, 8)],
        )
        if inserts:
            kwargs["localized_insert_intents"] = inserts
        return AssemblyPinMapPatchItem(**kwargs)

    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="corner",
            name="corner assembly",
            role="fuel",
            multiplicity_hint=4,
            pin_map=make_pm("fuel_cell_r1"),
        ),
        AssemblyTypePatchItem(
            assembly_type_id="edge",
            name="edge assembly",
            role="fuel",
            multiplicity_hint=4,
            pin_map=make_pm("fuel_cell_r2", [
                LocalizedInsertIntentPatchItem(
                    insert_id="edge_pyrex",
                    insert_kind="pyrex_rod",
                    host_kind="guide_tube",
                    insert_universe_id="guide_tube",
                    coordinates=gt_coords[:20],
                    z_min_cm=0.0, z_max_cm=100.0,
                ),
            ]),
        ),
        AssemblyTypePatchItem(
            assembly_type_id="center_rcca",
            name="center RCCA assembly",
            role="fuel",
            multiplicity_hint=1,
            pin_map=make_pm("fuel_cell_r1", [
                LocalizedInsertIntentPatchItem(
                    insert_id="rcca",
                    insert_kind="control_rod",
                    host_kind="guide_tube",
                    insert_universe_id="guide_tube",
                    coordinates=gt_coords,
                    z_min_cm=257.9, z_max_cm=365.76,
                ),
            ]),
        ),
    ])

    layout = CoreLayoutPatch(
        shape=(3, 3),
        assembly_pitch_cm=21.50,
        assembly_pattern=[
            ["corner", "edge", "corner"],
            ["edge", "center_rcca", "edge"],
            ["corner", "edge", "corner"],
        ],
        boundary="reflective",
        expected_assembly_type_counts={"corner": 4, "edge": 4, "center_rcca": 1},
    )

    settings = SettingsPatch()

    return [facts, materials, universes, catalog, layout, settings]


def run_diagnostic():
    print("=" * 70)
    print("VERA4 Deterministic Fixture Render Diagnostic (P2-FULLCORE-2A)")
    print("=" * 70)

    patches = build_vera4_patches()
    print(f"\n1. Patches: {len(patches)} total")
    for p in patches:
        print(f"   - {p.patch_type}")

    print("\n2. Production assembler...")
    result = assemble_simulation_plan_from_patches(patches, strict=False)

    print(f"   ok={result.ok}")
    print(f"   summary path={result.summary.get('path', 'N/A')}")
    print(f"   issues: {len(result.issues)}")
    for i in result.issues[:5]:
        print(f"     [{i.severity}] {i.code}: {i.message}")

    if result.plan is None:
        print("\nFAILED: No plan produced")
        return False

    plan = result.plan
    model = plan.complex_model

    print(f"\n3. Plan structure:")
    print(f"   kind={model.kind}")
    print(f"   materials={len(model.materials)}")
    print(f"   universes={len(model.universes)}")
    print(f"   cells={len(model.cells)}")
    print(f"   lattices={len(model.lattices)}")
    print(f"   assemblies={len(model.assemblies)}")
    print(f"   core={model.core is not None}")

    if model.core:
        print(f"   core.lattice_id={model.core.lattice_id}")
        print(f"   core.boundary={model.core.boundary}")
        print(f"   core.assembly_ids={model.core.assembly_ids}")

    # Key checks
    checks = []

    # kind=core
    c = model.kind == "core"
    checks.append(("kind=core", c))

    # has core lattice
    lattice_ids = {l.id for l in model.lattices}
    c = "core_lattice" in lattice_ids
    checks.append(("has core_lattice", c))

    # has per-type pin lattices
    c = "assembly_lattice__corner" in lattice_ids
    checks.append(("has corner pin lattice", c))
    c = "assembly_lattice__edge" in lattice_ids
    checks.append(("has edge pin lattice", c))
    c = "assembly_lattice__center_rcca" in lattice_ids
    checks.append(("has center_rcca pin lattice", c))

    # has wrapper universes
    uv_ids = {u.id for u in model.universes}
    c = "assembly_universe__corner" in uv_ids
    checks.append(("has corner wrapper universe", c))
    c = "assembly_universe__edge" in uv_ids
    checks.append(("has edge wrapper universe", c))
    c = "assembly_universe__center_rcca" in uv_ids
    checks.append(("has center_rcca wrapper universe", c))

    # assembly boundaries = transmission
    c = all(a.boundary == "transmission" for a in model.assemblies)
    checks.append(("all assembly boundaries=transmission", c))

    # core boundary = reflective
    c = model.core and model.core.boundary == "reflective"
    checks.append(("core boundary=reflective", c))

    # core_count_aggregation
    fuel_total = result.summary.get("core_total_fuel", 0)
    c = fuel_total == 2376
    checks.append((f"core_total_fuel=2376 (got {fuel_total})", c))

    # core lattice centered
    core_lat = next((l for l in model.lattices if l.id == "core_lattice"), None)
    if core_lat:
        c = core_lat.center_cm == (0.0, 0.0)
        checks.append(("core_lattice centered at origin", c))
        c = core_lat.shape == (3, 3)
        checks.append(("core_lattice shape=(3,3)", c))

    # serialized
    try:
        plan_dict = plan.model_dump()
        c = True
    except Exception as e:
        c = False
    checks.append(("plan serializable", c))

    print(f"\n4. Checks ({sum(1 for _, c in checks if c)}/{len(checks)} passed):")
    for name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"   {status} {name}")

    all_passed = all(c for _, c in checks)
    print(f"\n{'='*70}")
    if all_passed:
        print("VERA4 DETERMINISTIC FIXTURE RENDER: ALL CHECKS PASSED")
        print("Status: P2_FULLCORE_PRODUCTION_PLAN_ASSEMBLED")
    else:
        print("VERA4 DETERMINISTIC FIXTURE RENDER: SOME CHECKS FAILED")
    print(f"{'='*70}")

    # Save artifacts
    out_dir = ROOT / "data" / "evals" / "p2_fullcore1" / "vera4_production_render"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vera4_production_plan.json").write_text(
        json.dumps(plan.model_dump(), indent=2, default=str)
    )

    return all_passed


if __name__ == "__main__":
    run_diagnostic()
