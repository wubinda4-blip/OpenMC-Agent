"""VERA4 deterministic physical geometry closure (P2-FULLCORE-2C-B).

Builds deterministic VERA4 patches with full material compositions,
distinct replacement universes (pyrex_rod, rcca_absorber), profile
registry, and concrete per-segment axial-state materialization.
Runs through the production assembler, CoreRenderer, model.py, XML
export, OpenMC geometry debug, and low-cost transport smoke.

This is NOT a real-LLM canary — it verifies the deterministic physical
geometry pipeline end-to-end.

Target status: VERA4_DETERMINISTIC_PHYSICAL_CLOSURE_PASSED
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    AxialLayerPatchItem,
    AxialLayersPatch,
    CoreLayoutPatch,
    FactsPatch,
    LocalizedInsertAxialProfilePatchItem,
    LocalizedInsertAxialSegmentPatchItem,
    LocalizedInsertIntentPatchItem,
    LocalizedInsertProfilesPatch,
    MaterialsPatch,
    MaterialSpecPatch,
    SettingsPatch,
    UniversesPatch,
    UniverseSpecPatch,
    CellLayerPatch,
    ScopedExpectedCount,
)


def build_vera4_materials():
    """Build VERA4 materials with full composition (reactor-neutral)."""
    return MaterialsPatch(materials=[
        MaterialSpecPatch(
            material_id="fuel_r1", name="fuel 2.11%", role="fuel",
            density_g_cm3=10.25,
            composition={"U235": 0.0211, "U238": 0.9789, "O16": 2.0},
            composition_basis="atom_frac",
            composition_status="approximate",
            source_note="deterministic fixture for geometry testing",
        ),
        MaterialSpecPatch(
            material_id="fuel_r2", name="fuel 2.619%", role="fuel",
            density_g_cm3=10.25,
            composition={"U235": 0.02619, "U238": 0.97381, "O16": 2.0},
            composition_basis="atom_frac",
            composition_status="approximate",
            source_note="deterministic fixture for geometry testing",
        ),
        MaterialSpecPatch(
            material_id="water", name="borated water", role="coolant",
            density_g_cm3=0.7409,
            composition={"H1": 2.0, "O16": 1.0, "B10": 1e-5, "B11": 4e-5},
            composition_basis="atom_frac",
            composition_status="approximate",
            source_note="deterministic fixture for geometry testing",
        ),
        MaterialSpecPatch(
            material_id="zircaloy4", name="zircaloy-4", role="cladding",
            density_g_cm3=6.56,
            composition={"Zr": 0.9823, "Sn": 0.0145, "Fe": 0.0021, "Cr": 0.0010},
            composition_basis="weight_frac",
            composition_status="approximate",
            source_note="deterministic fixture for geometry testing",
        ),
        MaterialSpecPatch(
            material_id="pyrex_glass", name="pyrex glass", role="absorber",
            density_g_cm3=2.23,
            composition={"B11": 0.0403, "O16": 0.5356, "Na23": 0.0282,
                         "Al27": 0.0116, "Si28": 0.3770, "Si29": 0.0192},
            composition_basis="atom_frac",
            composition_status="approximate",
            source_note="deterministic fixture: Pyrex replacement for guide tube in burnable poison",
        ),
        MaterialSpecPatch(
            material_id="rcca_absorber_mat", name="RCCA absorber", role="absorber",
            density_g_cm3=10.2,
            composition={"Ag107": 0.4173, "In115": 0.1491, "Cd114": 0.0529,
                         "Ag109": 0.3827},
            composition_basis="atom_frac",
            composition_status="approximate",
            source_note="deterministic fixture: Ag-In-Cd absorber for RCCA",
        ),
    ])


def build_vera4_universes():
    return UniversesPatch(universes=[
        UniverseSpecPatch(
            universe_id="fuel_cell_r1",
            kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel_r1",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.4096),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.4096, r_max_cm=0.4750),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="fuel_cell_r2",
            kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel_r2",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.4096),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.4096, r_max_cm=0.4750),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="guide_tube",
            kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.5615),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.5615, r_max_cm=0.6121),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="inst_tube",
            kind="instrument_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.5615),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="pyrex_rod",
            kind="custom",
            cells=[
                CellLayerPatch(id="pyrex_inner", role="absorber", material_id="pyrex_glass",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.4180),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.4180, r_max_cm=0.4750),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="rcca_absorber",
            kind="custom",
            cells=[
                CellLayerPatch(id="absorber_inner", role="absorber", material_id="rcca_absorber_mat",
                               region_kind="cylinder", r_min_cm=0.0, r_max_cm=0.3860),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4",
                               region_kind="cylinder", r_min_cm=0.3860, r_max_cm=0.4750),
                CellLayerPatch(id="coolant", role="coolant", material_id="water",
                               region_kind="background"),
            ],
        ),
    ])


def build_vera4_axial_layers():
    """Build axial layers for VERA4 base model."""
    return AxialLayersPatch(
        axial_domain_cm=[0.0, 400.0],
        layers=[
            AxialLayerPatchItem(
                layer_id="lower_nozzle", role="lower_nozzle",
                z_min_cm=0.0, z_max_cm=50.0,
                fill_type="material", fill_id="water",
            ),
            AxialLayerPatchItem(
                layer_id="active_fuel", role="active_fuel",
                z_min_cm=50.0, z_max_cm=300.0,
                fill_type="lattice", fill_id="core_lattice",
            ),
            AxialLayerPatchItem(
                layer_id="upper_plenum", role="upper_plenum",
                z_min_cm=300.0, z_max_cm=365.76,
                fill_type="material", fill_id="water",
            ),
            AxialLayerPatchItem(
                layer_id="upper_nozzle", role="upper_nozzle",
                z_min_cm=365.76, z_max_cm=400.0,
                fill_type="material", fill_id="water",
            ),
        ],
    )


def build_vera4_patches():
    """Build deterministic VERA4 patches with full materials."""
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
        has_spacer_grids=False,
        has_special_pin_map=True,
        scoped_expected_counts=[
            ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total"),
            ScopedExpectedCount(role="guide_tube", value=216, scope="core_total"),
            ScopedExpectedCount(role="instrument_tube", value=9, scope="core_total"),
        ],
        axial_domain_cm=(0.0, 400.0),
        active_fuel_region_cm=(50.0, 300.0),
    )

    materials = build_vera4_materials()
    universes = build_vera4_universes()
    axial_layers = build_vera4_axial_layers()

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
                    insert_universe_id="pyrex_rod",
                    coordinates=gt_coords[:20],
                    z_min_cm=0.0, z_max_cm=50.0,
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
                    insert_universe_id="rcca_absorber",
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

    return [facts, materials, universes, axial_layers, catalog, layout, settings]


def run_diagnostic():
    print("=" * 70)
    print("VERA4 Deterministic Physical Geometry Closure (P2-FULLCORE-2C-B)")
    print("=" * 70)

    patches = build_vera4_patches()
    print(f"\n1. Patches: {len(patches)} total")
    for p in patches:
        print(f"   - {p.patch_type}")

    # ---- Assemble ----
    print("\n2. Production assembler...")
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    print(f"   ok={result.ok}")
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
        print(f"   core.axial_layers={len(model.core.axial_layers)}")

    # ---- Test CoreRenderer capability ----
    print("\n4. CoreRenderer capability check...")
    from openmc_agent.renderers.core import CoreRenderer
    renderer = CoreRenderer()
    capability = renderer.can_render(plan)
    print(f"   renderability={capability.renderability}")
    print(f"   supported_renderer={capability.supported_renderer}")
    if capability.reasons:
        for r in capability.reasons[:5]:
            print(f"   reason: {r}")
    if capability.issues:
        for iss in capability.issues[:10]:
            print(f"   [{iss.severity}] {iss.code}: {iss.message}")

    checks = []

    # ---- Structural checks ----
    lattice_ids = {l.id for l in model.lattices}
    uv_ids = {u.id for u in model.universes}
    cell_ids = {c.id for c in model.cells}

    checks.append(("kind=core", model.kind == "core"))
    checks.append(("has core_lattice", "core_lattice" in lattice_ids))
    n_pin_lattices = sum(1 for l in model.lattices if l.id.startswith("assembly_lattice__"))
    checks.append((f"has >=3 pin lattices ({n_pin_lattices})", n_pin_lattices >= 3))
    n_wrapper_uvs = sum(1 for u in model.universes if u.id.startswith("assembly_universe__"))
    checks.append((f"has >=3 wrapper universes ({n_wrapper_uvs})", n_wrapper_uvs >= 3))
    checks.append(("has moderator_outer", "moderator_outer" in uv_ids))
    checks.append(("core boundary=reflective", model.core and model.core.boundary == "reflective"))
    checks.append(("transmission assemblies", all(a.boundary == "transmission" for a in model.assemblies)))

    # ---- Reference integrity ----
    ref_ok = True
    for cell in model.cells:
        if cell.fill_type == "lattice" and cell.fill_id not in lattice_ids:
            ref_ok = False
            print(f"   BROKEN: cell {cell.id} -> lattice {cell.fill_id}")
        if cell.fill_type == "material":
            mat_ids = {m.id for m in model.materials}
            if cell.fill_id not in mat_ids:
                ref_ok = False
                print(f"   BROKEN: cell {cell.id} -> material {cell.fill_id}")
    checks.append(("all cell refs resolve", ref_ok))

    lat_ref_ok = True
    for lat in model.lattices:
        for row in lat.universe_pattern:
            for uid in row:
                if uid not in uv_ids:
                    lat_ref_ok = False
                    print(f"   BROKEN: lattice {lat.id} -> universe {uid}")
        if lat.outer_universe_id and lat.outer_universe_id not in uv_ids:
            lat_ref_ok = False
            print(f"   BROKEN: lattice {lat.id} -> outer {lat.outer_universe_id}")
    checks.append(("all lattice refs resolve", lat_ref_ok))

    # ---- Concrete insert state checks ----
    has_pyrex_uv = "pyrex_rod" in uv_ids
    checks.append(("has pyrex_rod replacement universe", has_pyrex_uv))
    has_rcca_uv = "rcca_absorber" in uv_ids
    checks.append(("has rcca_absorber replacement universe", has_rcca_uv))

    n_seg_lattices = sum(1 for l in model.lattices if "__seg" in l.id)
    checks.append((f"has segment-specific lattices ({n_seg_lattices})", n_seg_lattices > 0))

    n_axial_layers = len(model.core.axial_layers) if model.core else 0
    checks.append((f"core has >1 axial layers ({n_axial_layers})", n_axial_layers > 1))

    # Check derived lattices use replacement universes
    derived_with_insert = False
    for lat in model.lattices:
        if "__seg" in lat.id:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid in ("pyrex_rod", "rcca_absorber"):
                        derived_with_insert = True
    checks.append(("derived lattices contain insert universes", derived_with_insert))

    # ---- Material composition ----
    mat_ok = all(
        bool(m.composition) or bool(m.chemical_formula) or bool(m.mixture_component_ids)
        for m in model.materials
    )
    checks.append(("all materials have composition", mat_ok))

    # ---- Plan serializable ----
    try:
        plan.model_dump()
        checks.append(("plan serializable", True))
    except Exception:
        checks.append(("plan serializable", False))

    # ---- CoreRenderer exportable ----
    is_exportable = capability.renderability in ("exportable", "runnable")
    checks.append(("CoreRenderer exportable/runnable", is_exportable))

    # ---- Render to model.py ----
    model_script = None
    if is_exportable:
        print("\n5. Rendering to model.py...")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                render_result = renderer.render(plan, Path(tmpdir))
                model_file = Path(tmpdir) / "model.py"
                if model_file.exists():
                    model_script = model_file.read_text()
                    print(f"   model.py generated: {len(model_script)} chars")
                    checks.append(("model.py generated", True))
                else:
                    print(f"   model.py not found, render_result={render_result}")
                    checks.append(("model.py generated", False))

                # ---- Export to XML ----
                if model_file.exists():
                    print("\n6. Exporting to XML...")
                    xml_result = subprocess.run(
                        [sys.executable, str(model_file)],
                        capture_output=True, text=True,
                        timeout=60,
                        cwd=tmpdir,
                        env={
                            "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                            "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
                        },
                    )
                    if xml_result.returncode == 0:
                        xml_files = list(Path(tmpdir).glob("*.xml"))
                        print(f"   XML export OK: {len(xml_files)} files")
                        for xf in xml_files:
                            print(f"     - {xf.name}: {xf.stat().st_size} bytes")
                        checks.append(("XML exported", True))

                        # ---- Geometry debug ----
                        print("\n7. OpenMC geometry debug...")
                        geo_result = subprocess.run(
                            [sys.executable, "-c",
                             "import openmc; g = openmc.Geometry.from_xml(); "
                             "print('Geometry loaded OK')"],
                            capture_output=True, text=True,
                            timeout=60,
                            cwd=tmpdir,
                            env={
                                "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                                "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
                            },
                        )
                        if geo_result.returncode == 0:
                            print(f"   {geo_result.stdout.strip()}")
                            checks.append(("geometry loads", True))

                            # ---- Low-cost transport smoke ----
                            print("\n8. Low-cost transport smoke (1 batch, 10 particles)...")
                            smoke_script = tmpdir_str + "/smoke.py" if False else str(Path(tmpdir) / "smoke.py")
                            smoke_code = '''import openmc
import sys
mats = openmc.Materials.from_xml("materials.xml")
geom = openmc.Geometry.from_xml("geometry.xml", mats)
sett = openmc.Settings.from_xml("settings.xml")
sett.batches = 5
sett.inactive = 1
sett.particles = 100
model = openmc.Model(materials=mats, geometry=geom, settings=sett)
try:
    sp_filename = model.run(cwd=".")
    sp = openmc.StatePoint(sp_filename)
    print(f"TRANSPORT_SMOKE_KEFF={sp.k_combined.nominal_value:.5f}+/-{sp.k_combined.std_dev:.5f}")
except RuntimeError as e:
    print(f"TRANSPORT_SMOKE_FAILED: {e}", file=sys.stderr)
    sys.exit(1)
'''
                            Path(smoke_script).write_text(smoke_code)
                            smoke_result = subprocess.run(
                                [sys.executable, str(smoke_script)],
                                capture_output=True, text=True,
                                timeout=120,
                                cwd=tmpdir,
                                env={
                                    "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                                    "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
                                    "HOME": str(Path.home()),
                                },
                            )
                            if smoke_result.returncode == 0:
                                keff_line = [l for l in smoke_result.stdout.splitlines() if "keff" in l]
                                print(f"   {' '.join(keff_line) if keff_line else 'Transport OK (no keff line)'}")
                                checks.append(("transport smoke", True))
                            else:
                                print(f"   transport smoke FAILED: {smoke_result.stderr[:500]}")
                                checks.append(("transport smoke", False))
                        else:
                            print(f"   geometry load FAILED: {geo_result.stderr[:500]}")
                            checks.append(("geometry loads", False))
                    else:
                        print(f"   XML export FAILED: {xml_result.stderr[:500]}")
                        checks.append(("XML exported", False))
        except Exception as e:
            print(f"   Render error: {e}")
            checks.append(("model.py generated", False))
            checks.append(("XML exported", False))

    # ---- Summary ----
    print(f"\n{'='*70}")
    print(f"Checks ({sum(1 for _, c in checks if c)}/{len(checks)} passed):")
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    all_passed = all(c for _, c in checks)
    if all_passed:
        print(f"\nVERA4_DETERMINISTIC_PHYSICAL_CLOSURE_PASSED")
    else:
        failed = [n for n, c in checks if not c]
        print(f"\nBLOCKED: {failed}")

    # ---- Save artifacts ----
    out_dir = ROOT / "data" / "evals" / "p2_fullcore2c" / "vera4_geometry_closure"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vera4_plan.json").write_text(
        json.dumps(plan.model_dump(), indent=2, default=str)
    )
    if model_script:
        (out_dir / "model.py").write_text(model_script)

    return all_passed


if __name__ == "__main__":
    success = run_diagnostic()
    sys.exit(0 if success else 1)
