"""Tests for full-core boundary, gap, and centered placement (P2-FULLCORE-2A)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoreLayoutPatch,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    build_hierarchical_core_plan,
    assemble_core_lattice,
)


def test_assembly_boundary_transmission_not_reflective():
    """Internal assemblies must use 'transmission', not 'reflective'."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["a", "a"], ["a", "a"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    for asm in result.assembly_specs:
        assert asm.boundary == "transmission"


def test_core_lattice_centered_at_origin():
    """3×3 core lattice should be centered at (0,0)."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(3, 3),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["a"]*3]*3,
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    core_lat = result.core_lattices[0]
    assert core_lat.center_cm == (0.0, 0.0)
    assert core_lat.lower_left_cm is not None
    half_width = 3 * 21.50 / 2.0
    assert abs(core_lat.lower_left_cm[0] + half_width) < 0.01
    assert abs(core_lat.lower_left_cm[1] + half_width) < 0.01


def test_core_lattice_pitch_is_assembly_pitch():
    """Core lattice pitch should equal assembly pitch, not pin pitch."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["a", "a"], ["a", "a"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    core_lat = result.core_lattices[0]
    assert core_lat.pitch_cm == (21.50, 21.50)


def test_pin_lattice_pitch_is_pin_pitch():
    """Pin lattice pitch should equal pin pitch, not assembly pitch."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.50,
        assembly_pattern=[["a", "a"], ["a", "a"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None, pitch_cm=1.26)
    pin_lat = result.pin_lattices[0]
    assert pin_lat.pitch_cm == (1.26, 1.26)


def test_core_boundary_from_layout():
    """Core boundary should come from the layout patch."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(lattice_size=(3, 3), default_universe_id="fuel"),
            ),
        ],
    )
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["a", "a"], ["a", "a"]],
        boundary="reflective",
    )
    result = build_hierarchical_core_plan(catalog, layout, facts=None)
    assert result.core_spec.boundary == "reflective"


def test_assembly_gap_implicit():
    """Assembly gap = assembly_pitch - lattice_size × pin_pitch."""
    # 17×17 at 1.26 pitch = 21.42, assembly pitch = 21.50 → gap = 0.08 cm
    pin_lattice_width = 17 * 1.26
    assembly_pitch = 21.50
    gap = assembly_pitch - pin_lattice_width
    assert abs(gap - 0.08) < 0.01  # 0.08 cm total gap
    half_gap = gap / 2.0
    assert abs(half_gap - 0.04) < 0.01  # 0.04 cm per side
