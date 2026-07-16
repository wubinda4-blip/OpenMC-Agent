"""Reactor-neutral synthetic grid geometry fixtures for unit tests.

Builds minimal ``SimulationPlan`` objects with spacer-grid geometry **without**
going through the full patch assembler.  This lets tests verify the validator
in a controlled, reactor-neutral context.
"""

from __future__ import annotations

from openmc_agent.plan_builder.grid_geometry_validation import (
    compute_geometry_structural_digest,
)
from openmc_agent.schemas import (
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RegionSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
)
from openmc_agent.schemas import AxialLayerSpec, AxialOverlaySpec, FillRefSpec


__all__ = [
    "build_synthetic_grid_plan",
    "build_synthetic_no_grid_plan",
    "corrupt_plan_remove_decorated_universes",
    "corrupt_plan_remove_lattice_refs",
    "corrupt_plan_remove_ir_merge",
    "corrupt_plan_remove_material_reachability",
    "corrupt_plan_make_identical_digest",
]


def _nuc(sym: str, pct: float = 1.0) -> NuclideSpec:
    return NuclideSpec(name=sym, percent=pct)


def _mat(mid: str, name: str, density: float) -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id=mid, name=name, density_value=density, density_unit="g/cm3",
        composition=[_nuc("Zr90")],
    )


def _cyl_surfaces(prefix: str, radii: list[float]) -> list[SurfaceSpec]:
    return [
        SurfaceSpec(id=f"{prefix}_r{i}", kind="zcylinder",
                    parameters={"r": r, "x0": 0.0, "y0": 0.0})
        for i, r in enumerate(radii)
    ]


def _cyl_region(prefix: str, surf_ids: list[str], idx: int) -> RegionSpec:
    if idx == 0:
        expr = f"-{surf_ids[0]}"
    else:
        expr = f"+{surf_ids[idx-1]} -{surf_ids[idx]}"
    return RegionSpec(
        id=f"reg_{prefix}_{idx}",
        expression=expr,
        surface_ids=[surf_ids[min(idx, len(surf_ids)-1)], surf_ids[max(idx-1, 0)]],
    )


def _background_region(prefix: str, last_surf: str) -> RegionSpec:
    return RegionSpec(
        id=f"reg_{prefix}_bg",
        expression=f"+{last_surf}",
        surface_ids=[last_surf],
    )


def _pin_universe(
    uv_id: str, mat_fuel: str, mat_clad: str, mat_cool: str,
    r_fuel: float = 0.3, r_clad: float = 0.4,
) -> tuple[UniverseSpec, list[CellSpec], list[SurfaceSpec], list[RegionSpec]]:
    """Build a concentric-cylinder pin-cell universe."""
    surfs = _cyl_surfaces(uv_id, [r_fuel, r_clad])
    regions = [
        _cyl_region(uv_id, [s.id for s in surfs], 0),
        _cyl_region(uv_id, [s.id for s in surfs], 1),
        _background_region(uv_id, surfs[-1].id),
    ]
    cells = [
        CellSpec(id=f"cell_{uv_id}_fuel", name=f"fuel {uv_id}",
                 region_id=regions[0].id, fill_type="material", fill_id=mat_fuel),
        CellSpec(id=f"cell_{uv_id}_clad", name=f"clad {uv_id}",
                 region_id=regions[1].id, fill_type="material", fill_id=mat_clad),
        CellSpec(id=f"cell_{uv_id}_cool", name=f"cool {uv_id}",
                 region_id=regions[2].id, fill_type="material", fill_id=mat_cool),
    ]
    uv = UniverseSpec(id=uv_id, name=f"pin {uv_id}", cell_ids=[c.id for c in cells])
    return uv, cells, surfs, regions


