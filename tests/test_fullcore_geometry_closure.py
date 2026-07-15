"""Tests for concrete axial-state materialization and VERA4 geometry closure
(P2-FULLCORE-2C-A).

Verifies that the deterministic VERA4 patches produce a SimulationPlan
that the CoreRenderer can render to model.py and export to XML.
"""

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.hierarchical_assembler import (
    compile_global_axial_segments,
)
from openmc_agent.plan_builder.localized_insert_profiles import (
    resolve_all_profiles_for_catalog,
)
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
from openmc_agent.renderers.core import CoreRenderer
from openmc_agent.schemas import SimulationPlan


def _build_vera4_patches():
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
        axial_domain_cm=(0.0, 400.0),
        active_fuel_region_cm=(50.0, 300.0),
        scoped_expected_counts=[
            ScopedExpectedCount(role="fuel_pin", value=2376, scope="core_total"),
        ],
    )

    materials = MaterialsPatch(materials=[
        MaterialSpecPatch(
            material_id="fuel_r1", name="fuel", role="fuel",
            density_g_cm3=10.25,
            composition={"U235": 0.02, "U238": 0.98, "O16": 2.0},
            composition_basis="atom_frac",
            composition_status="approximate",
        ),
        MaterialSpecPatch(
            material_id="water", name="water", role="coolant",
            density_g_cm3=0.74,
            composition={"H1": 2.0, "O16": 1.0},
            composition_basis="atom_frac",
            composition_status="approximate",
        ),
        MaterialSpecPatch(
            material_id="zircaloy4", name="Zry-4", role="cladding",
            density_g_cm3=6.56,
            composition={"Zr": 0.98, "Sn": 0.0145},
            composition_basis="weight_frac",
            composition_status="approximate",
        ),
    ])

    universes = UniversesPatch(universes=[
        UniverseSpecPatch(
            universe_id="fuel_cell", kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel_r1"),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy4"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="guide_tube", kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water"),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy4"),
            ],
        ),
    ])

    axial_layers = AxialLayersPatch(
        axial_domain_cm=[0.0, 400.0],
        layers=[
            AxialLayerPatchItem(layer_id="fuel_only", role="active_fuel",
                                z_min_cm=0.0, z_max_cm=400.0,
                                fill_type="lattice", fill_id="core_lattice"),
        ],
    )

    def make_pm(inserts=None):
        kwargs = dict(
            lattice_size=(3, 3),
            default_universe_id="fuel_cell",
            guide_tube_coords=[(1, 1)],
        )
        if inserts:
            kwargs["localized_insert_intents"] = inserts
        return AssemblyPinMapPatchItem(**kwargs)

    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a", role="fuel",
            pin_map=make_pm(),
        ),
        AssemblyTypePatchItem(
            assembly_type_id="type_b", role="fuel",
            pin_map=make_pm([
                LocalizedInsertIntentPatchItem(
                    insert_id="insert_1", insert_kind="control_rod",
                    insert_universe_id="guide_tube",
                    coordinates=[(1, 1)],
                    z_min_cm=100.0, z_max_cm=200.0,
                ),
            ]),
        ),
    ])

    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["type_a", "type_b"], ["type_b", "type_a"]],
        boundary="reflective",
        expected_assembly_type_counts={"type_a": 2, "type_b": 2},
    )

    settings = SettingsPatch()

    return [facts, materials, universes, axial_layers, catalog, layout, settings]


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_vera4_plan_structure():
    """Multi-assembly plan has correct structure for rendering."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    model = result.plan.complex_model
    assert model.kind == "core"
    assert len(model.materials) == 3
    assert len(model.lattices) >= 3  # 2 pin + 1 core
    assert model.core is not None
    assert model.core.lattice_id == "core_lattice"


def test_vera4_all_references_resolve():
    """All cell and lattice references must resolve."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    model = result.plan.complex_model

    lattice_ids = {l.id for l in model.lattices}
    uv_ids = {u.id for u in model.universes}
    mat_ids = {m.id for m in model.materials}

    for cell in model.cells:
        if cell.fill_type == "lattice":
            assert cell.fill_id in lattice_ids, f"cell {cell.id} -> missing lattice {cell.fill_id}"
        if cell.fill_type == "material":
            assert cell.fill_id in mat_ids, f"cell {cell.id} -> missing material {cell.fill_id}"
        if cell.fill_type == "universe":
            assert cell.fill_id in uv_ids, f"cell {cell.id} -> missing universe {cell.fill_id}"

    for lat in model.lattices:
        for row in lat.universe_pattern:
            for uid in row:
                assert uid in uv_ids, f"lattice {lat.id} -> missing universe {uid}"


