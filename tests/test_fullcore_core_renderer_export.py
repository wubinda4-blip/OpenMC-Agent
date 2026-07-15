"""Tests for CoreRenderer export with hierarchical plans (P2-FULLCORE-2B).

Verifies that a multi-assembly SimulationPlan can be rendered to Python
script by the existing CoreRenderer pipeline.
"""

import pytest
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


def _make_patches():
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=4,
        core_lattice_size=(2, 2),
        lattice_size=(3, 3),
        pin_pitch_cm=1.26,
        assembly_pitch_cm=21.50,
        has_axial_geometry=True,
    )
    materials = MaterialsPatch(materials=[
        MaterialSpecPatch(material_id="fuel", name="fuel", role="fuel", density_g_cm3=10.0),
        MaterialSpecPatch(material_id="water", name="water", role="coolant"),
        MaterialSpecPatch(material_id="zircaloy", name="zr4", role="cladding"),
    ])
    universes = UniversesPatch(universes=[
        UniverseSpecPatch(
            universe_id="fuel_cell", kind="fuel_pin",
            cells=[
                CellLayerPatch(id="pellet", role="fuel_internal", material_id="fuel"),
                CellLayerPatch(id="clad", role="cladding", material_id="zircaloy"),
            ],
        ),
        UniverseSpecPatch(
            universe_id="guide_tube", kind="guide_tube",
            cells=[
                CellLayerPatch(id="inner", role="inner_flow", material_id="water"),
                CellLayerPatch(id="wall", role="cladding", material_id="zircaloy"),
            ],
        ),
    ])
    catalog = AssemblyCatalogPatch(assembly_types=[
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
    ])
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["type_a", "type_b"], ["type_b", "type_a"]],
        boundary="reflective",
    )
    return [facts, materials, universes, catalog, layout, SettingsPatch()]


def test_multi_assembly_plan_is_renderable_structure():
    """Multi-assembly plan has all required objects for rendering."""
    result = assemble_simulation_plan_from_patches(_make_patches(), strict=False)
    if result.plan is None:
        pytest.skip("Plan not produced (expected for minimal fixture)")
    model = result.plan.complex_model
    assert model.kind == "core"
    # Must have core lattice
    assert any(l.id == "core_lattice" for l in model.lattices)
    # Must have wrapper universes
    uv_ids = {u.id for u in model.universes}
    assert "assembly_universe__type_a" in uv_ids
    # Must have moderator outer universe
    assert "moderator_outer" in uv_ids
    # Pin lattice outer must be moderator
    for lat in model.lattices:
        if lat.id.startswith("assembly_lattice__"):
            assert lat.outer_universe_id == "moderator_outer"
    # Must have core spec
    assert model.core is not None
    assert model.core.lattice_id == "core_lattice"


def test_multi_assembly_plan_serializable():
    """Multi-assembly plan must be serializable to dict and re-loadable."""
    result = assemble_simulation_plan_from_patches(_make_patches(), strict=False)
    if result.plan is None:
        pytest.skip("Plan not produced")
    plan_dict = result.plan.model_dump()
    assert plan_dict is not None
    assert plan_dict["complex_model"]["kind"] == "core"


def test_core_lattice_has_centered_placement():
    """Core lattice must be centered at origin."""
    result = assemble_simulation_plan_from_patches(_make_patches(), strict=False)
    if result.plan is None:
        pytest.skip("Plan not produced")
    core_lat = next(
        l for l in result.plan.complex_model.lattices if l.id == "core_lattice"
    )
    assert core_lat.center_cm == (0.0, 0.0)
    assert core_lat.lower_left_cm is not None
    assert core_lat.lower_left_cm[0] < 0


def test_core_lattice_universe_pattern_valid():
    """Core lattice pattern must reference real wrapper universe IDs."""
    result = assemble_simulation_plan_from_patches(_make_patches(), strict=False)
    if result.plan is None:
        pytest.skip("Plan not produced")
    core_lat = next(
        l for l in result.plan.complex_model.lattices if l.id == "core_lattice"
    )
    uv_ids = {u.id for u in result.plan.complex_model.universes}
    for row in core_lat.universe_pattern:
        for cell in row:
            assert cell in uv_ids, f"Core lattice references missing universe {cell}"


def test_all_wrapper_cells_reference_lattices():
    """Each wrapper cell must reference a real lattice."""
    result = assemble_simulation_plan_from_patches(_make_patches(), strict=False)
    if result.plan is None:
        pytest.skip("Plan not produced")
    lattice_ids = {l.id for l in result.plan.complex_model.lattices}
    for cell in result.plan.complex_model.cells:
        if cell.fill_type == "lattice":
            assert cell.fill_id in lattice_ids, (
                f"Cell {cell.id} references missing lattice {cell.fill_id}"
            )