def _grid_decorated_universe(
    base_uv_id: str, grid_hash: str, mat_grid: str,
    r_fuel: float = 0.3, r_clad: float = 0.4,
    pitch: float = 1.25, inner_side: float = 1.1,
) -> tuple[UniverseSpec, list[CellSpec], list[SurfaceSpec], list[RegionSpec]]:
    """Build a grid-decorated universe: cylinders + square_frame + background."""
    decorated_id = f"{base_uv_id}__grid__{grid_hash}"

    # Cylinder surfaces and regions (same as pin)
    surfs = _cyl_surfaces(decorated_id, [r_fuel, r_clad])
    regions_cyl = [
        _cyl_region(decorated_id, [s.id for s in surfs], 0),
        _cyl_region(decorated_id, [s.id for s in surfs], 1),
    ]

    # Square frame surfaces: outer (x_lo, x_hi, y_lo, y_hi) + inner
    half_outer = pitch / 2.0
    half_inner = inner_side / 2.0
    frame_surfs = []
    for tag, val in [("oxl", -half_outer), ("oxh", half_outer),
                     ("oyl", -half_outer), ("oyh", half_outer),
                     ("ixl", -half_inner), ("ixh", half_inner),
                     ("iyl", -half_inner), ("iyh", half_inner)]:
        axis = "xplane" if "x" in tag else "yplane"
        s = SurfaceSpec(id=f"surf_{decorated_id}_{tag}", kind=axis,
                        parameters={"x0" if "x" in tag else "y0": val})
        frame_surfs.append(s)

    # Frame region: between outer and inner squares
    fs = [s.id for s in frame_surfs]
    frame_region = RegionSpec(
        id=f"reg_{decorated_id}_frame",
        expression=f"+{fs[0]} -{fs[1]} +{fs[2]} -{fs[3]} ~ ( +{fs[4]} -{fs[5]} +{fs[6]} -{fs[7]} )",
        surface_ids=fs,
    )

    # Background region: outside cylinder AND NOT in frame
    bg_region = RegionSpec(
        id=f"reg_{decorated_id}_bg",
        expression=f"+{surfs[-1].id} ~ ( +{fs[0]} -{fs[1]} +{fs[2]} -{fs[3]} ~ ( +{fs[4]} -{fs[5]} +{fs[6]} -{fs[7]} ) )",
        surface_ids=[surfs[-1].id] + fs,
    )

    cells = [
        CellSpec(id=f"cell_{decorated_id}_fuel", name=f"fuel {decorated_id}",
                 region_id=regions_cyl[0].id, fill_type="material", fill_id="fuel"),
        CellSpec(id=f"cell_{decorated_id}_clad", name=f"clad {decorated_id}",
                 region_id=regions_cyl[1].id, fill_type="material", fill_id="clad"),
        CellSpec(id=f"cell_{decorated_id}_frame", name=f"grid_frame {decorated_id}",
                 region_id=frame_region.id, fill_type="material", fill_id=mat_grid,
                 component_role="grid_frame"),
        CellSpec(id=f"cell_{decorated_id}_bg", name=f"bg {decorated_id}",
                 region_id=bg_region.id, fill_type="material", fill_id="water"),
    ]

    all_surfs = surfs + frame_surfs
    all_regions = regions_cyl + [frame_region, bg_region]

    uv = UniverseSpec(
        id=decorated_id, name=f"grid-decorated {base_uv_id}",
        cell_ids=[c.id for c in cells],
    )
    return uv, cells, all_surfs, all_regions


