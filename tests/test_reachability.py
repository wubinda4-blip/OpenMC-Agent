"""Reachability-based material checks for the assembly renderer.

These tests lock in the behaviour that the renderer must only treat *active*
materials (those reachable from the default lattice) as blocking. Candidate /
inactive materials (e.g. a burnable-poison universe that is not inserted into the
default lattice) may be incomplete without downgrading the default model below
``exportable``.
"""

from __future__ import annotations

import pytest

from openmc_agent.reachability import (
    ActiveDependencies,
    collect_active_dependencies,
)
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    ExecutionCheckSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    UniverseSpec,
)


# -- shared plan builder --------------------------------------------------


def _complete_material(mid: str, name: str) -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id=mid,
        name=name,
        density_unit="g/cm3",
        density_value=10.0,
        composition=[NuclideSpec(name="U235", percent=1.0)],
    )


def _incomplete_material(mid: str, name: str) -> ComplexMaterialSpec:
    """A material with neither density nor composition declared."""
    return ComplexMaterialSpec(
        id=mid,
        name=name,
        requires_human_confirmation=["density", "composition"],
    )


def _assembly_plan(
    *,
    pattern: list[list[str]],
    materials: list[ComplexMaterialSpec],
    cells: list[CellSpec],
    universes: list[UniverseSpec],
    boundary: str = "reflective",
) -> SimulationPlan:
    model = ComplexModelSpec(
        name="reachability test assembly",
        kind="assembly",
        materials=materials,
        cells=cells,
        universes=universes,
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=pattern,
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root",
                lattice_id="assembly_lattice",
                pitch_cm=1.43,
                boundary=boundary,
            )
        ],
        settings=RunSettingsSpec(batches=8, inactive=2, particles=80),
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(
            is_executable=False, supported_renderer="none"
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.86, 2.86), filename="a.png")],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20)
        ),
    )


def _fuel_guide_cells() -> list[CellSpec]:
    return [
        CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="uo2_fuel"),
        CellSpec(id="clad_cell", name="clad", fill_type="material", fill_id="zr4_clad"),
        CellSpec(id="mod_cell", name="moderator", fill_type="material", fill_id="h2o"),
        CellSpec(id="guide_wall_cell", name="guide wall", fill_type="material", fill_id="ss_guide"),
        CellSpec(id="guide_mod_cell", name="guide moderator", fill_type="material", fill_id="h2o"),
    ]


def _fuel_guide_universes() -> list[UniverseSpec]:
    return [
        UniverseSpec(
            id="fuel_pin_universe",
            name="fuel pin",
            cell_ids=["fuel_cell", "clad_cell", "mod_cell"],
        ),
        UniverseSpec(
            id="guide_tube_universe",
            name="guide tube",
            cell_ids=["guide_wall_cell", "guide_mod_cell"],
        ),
    ]


def _active_complete_materials() -> list[ComplexMaterialSpec]:
    return [
        _complete_material("uo2_fuel", "UO2 fuel"),
        _complete_material("zr4_clad", "Zr-4 clad"),
        _complete_material("h2o", "water"),
        _complete_material("ss_guide", "stainless steel"),
    ]


_DEFAULT_PATTERN = [
    ["fuel_pin_universe", "guide_tube_universe"],
    ["guide_tube_universe", "fuel_pin_universe"],
]


# -- reachability unit tests ----------------------------------------------


def test_active_materials_collected_from_rect_lattice_pattern() -> None:
    """Active graph follows lattice.universe_pattern -> universes -> cells -> materials."""
    materials = _active_complete_materials() + [_incomplete_material("borosilicate_glass", "BP glass")]
    cells = _fuel_guide_cells() + [
        CellSpec(id="bp_glass_cell", name="bp glass", fill_type="material", fill_id="borosilicate_glass"),
    ]
    universes = _fuel_guide_universes() + [
        UniverseSpec(id="burnable_poison_universe", name="bp", cell_ids=["bp_glass_cell"]),
    ]
    plan = _assembly_plan(pattern=_DEFAULT_PATTERN, materials=materials, cells=cells, universes=universes)

    deps = collect_active_dependencies(plan)

    assert "fuel_pin_universe" in deps.universe_ids
    assert "guide_tube_universe" in deps.universe_ids
    assert "burnable_poison_universe" in deps.inactive_universe_ids
    assert "burnable_poison_universe" not in deps.universe_ids

    assert "borosilicate_glass" not in deps.material_ids
    assert "borosilicate_glass" in deps.inactive_material_ids
    # Every active material used by fuel/guide universes is collected.
    assert {"uo2_fuel", "zr4_clad", "h2o", "ss_guide"} <= deps.material_ids


def test_collect_active_dependencies_handles_missing_complex_model() -> None:
    """A pin-cell-only plan has no complex_model; reachability is empty, not an error."""
    from openmc_agent.schemas import SimulationSpec, PinCellSpec, GeometrySpec, MaterialSpec

    spec = SimulationSpec(
        name="pin",
        pin_cell=PinCellSpec(
            fuel=MaterialSpec(name="UO2", density_unit="g/cm3", density_value=10.0,
                              composition=[NuclideSpec(name="U235", percent=1.0)]),
            moderator=MaterialSpec(name="H2O", density_unit="g/cm3", density_value=1.0,
                                   composition=[NuclideSpec(name="H1", percent=2.0)]),
            geometry=GeometrySpec(fuel_radius_cm=0.4, pitch_cm=1.26),
        ),
    )
    plan = SimulationPlan(
        model_spec=spec,
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="p.png")],
    )
    deps = collect_active_dependencies(plan)
    assert isinstance(deps, ActiveDependencies)
    assert not deps.material_ids
    assert not deps.universe_ids


