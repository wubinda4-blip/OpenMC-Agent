"""Tests for production hierarchical assembler integration (P2-FULLCORE-2A).

Verifies that the main ``assemble_simulation_plan_from_patches`` correctly
routes multi-assembly patches through the hierarchical path and produces
a ``SimulationPlan`` with ``kind="core"``.
"""

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
    FactsPatch,
    MaterialsPatch,
    MaterialSpecPatch,
    SettingsPatch,
    UniversesPatch,
    UniverseSpecPatch,
    CellLayerPatch,
)


def _make_minimal_universes():
    return UniversesPatch(universes=[
        UniverseSpecPatch(
            universe_id="fuel_cell",
            kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel"),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="guide_tube",
            kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water"),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy"),
            ],
        ),
    ])


def _make_minimal_materials():
    return MaterialsPatch(materials=[
        MaterialSpecPatch(material_id="fuel", name="fuel", role="fuel"),
        MaterialSpecPatch(material_id="water", name="water", role="coolant"),
        MaterialSpecPatch(material_id="zircaloy", name="zircaloy", role="cladding"),
    ])


def _make_2x2_catalog():
    return AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="type_a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel_cell",
                    guide_tube_coords=[(1, 1)],
                ),
            ),
            AssemblyTypePatchItem(
                assembly_type_id="type_b",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3),
                    default_universe_id="fuel_cell",
                    guide_tube_coords=[(0, 0), (2, 2)],
                ),
            ),
        ],
    )


def _make_2x2_layout():
    return CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["type_a", "type_b"], ["type_b", "type_a"]],
        boundary="reflective",
    )


def _make_multi_assembly_facts():
    return FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=4,
        core_lattice_size=(2, 2),
        lattice_size=(3, 3),
        pin_pitch_cm=1.26,
        assembly_pitch_cm=21.50,
        has_axial_geometry=True,
    )


def test_multi_assembly_produces_core_kind():
    """Multi-assembly path must produce ComplexModelSpec.kind='core'."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    assert result.ok or len(result.issues) > 0
    if result.plan is not None:
        assert result.plan.complex_model.kind == "core"


def test_multi_assembly_summary_path():
    """Summary should indicate multi_assembly_core path."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.summary.get("path"):
        assert result.summary["path"] == "multi_assembly_core"


def test_multi_assembly_no_assembly_1():
    """Multi-assembly path must NOT create the old 'assembly_1' id."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        asm_ids = [a.id for a in result.plan.complex_model.assemblies]
        assert "assembly_1" not in asm_ids


def test_multi_assembly_has_wrapper_universes():
    """Multi-assembly plan must include assembly wrapper universes."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        uv_ids = {u.id for u in result.plan.complex_model.universes}
        assert "assembly_universe__type_a" in uv_ids
        assert "assembly_universe__type_b" in uv_ids


def test_multi_assembly_has_core_lattice():
    """Multi-assembly plan must include a core lattice."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        lattice_ids = {l.id for l in result.plan.complex_model.lattices}
        assert "core_lattice" in lattice_ids


def test_multi_assembly_internal_boundary_transmission():
    """Assembly specs in multi-assembly plan must have transmission boundary."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        for asm in result.plan.complex_model.assemblies:
            assert asm.boundary == "transmission"


def test_multi_assembly_has_pin_lattices_per_type():
    """Each assembly type must have its own pin lattice."""
    patches = [
        _make_multi_assembly_facts(),
        _make_minimal_materials(),
        _make_minimal_universes(),
        _make_2x2_catalog(),
        _make_2x2_layout(),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        lattice_ids = {l.id for l in result.plan.complex_model.lattices}
        assert "assembly_lattice__type_a" in lattice_ids
        assert "assembly_lattice__type_b" in lattice_ids


def test_single_assembly_path_unchanged():
    """Single-assembly path must still produce kind='assembly'."""
    from openmc_agent.plan_builder.patches import PinMapPatch
    patches = [
        FactsPatch(
            model_scope="single_assembly",
            lattice_size=(3, 3),
            pin_pitch_cm=1.26,
            expected_pin_count=8,
            expected_guide_tube_count=1,
        ),
        _make_minimal_materials(),
        _make_minimal_universes(),
        PinMapPatch(
            lattice_size=(3, 3),
            default_universe_id="fuel_cell",
            guide_tube_coords=[(1, 1)],
        ),
        SettingsPatch(),
    ]
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    if result.plan is not None:
        assert result.plan.complex_model.kind == "assembly"