def build_synthetic_grid_plan(*, grid_on: bool = True) -> SimulationPlan:
    """Build a minimal synthetic plan with optional spacer-grid geometry.

    2x2 core lattice, single assembly type, 3x3 pin lattice.
    When ``grid_on=True``, one pin universe is replaced with a grid-decorated
    variant in a segment-specific lattice.
    """
    materials = [
        _mat("fuel", "Fuel", 10.0),
        _mat("clad", "Clad", 6.55),
        _mat("water", "Water", 0.99),
        _mat("grid_end_mat", "End grid alloy", 8.19),
    ]

    # Pin universe (template)
    pin_uv, pin_cells, pin_surfs, pin_regions = _pin_universe(
        "fuel_pin", "fuel", "clad", "water",
    )

    all_universes: list[UniverseSpec] = []
    all_cells: list[CellSpec] = []
    all_surfaces: list[SurfaceSpec] = []
    all_regions: list[RegionSpec] = []

    all_universes.append(pin_uv)
    all_cells.extend(pin_cells)
    all_surfaces.extend(pin_surfs)
    all_regions.extend(pin_regions)

    # Moderator outer universe (for lattice outer)
    mod_region = RegionSpec(id="reg_mod_outer", expression="1", surface_ids=[])
    mod_cell = CellSpec(id="cell_mod_outer", name="moderator outer",
                        fill_type="material", fill_id="water")
    mod_uv = UniverseSpec(id="moderator_outer", name="moderator outer",
                          cell_ids=["cell_mod_outer"])
    all_universes.append(mod_uv)
    all_cells.append(mod_cell)
    all_regions.append(mod_region)

    # Grid-decorated universe (if grid_on)
    decorated_id = None
    if grid_on:
        grid_hash = "aabbccdd1122"
        dec_uv, dec_cells, dec_surfs, dec_regions = _grid_decorated_universe(
            "fuel_pin", grid_hash, "grid_end_mat",
        )
        all_universes.append(dec_uv)
        all_cells.extend(dec_cells)
        all_surfaces.extend(dec_surfs)
        all_regions.extend(dec_regions)
        decorated_id = dec_uv.id

    # Assembly lattice (template)
    assembly_lat = LatticeSpec(
        id="assembly_lattice__fuel",
        name="assembly lattice fuel",
        kind="rect",
        pitch_cm=(1.25, 1.25),
        universe_pattern=[
            ["fuel_pin", "fuel_pin", "fuel_pin"],
            ["fuel_pin", "fuel_pin", "fuel_pin"],
            ["fuel_pin", "fuel_pin", "fuel_pin"],
        ],
        outer_universe_id="moderator_outer",
    )

    lattices: list[LatticeSpec] = [assembly_lat]

    # Grid-active segment lattice (if grid_on)
    if grid_on and decorated_id:
        grid_lat = LatticeSpec(
            id="assembly_lattice__fuel__grid_seg0",
            name="assembly lattice fuel grid segment 0",
            kind="rect",
            pitch_cm=(1.25, 1.25),
            universe_pattern=[
                [decorated_id, "fuel_pin", "fuel_pin"],
                ["fuel_pin", "fuel_pin", "fuel_pin"],
                ["fuel_pin", "fuel_pin", "fuel_pin"],
            ],
            outer_universe_id="moderator_outer",
        )
        lattices.append(grid_lat)

    # Assembly wrapper universe
    asm_cell = CellSpec(id="assembly_wrapper_cell__fuel",
                        name="assembly wrapper fuel",
                        fill_type="lattice", fill_id="assembly_lattice__fuel")
    asm_uv = UniverseSpec(id="assembly_universe__fuel",
                          name="assembly fuel",
                          cell_ids=["assembly_wrapper_cell__fuel"])
    all_universes.append(asm_uv)
    all_cells.append(asm_cell)

    # Core lattice
    core_lat = LatticeSpec(
        id="core_lattice",
        name="core lattice",
        kind="rect",
        pitch_cm=(4.0, 4.0),
        universe_pattern=[
            ["assembly_universe__fuel", "assembly_universe__fuel"],
            ["assembly_universe__fuel", "assembly_universe__fuel"],
        ],
        outer_universe_id="moderator_outer",
    )
    lattices.append(core_lat)

    # Axial layers
    axial_layers = [
        AxialLayerSpec(
            id="layer_active",
            name="active fuel",
            z_min_cm=10.0, z_max_cm=90.0,
            fill=FillRefSpec(type="lattice", id="core_lattice"),
        ),
        AxialLayerSpec(
            id="layer_lower",
            name="lower",
            z_min_cm=0.0, z_max_cm=10.0,
            fill=FillRefSpec(type="material", id="water"),
        ),
    ]

    # Axial overlays (grid bands)
    axial_overlays: list[AxialOverlaySpec] = []
    if grid_on:
        axial_overlays = [
            AxialOverlaySpec(
                id="grid_end_bottom",
                overlay_kind="spacer_grid",
                z_min_cm=12.0, z_max_cm=14.0,
                material_id="grid_end_mat",
                geometry_mode="mass_conserving_outer_frame",
            ),
        ]

    core = CoreSpec(
        id="core",
        name="core",
        lattice_id="core_lattice",
        boundary="reflective",
        axial_layers=axial_layers,
        axial_overlays=axial_overlays,
    )

    model = ComplexModelSpec(
        name="synthetic grid core",
        kind="core",
        materials=materials,
        cells=all_cells,
        surfaces=all_surfaces,
        regions=all_regions,
        universes=all_universes,
        lattices=lattices,
        core=core,
        settings=RunSettingsSpec(batches=5, inactive=1, particles=100),
    )

    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        complex_model=model,
        capability_report=RenderCapabilityReport(
            renderability="none",
            is_executable=False,
            supported_renderer="none",
            reasons=["synthetic test plan"],
        ),
        plot_specs=[PlotSpec(basis="xy", origin=(0, 0, 0), width_cm=(8.0, 8.0), filename="core_xy.png")],
        execution_check=ExecutionCheckSpec(enabled=True, settings=RunSettingsSpec()),
    )
    return plan