# -- capability / blocking behaviour --------------------------------------


def test_inactive_candidate_material_missing_is_warning_only() -> None:
    """borosilicate_glass lives in an un-inserted candidate universe -> warning, not blocking."""
    materials = _active_complete_materials() + [_incomplete_material("borosilicate_glass", "BP glass")]
    cells = _fuel_guide_cells() + [
        CellSpec(id="bp_glass_cell", name="bp glass", fill_type="material", fill_id="borosilicate_glass"),
    ]
    universes = _fuel_guide_universes() + [
        UniverseSpec(id="burnable_poison_universe", name="bp", cell_ids=["bp_glass_cell"]),
    ]
    plan = _assembly_plan(pattern=_DEFAULT_PATTERN, materials=materials, cells=cells, universes=universes)

    capability = RectAssemblyRenderer().can_render(plan)

    # Default F/G assembly stays executable despite the incomplete candidate material.
    assert capability.is_executable is True
    assert capability.renderability in {"exportable", "runnable"}
    assert not any("borosilicate_glass" in reason for reason in capability.reasons), capability.reasons
    # The gap is surfaced as a warning / human-confirmation instead.
    blob = "\n".join(capability.warnings + capability.required_human_confirmations)
    assert "borosilicate_glass" in blob


def test_active_candidate_material_missing_is_blocking() -> None:
    """Once the candidate universe is referenced by the lattice, its material becomes blocking."""
    materials = _active_complete_materials() + [_incomplete_material("borosilicate_glass", "BP glass")]
    cells = _fuel_guide_cells() + [
        CellSpec(id="bp_glass_cell", name="bp glass", fill_type="material", fill_id="borosilicate_glass"),
    ]
    universes = _fuel_guide_universes() + [
        UniverseSpec(id="burnable_poison_universe", name="bp", cell_ids=["bp_glass_cell"]),
    ]
    pattern = [
        ["fuel_pin_universe", "burnable_poison_universe"],
        ["guide_tube_universe", "fuel_pin_universe"],
    ]
    plan = _assembly_plan(pattern=pattern, materials=materials, cells=cells, universes=universes)

    capability = RectAssemblyRenderer().can_render(plan)

    assert capability.is_executable is False
    assert capability.renderability == "skeleton"
    assert any("borosilicate_glass" in reason and "missing density" in reason
               for reason in capability.reasons), capability.reasons


def test_active_material_missing_density_is_blocking() -> None:
    """An active fuel material missing density must still block (no false negatives)."""
    materials = _active_complete_materials()
    # Replace uo2_fuel with an incomplete active material.
    materials = [m for m in materials if m.id != "uo2_fuel"]
    materials.append(_incomplete_material("uo2_fuel", "UO2 fuel"))
    plan = _assembly_plan(
        pattern=_DEFAULT_PATTERN,
        materials=materials,
        cells=_fuel_guide_cells(),
        universes=_fuel_guide_universes(),
    )

    capability = RectAssemblyRenderer().can_render(plan)

    assert capability.is_executable is False
    assert capability.renderability == "skeleton"
    assert any("uo2_fuel" in reason and "missing density" in reason
               for reason in capability.reasons), capability.reasons


def test_unused_material_does_not_block_exportability() -> None:
    """A fully orphaned incomplete material must not downgrade an exportable plan."""
    materials = _active_complete_materials() + [_incomplete_material("orphan_mat", "nowhere used")]
    plan = _assembly_plan(
        pattern=_DEFAULT_PATTERN,
        materials=materials,
        cells=_fuel_guide_cells(),
        universes=_fuel_guide_universes(),
    )

    capability = RectAssemblyRenderer().can_render(plan)

    assert capability.renderability in {"exportable", "runnable"}
    assert capability.is_executable is True
    assert not any("orphan_mat" in reason for reason in capability.reasons), capability.reasons
    blob = "\n".join(capability.warnings + capability.required_human_confirmations)
    assert "orphan_mat" in blob


def test_candidate_universe_not_inserted_is_warned() -> None:
    """A candidate universe absent from the default lattice is reported as a warning."""
    materials = _active_complete_materials() + [_incomplete_material("borosilicate_glass", "BP glass")]
    cells = _fuel_guide_cells() + [
        CellSpec(id="bp_glass_cell", name="bp glass", fill_type="material", fill_id="borosilicate_glass"),
    ]
    universes = _fuel_guide_universes() + [
        UniverseSpec(id="burnable_poison_universe", name="bp", cell_ids=["bp_glass_cell"]),
    ]
    plan = _assembly_plan(pattern=_DEFAULT_PATTERN, materials=materials, cells=cells, universes=universes)

    capability = RectAssemblyRenderer().can_render(plan)

    warnings_blob = "\n".join(capability.warnings)
    assert "burnable_poison_universe" in warnings_blob