def test_vera4_core_lattice_has_safety_outer():
    """Core lattice should have outer_universe_id set as precision safety net."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    core_lat = next(
        l for l in result.plan.complex_model.lattices if l.id == "core_lattice"
    )
    assert core_lat.outer_universe_id is not None


# ---------------------------------------------------------------------------
# CoreRenderer capability
# ---------------------------------------------------------------------------


def test_vera4_core_renderer_exportable():
    """CoreRenderer should report exportable or runnable."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    renderer = CoreRenderer()
    capability = renderer.can_render(result.plan)
    assert capability.renderability in ("exportable", "runnable"), (
        f"Expected exportable/runnable, got {capability.renderability}: "
        f"{[r.message if hasattr(r, 'message') else str(r) for r in capability.issues]}"
    )


def test_vera4_core_renderer_render(tmp_path):
    """CoreRenderer should produce a model.py script."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    renderer = CoreRenderer()
    renderer.render(result.plan, tmp_path)
    model_file = tmp_path / "model.py"
    assert model_file.exists()
    script = model_file.read_text()
    assert "openmc" in script
    assert "RectLattice" in script
    assert "model.export_to_xml" in script


# ---------------------------------------------------------------------------
# Global axial segments with profiles
# ---------------------------------------------------------------------------


def test_global_segments_with_profiled_insert():
    """Global axial segments should include profile segment boundaries."""
    catalog = AssemblyCatalogPatch(assembly_types=[
        AssemblyTypePatchItem(
            assembly_type_id="type_a",
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel_cell",
                guide_tube_coords=[(1, 1)],
                localized_insert_intents=[
                    LocalizedInsertIntentPatchItem(
                        insert_id="rod_1", insert_kind="control_rod",
                        insert_universe_id="guide_tube",
                        coordinates=[(1, 1)],
                        axial_profile_id="rcca_1",
                        anchor_z_cm=200.0,
                    ),
                ],
            ),
        ),
    ])
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=1,
        axial_domain_cm=(0.0, 500.0),
    )
    profiles_patch = LocalizedInsertProfilesPatch(profiles=[
        LocalizedInsertAxialProfilePatchItem(
            profile_id="rcca_1", anchor_kind="bottom",
            segments=[
                LocalizedInsertAxialSegmentPatchItem(
                    segment_id="abs", relative_z_min_cm=0, relative_z_max_cm=150,
                    universe_id="guide_tube",
                ),
            ],
        ),
    ])
    resolved = resolve_all_profiles_for_catalog(catalog, profiles_patch)
    assert len(resolved) == 1
    assert resolved[0].absolute_z_min_cm == pytest.approx(200.0)
    assert resolved[0].absolute_z_max_cm == pytest.approx(350.0)

    segments = compile_global_axial_segments(
        facts, catalog, resolved_profiles=resolved,
    )
    breakpoints = {s.z_min_cm for s in segments} | {s.z_max_cm for s in segments}
    assert 200.0 in breakpoints
    assert 350.0 in breakpoints

    # Check that the profile segment creates an active_inserts entry
    has_active = any(
        "rod_1::abs" in seg.active_inserts.get("type_a", [])
        for seg in segments
    )
    assert has_active


# ---------------------------------------------------------------------------
# VERA4 deterministic fixture (simplified) — plan exportable
# ---------------------------------------------------------------------------


def test_vera4_simplified_plan_roundtrip():
    """Simplified VERA4 plan should be serializable and renderable."""
    result = assemble_simulation_plan_from_patches(_build_vera4_patches(), strict=False)
    assert result.plan is not None
    plan_dict = result.plan.model_dump()
    assert plan_dict["complex_model"]["kind"] == "core"

    # Re-serialize
    plan = SimulationPlan(**plan_dict)
    assert plan.complex_model.kind == "core"