def build_synthetic_no_grid_plan() -> SimulationPlan:
    """Same plan without grid overlays or decorated universes."""
    return build_synthetic_grid_plan(grid_on=False)


# ---------------------------------------------------------------------------
# Corruption helpers for negative tests
# ---------------------------------------------------------------------------

def corrupt_plan_remove_decorated_universes(plan: SimulationPlan) -> SimulationPlan:
    """Scenario A: overlay exists but decorated universes are removed."""
    model = plan.complex_model
    new_universes = [u for u in model.universes if "__grid__" not in u.id]
    new_cells = [c for c in model.cells if "__grid__" not in c.id]
    return plan.model_copy(update={
        "complex_model": model.model_copy(update={
            "universes": new_universes,
            "cells": new_cells,
        })
    })


def corrupt_plan_remove_lattice_refs(plan: SimulationPlan) -> SimulationPlan:
    """Scenario B: decorated universes exist but lattices don't reference them."""
    model = plan.complex_model
    new_lattices = []
    for lat in model.lattices:
        new_lat = lat.model_copy()
        new_pattern = []
        for row in (new_lat.universe_pattern or []):
            new_row = [uid if "__grid__" not in uid else uid.replace("__grid__", "__normal__") for uid in row]
            new_pattern.append(new_row)
        new_lat.universe_pattern = new_pattern
        new_lattices.append(new_lat)
    return plan.model_copy(update={
        "complex_model": model.model_copy(update={"lattices": new_lattices})
    })


def corrupt_plan_remove_ir_merge(plan: SimulationPlan) -> SimulationPlan:
    """Scenario C: lattice references decorated ID but universe not in catalog."""
    model = plan.complex_model
    decorated = [u for u in model.universes if "__grid__" in u.id]
    if not decorated:
        return plan
    target = decorated[0].id
    new_universes = [u for u in model.universes if u.id != target]
    new_cells = [c for c in model.cells if target not in c.id]
    return plan.model_copy(update={
        "complex_model": model.model_copy(update={
            "universes": new_universes,
            "cells": new_cells,
        })
    })


def corrupt_plan_remove_material_reachability(plan: SimulationPlan) -> SimulationPlan:
    """Scenario D: frame cells exist but grid material not in material catalog."""
    model = plan.complex_model
    new_materials = [m for m in model.materials if m.id != "grid_end_mat"]
    return plan.model_copy(update={
        "complex_model": model.model_copy(update={"materials": new_materials})
    })


def corrupt_plan_make_identical_digest(plan: SimulationPlan) -> SimulationPlan:
    """Scenario E: grid-on and grid-off geometry are identical.

    Takes the grid-off plan (no decorated universes, no frame cells) and
    adds a physical spacer_grid overlay to its core.  The overlay is active
    but no geometry was injected — the geometry is literally identical to
    grid-off.
    """
    plan_off = build_synthetic_no_grid_plan()
    model = plan_off.complex_model
    # Add a physical overlay to the grid-off model
    new_overlays = [
        AxialOverlaySpec(
            id="grid_end_bottom",
            overlay_kind="spacer_grid",
            z_min_cm=12.0, z_max_cm=14.0,
            material_id="grid_end_mat",
            geometry_mode="mass_conserving_outer_frame",
        ),
    ]
    new_core = model.core.model_copy(update={"axial_overlays": new_overlays})
    return plan_off.model_copy(update={
        "complex_model": model.model_copy(update={"core": new_core})
    })
