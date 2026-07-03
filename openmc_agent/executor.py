from pathlib import Path
import re

import openmc

from openmc_agent.schemas import (
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    LatticeSpec,
    MaterialSpec,
    PlotSpec,
    PackedSphereSpec,
    PebbleSpec,
    RegionSpec,
    RunSettingsSpec,
    SimulationPlan,
    SimulationSpec,
    SurfaceSpec,
    TRISOSpec,
    UniverseSpec,
)
from openmc_agent.reachability import (
    ActiveDependencies,
    collect_active_dependencies_from_model,
)


def build_openmc_material(spec: MaterialSpec) -> openmc.Material:
    material = openmc.Material(name=spec.name)
    material.set_density(spec.density_unit, spec.density_value)
    if spec.temperature_k is not None:
        material.temperature = spec.temperature_k
    material.depletable = spec.depletable
    if spec.volume_cm3 is not None:
        material.volume = spec.volume_cm3

    for component in spec.composition:
        if component.kind == "element":
            material.add_element(
                component.name,
                component.percent,
                component.percent_type,
            )
        else:
            material.add_nuclide(
                component.name,
                component.percent,
                component.percent_type,
            )

    for sab_name in spec.sab:
        material.add_s_alpha_beta(sab_name)

    return material


def build_openmc_complex_material(spec: ComplexMaterialSpec) -> openmc.Material:
    if spec.macroscopic is None and (spec.density_unit is None or spec.density_value is None):
        raise ValueError(f"material {spec.id!r} is missing density")
    if spec.macroscopic is None and not spec.composition and not spec.chemical_formula:
        raise ValueError(
            f"material {spec.id!r} is missing composition, chemical_formula, or macroscopic"
        )
    if spec.macroscopic is not None and spec.density_unit not in {None, "macro"}:
        raise ValueError(f"material {spec.id!r} uses macroscopic data with non-macro density")
    if spec.macroscopic is None and _material_has_mixed_percent_types(spec) and spec.chemical_formula is None:
        raise ValueError(
            f"material {spec.id!r} mixes atom and weight percents without "
            "chemical_formula fallback"
        )

    material = openmc.Material(name=spec.name)
    if spec.density_unit is not None and spec.density_value is not None:
        material.set_density(spec.density_unit, spec.density_value)
    if spec.temperature_k is not None:
        material.temperature = spec.temperature_k
    material.depletable = spec.depletable
    if spec.volume_cm3 is not None:
        material.volume = spec.volume_cm3

    if spec.macroscopic is not None:
        material.add_macroscopic(spec.macroscopic)
    elif _use_chemical_formula_for_complex_material(spec):
        material.add_elements_from_formula(
            spec.chemical_formula,
            **_complex_enrichment_kwargs(spec),
        )
    elif spec.composition:
        for component in spec.composition:
            if component.kind == "element":
                material.add_element(
                    component.name,
                    component.percent,
                    component.percent_type,
                    **_complex_enrichment_kwargs(spec),
                )
            else:
                material.add_nuclide(
                    component.name,
                    component.percent,
                    component.percent_type,
                )

    for sab_name in spec.sab:
        material.add_s_alpha_beta(sab_name)

    return material


def render_openmc_script(
    spec: SimulationSpec,
    *,
    settings_override: RunSettingsSpec | None = None,
    plot_specs: list[PlotSpec] | None = None,
) -> str:
    pin_cell = spec.pin_cell
    geometry = pin_cell.geometry
    settings = settings_override or spec.settings
    materials = [pin_cell.fuel, pin_cell.moderator]
    if pin_cell.cladding is not None:
        materials.append(pin_cell.cladding)

    material_blocks = "\n\n".join(
        _render_material_definition(material, f"material_{index}")
        for index, material in enumerate(materials)
    )
    material_names = ", ".join(f"material_{index}" for index in range(len(materials)))

    cladding_setup = ""
    cell_setup = """
fuel_cell = openmc.Cell(name="fuel", fill=material_0, region=-fuel_surface)
moderator_region = +fuel_surface & boundary_region
moderator_cell = openmc.Cell(name="moderator", fill=material_1, region=moderator_region)
cells = [fuel_cell, moderator_cell]
"""
    if pin_cell.cladding is not None:
        cladding_setup = f"""
clad_inner_radius = {geometry.clad_inner_radius_cm!r}
clad_outer_radius = {geometry.clad_outer_radius_cm!r}
clad_inner_surface = openmc.ZCylinder(r=clad_inner_radius)
clad_outer_surface = openmc.ZCylinder(r=clad_outer_radius)
"""
        cell_setup = """
fuel_cell = openmc.Cell(name="fuel", fill=material_0, region=-fuel_surface)
gap_cell = openmc.Cell(name="gap", region=+fuel_surface & -clad_inner_surface)
clad_cell = openmc.Cell(
    name="cladding",
    fill=material_2,
    region=+clad_inner_surface & -clad_outer_surface,
)
moderator_region = +clad_outer_surface & boundary_region
moderator_cell = openmc.Cell(name="moderator", fill=material_1, region=moderator_region)
cells = [fuel_cell, gap_cell, clad_cell, moderator_cell]
"""

    plots_block = _render_plots_block(plot_specs or [])
    energy_mode_block = _render_optional_energy_mode(settings)

    return f'''"""Generated OpenMC pin-cell model for {spec.name}."""

import openmc


{material_blocks}

materials = openmc.Materials([{material_names}])

fuel_radius = {geometry.fuel_radius_cm!r}
pitch = {geometry.pitch_cm!r}
half_pitch = pitch / 2.0

fuel_surface = openmc.ZCylinder(r=fuel_radius)
{cladding_setup}
x_min = openmc.XPlane(x0=-half_pitch, boundary_type="reflective")
x_max = openmc.XPlane(x0=half_pitch, boundary_type="reflective")
y_min = openmc.YPlane(y0=-half_pitch, boundary_type="reflective")
y_max = openmc.YPlane(y0=half_pitch, boundary_type="reflective")
boundary_region = +x_min & -x_max & +y_min & -y_max
{cell_setup}
root_universe = openmc.Universe(cells=cells)
geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
settings.batches = {settings.batches}
settings.inactive = {settings.inactive}
settings.particles = {settings.particles}
{_render_optional_seed(settings)}
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (-half_pitch, -half_pitch, -1.0),
        (half_pitch, half_pitch, 1.0),
        only_fissionable=True,
    )
)

flux_tally = openmc.Tally(name="cell flux")
flux_tally.filters = [openmc.CellFilter(cells)]
flux_tally.scores = ["flux"]
tallies = openmc.Tallies([flux_tally])

model = openmc.Model(
    materials=materials,
    geometry=geometry,
    settings=settings,
    tallies=tallies,
)
model.export_to_xml()
{plots_block}
'''


def render_openmc_assembly_script(
    spec: ComplexModelSpec,
    *,
    settings_override: RunSettingsSpec | None = None,
    plot_specs: list[PlotSpec] | None = None,
) -> str:
    deps = collect_active_dependencies_from_model(spec)
    _validate_renderable_assembly(spec, deps)
    settings = settings_override or spec.settings
    # Only emit objects the default model actually uses. Candidate / inactive
    # subsystems (e.g. an un-inserted burnable-poison universe with an
    # incomplete borosilicate glass) stay in the IR for review, not in model.py.
    renderable_materials = _renderable_assembly_materials(spec, deps)
    active_cells = [cell for cell in spec.cells if cell.id in deps.cell_ids]
    active_universes = [
        universe for universe in spec.universes if universe.id in deps.universe_ids
    ]
    material_blocks = "\n\n".join(
        _render_complex_material_definition(material)
        for material in renderable_materials
    )
    benchmark_imports = _render_benchmark_imports(spec)
    mgxs_setup = _render_mgxs_setup(spec)
    cross_sections_assignment = _render_materials_cross_sections_assignment(spec)
    surfaces_block = _render_surface_definitions(spec.surfaces)
    regions_block = _render_region_definitions(spec.regions)
    cells_block = _render_cell_definitions(active_cells)
    universes_block = _render_universe_definitions(active_universes)
    lattices_block = _render_lattice_definitions(spec.lattices)
    root_block = _render_assembly_root(spec)
    plots_block = _render_plots_block(plot_specs or [])
    energy_mode_block = _render_optional_energy_mode(settings, spec)

    return f'''"""Generated OpenMC assembly model for {spec.name}."""

import openmc
{benchmark_imports}


materials_by_id = {{}}
surfaces = {{}}
regions = {{}}
cells = {{}}
universes = {{}}
lattices = {{}}

{material_blocks}

{mgxs_setup}
materials = openmc.Materials(list(materials_by_id.values()))
{cross_sections_assignment}

{surfaces_block}

{regions_block}

{cells_block}

{universes_block}

{lattices_block}

{root_block}

geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
settings.batches = {settings.batches}
settings.inactive = {settings.inactive}
settings.particles = {settings.particles}
{_render_optional_seed(settings)}
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (assembly_x_min, assembly_y_min, -1.0),
        (assembly_x_max, assembly_y_max, 1.0),
        only_fissionable=True,
    )
)

flux_tally = openmc.Tally(name="assembly cell flux")
flux_tally.filters = [openmc.CellFilter([root_cell])]
flux_tally.scores = ["flux"]
tallies = openmc.Tallies([flux_tally])

model = openmc.Model(
    materials=materials,
    geometry=geometry,
    settings=settings,
    tallies=tallies,
)
model.export_to_xml()
{plots_block}
'''


def render_openmc_triso_script(
    spec: ComplexModelSpec,
    *,
    settings_override: RunSettingsSpec | None = None,
    plot_specs: list[PlotSpec] | None = None,
) -> str:
    _validate_renderable_triso(spec)
    settings = settings_override or spec.settings
    material_blocks = "\n\n".join(
        _render_complex_material_definition(material)
        for material in spec.materials
    )
    benchmark_imports = _render_benchmark_imports(spec)
    mgxs_setup = _render_mgxs_setup(spec)
    cross_sections_assignment = _render_materials_cross_sections_assignment(spec)
    triso = spec.trisos[0]
    pebble = spec.pebbles[0] if spec.pebbles else None
    packed = spec.packed_spheres[0] if spec.packed_spheres else None
    container_radius = _triso_container_radius(triso, pebble)
    fuel_zone_radius = _triso_fuel_zone_radius(triso, pebble, container_radius)
    num_spheres = _triso_num_spheres(packed)
    seed = packed.seed if packed is not None and packed.seed is not None else 1
    matrix_material_id = _triso_matrix_material_id(triso, pebble)
    layer_blocks = _render_triso_layer_universe(triso)
    plots_block = _render_plots_block(plot_specs or [])
    energy_mode_block = _render_optional_energy_mode(settings, spec)

    return f'''"""Generated OpenMC TRISO/pebble model for {spec.name}."""

import openmc
{benchmark_imports}


materials_by_id = {{}}

{material_blocks}

{mgxs_setup}
materials = openmc.Materials(list(materials_by_id.values()))
{cross_sections_assignment}

{layer_blocks}

container_radius = {container_radius!r}
fuel_zone_radius = {fuel_zone_radius!r}
triso_outer_radius = {triso.layers[-1].outer_radius_cm!r}
container_surface = openmc.Sphere(r=container_radius, boundary_type="vacuum")
fuel_zone_surface = openmc.Sphere(r=fuel_zone_radius)
container_region = -container_surface
fuel_zone_region = -fuel_zone_surface

triso_centers = openmc.model.pack_spheres(
    radius=triso_outer_radius,
    region=fuel_zone_region,
    num_spheres={num_spheres},
    seed={seed!r},
)
trisos = [
    openmc.model.TRISO(
        outer_radius=triso_outer_radius,
        fill=triso_universe,
        center=center,
    )
    for center in triso_centers
]

matrix_region = container_region
for triso_cell in trisos:
    matrix_region = matrix_region & ~triso_cell.region
matrix_cell = openmc.Cell(
    name="matrix",
    fill=materials_by_id[{matrix_material_id!r}],
    region=matrix_region,
)
root_universe = openmc.Universe(cells=[matrix_cell, *trisos])
geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
settings.batches = {settings.batches}
settings.inactive = {settings.inactive}
settings.particles = {settings.particles}
{_render_optional_seed(settings)}
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (-container_radius, -container_radius, -container_radius),
        (container_radius, container_radius, container_radius),
        only_fissionable=True,
    )
)

flux_tally = openmc.Tally(name="triso flux")
flux_tally.filters = [openmc.CellFilter([matrix_cell, *trisos])]
flux_tally.scores = ["flux"]
tallies = openmc.Tallies([flux_tally])

model = openmc.Model(
    materials=materials,
    geometry=geometry,
    settings=settings,
    tallies=tallies,
)
model.export_to_xml()
{plots_block}
'''


def render_openmc_core_script(
    spec: ComplexModelSpec,
    *,
    settings_override: RunSettingsSpec | None = None,
    plot_specs: list[PlotSpec] | None = None,
) -> str:
    _validate_renderable_core(spec)
    settings = settings_override or spec.settings
    material_blocks = "\n\n".join(
        _render_complex_material_definition(material)
        for material in spec.materials
    )
    benchmark_imports = _render_benchmark_imports(spec)
    mgxs_setup = _render_mgxs_setup(spec)
    cross_sections_assignment = _render_materials_cross_sections_assignment(spec)
    surfaces_block = _render_surface_definitions(spec.surfaces)
    regions_block = _render_region_definitions(spec.regions)
    cells_block = _render_cell_definitions(spec.cells)
    universes_block = _render_universe_definitions(spec.universes)
    lattices_block = _render_lattice_definitions(spec.lattices)
    assert spec.core is not None
    root_block = _render_lattice_root(
        spec,
        lattice_id=spec.core.lattice_id,
        root_name=spec.core.name,
        boundary=spec.core.boundary,
    )
    plots_block = _render_plots_block(plot_specs or [])
    energy_mode_block = _render_optional_energy_mode(settings, spec)

    return f'''"""Generated OpenMC core model for {spec.name}."""

import openmc
{benchmark_imports}


materials_by_id = {{}}
surfaces = {{}}
regions = {{}}
cells = {{}}
universes = {{}}
lattices = {{}}

{material_blocks}

{mgxs_setup}
materials = openmc.Materials(list(materials_by_id.values()))
{cross_sections_assignment}

{surfaces_block}

{regions_block}

{cells_block}

{universes_block}

{lattices_block}

{root_block}

geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
settings.batches = {settings.batches}
settings.inactive = {settings.inactive}
settings.particles = {settings.particles}
{_render_optional_seed(settings)}
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (assembly_x_min, assembly_y_min, -1.0),
        (assembly_x_max, assembly_y_max, 1.0),
        only_fissionable=True,
    )
)

flux_tally = openmc.Tally(name="core cell flux")
flux_tally.filters = [openmc.CellFilter([root_cell])]
flux_tally.scores = ["flux"]
tallies = openmc.Tallies([flux_tally])

model = openmc.Model(
    materials=materials,
    geometry=geometry,
    settings=settings,
    tallies=tallies,
)
model.export_to_xml()
{plots_block}
'''


def render_openmc_plan_script(
    plan: SimulationPlan,
    *,
    settings_override: RunSettingsSpec | None = None,
) -> str:
    if plan.model_spec is not None:
        return render_openmc_script(
            plan.model_spec,
            settings_override=settings_override,
            plot_specs=plan.plot_specs,
        )
    if (
        plan.complex_model is not None
        and plan.capability_report.supported_renderer == "assembly"
    ):
        return render_openmc_assembly_script(
            plan.complex_model,
            settings_override=settings_override,
            plot_specs=plan.plot_specs,
        )
    if (
        plan.complex_model is not None
        and plan.capability_report.supported_renderer == "triso"
    ):
        return render_openmc_triso_script(
            plan.complex_model,
            settings_override=settings_override,
            plot_specs=plan.plot_specs,
        )
    if (
        plan.complex_model is not None
        and plan.capability_report.supported_renderer == "core"
    ):
        return render_openmc_core_script(
            plan.complex_model,
            settings_override=settings_override,
            plot_specs=plan.plot_specs,
        )
    raise ValueError("SimulationPlan does not contain an executable renderer target")


def render_openmc_smoke_test_script(plan: SimulationPlan) -> str:
    return render_openmc_plan_script(
        plan,
        settings_override=plan.execution_check.settings,
    )


def _render_material_definition(spec: MaterialSpec, variable_name: str) -> str:
    lines = [
        f'{variable_name} = openmc.Material(name={spec.name!r})',
        f"{variable_name}.set_density({spec.density_unit!r}, {spec.density_value!r})",
    ]
    if spec.temperature_k is not None:
        lines.append(f"{variable_name}.temperature = {spec.temperature_k!r}")
    if spec.depletable:
        lines.append(f"{variable_name}.depletable = {spec.depletable!r}")
    if spec.volume_cm3 is not None:
        lines.append(f"{variable_name}.volume = {spec.volume_cm3!r}")
    for component in spec.composition:
        if component.kind == "element":
            lines.append(
                f"{variable_name}.add_element("
                f"{component.name!r}, {component.percent!r}, {component.percent_type!r})"
            )
        else:
            lines.append(
                f"{variable_name}.add_nuclide("
                f"{component.name!r}, {component.percent!r}, {component.percent_type!r})"
            )
    for sab_name in spec.sab:
        lines.append(f"{variable_name}.add_s_alpha_beta({sab_name!r})")
    return "\n".join(lines)


def _validate_renderable_assembly(spec: ComplexModelSpec, deps: ActiveDependencies) -> None:
    if spec.kind != "assembly":
        raise ValueError(f"assembly renderer requires kind='assembly', got {spec.kind!r}")
    if not spec.materials:
        raise ValueError("assembly renderer requires materials")
    if not spec.cells:
        raise ValueError("assembly renderer requires cells")
    if not spec.universes:
        raise ValueError("assembly renderer requires universes")
    if not spec.lattices:
        raise ValueError("assembly renderer requires a RectLattice")
    if not spec.assemblies or spec.assemblies[0].lattice_id is None:
        raise ValueError("assembly renderer requires an AssemblySpec with lattice_id")
    if spec.lattices[0].kind != "rect":
        raise ValueError("assembly renderer currently supports RectLattice only")
    # Only materials reachable from the default lattice (or used by reflectors /
    # control rods) must be complete. Candidate / inactive materials may stay
    # incomplete; the capability layer already warned about them and they are
    # skipped at render time.
    for material in spec.materials:
        if material.id not in deps.material_ids:
            continue
        if _material_is_macroscopic(material):
            continue
        if material.density_unit is None or material.density_value is None:
            raise ValueError(f"material {material.id!r} is missing density")
        if not material.composition and not material.chemical_formula:
            raise ValueError(
                f"material {material.id!r} is missing composition, "
                "chemical_formula, or macroscopic"
            )
        if _material_has_mixed_percent_types(material) and material.chemical_formula is None:
            raise ValueError(
                f"material {material.id!r} mixes atom and weight percents without "
                "chemical_formula fallback"
            )
    cell_ids = {cell.id for cell in spec.cells}
    for universe in spec.universes:
        if universe.id not in deps.universe_ids:
            continue
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing:
            raise ValueError(f"universe {universe.id!r} references missing cells: {missing}")
    material_ids = {material.id for material in spec.materials}
    region_ids = {region.id for region in spec.regions}
    lattice_universe_ids = {
        universe_id
        for lattice in spec.lattices
        for row in lattice.universe_pattern
        for universe_id in row
    }
    for reflector in spec.reflectors:
        if reflector.material_id not in material_ids:
            raise ValueError(f"reflector {reflector.id!r} references missing material")
        if reflector.region_id is None or reflector.region_id not in region_ids:
            raise ValueError(f"reflector {reflector.id!r} requires a valid region_id")
    for control_rod in spec.control_rods:
        if control_rod.absorber_material_id not in material_ids:
            raise ValueError(f"control rod {control_rod.id!r} references missing absorber material")
        if control_rod.guide_tube_region_id is not None and control_rod.guide_tube_region_id not in region_ids:
            raise ValueError(f"control rod {control_rod.id!r} references missing guide_tube_region_id")
        if control_rod.guide_tube_region_id is None and not any(
            position_id in lattice_universe_ids for position_id in control_rod.position_ids
        ):
            raise ValueError(
                f"control rod {control_rod.id!r} must reference a lattice universe position "
                "or a guide_tube_region_id"
            )


def _material_is_fully_defined(material: ComplexMaterialSpec) -> bool:
    """True when a material can be rendered without human-filled fields."""
    if _material_is_macroscopic(material):
        return True
    has_density = material.density_unit is not None and material.density_value is not None
    has_composition = bool(material.composition or material.chemical_formula)
    return has_density and has_composition


def _material_is_macroscopic(material: ComplexMaterialSpec) -> bool:
    return material.macroscopic is not None


def _material_has_mixed_percent_types(material: ComplexMaterialSpec) -> bool:
    percent_types = {component.percent_type for component in material.composition}
    return len(percent_types) > 1


def _use_chemical_formula_for_complex_material(material: ComplexMaterialSpec) -> bool:
    """Prefer formula rendering when explicit components are not OpenMC-safe.

    OpenMC rejects a material card that mixes ``ao`` and ``wo`` entries. LLMs
    commonly describe enriched UO2 as U isotopes in weight percent plus oxygen
    stoichiometry in atom ratio. When a chemical formula is available, render
    the formula with enrichment instead of emitting an invalid mixed card.
    """
    if material.chemical_formula is None:
        return False
    return not material.composition or _material_has_mixed_percent_types(material)


def _renderable_assembly_materials(
    spec: ComplexModelSpec,
    deps: ActiveDependencies,
) -> list[ComplexMaterialSpec]:
    """Materials to emit into the default model.py.

    Active materials are always emitted. Inactive materials are emitted only when
    fully defined, so a candidate burnable-poison material with a partial density
    is dropped instead of producing broken ``set_density`` code, while a complete
    but currently-unused material still renders (harmless, and robust to any
    reachability gap for non-assembly subsystems).
    """
    emitted: list[ComplexMaterialSpec] = []
    for material in spec.materials:
        if material.id in deps.material_ids or _material_is_fully_defined(material):
            emitted.append(material)
    return emitted


def _validate_renderable_triso(spec: ComplexModelSpec) -> None:
    if spec.kind not in {"triso_compact", "pebble"}:
        raise ValueError("triso renderer requires kind='triso_compact' or 'pebble'")
    if not spec.materials:
        raise ValueError("triso renderer requires materials")
    if not spec.trisos:
        raise ValueError("triso renderer requires at least one TRISOSpec")
    material_ids = {material.id for material in spec.materials}
    for material in spec.materials:
        if _material_is_macroscopic(material):
            continue
        if material.density_unit is None or material.density_value is None:
            raise ValueError(f"material {material.id!r} is missing density")
        if not material.composition and not material.chemical_formula:
            raise ValueError(
                f"material {material.id!r} is missing composition, "
                "chemical_formula, or macroscopic"
            )
        if _material_has_mixed_percent_types(material) and material.chemical_formula is None:
            raise ValueError(
                f"material {material.id!r} mixes atom and weight percents without "
                "chemical_formula fallback"
            )

    triso = spec.trisos[0]
    missing_layers = [
        layer.material_id for layer in triso.layers if layer.material_id not in material_ids
    ]
    if missing_layers:
        raise ValueError(f"TRISO layers reference missing materials: {missing_layers}")
    matrix_material_id = _triso_matrix_material_id(triso, spec.pebbles[0] if spec.pebbles else None)
    if matrix_material_id not in material_ids:
        raise ValueError("triso renderer requires a matrix material present in materials")
    pebble = spec.pebbles[0] if spec.pebbles else None
    container_radius = _triso_container_radius(triso, pebble)
    fuel_zone_radius = _triso_fuel_zone_radius(triso, pebble, container_radius)
    if fuel_zone_radius > container_radius:
        raise ValueError("TRISO fuel zone radius must not exceed container radius")
    if triso.layers[-1].outer_radius_cm >= fuel_zone_radius:
        raise ValueError("TRISO outer radius must be less than fuel zone radius")


def _validate_renderable_core(spec: ComplexModelSpec) -> None:
    if spec.kind != "core":
        raise ValueError("core renderer requires kind='core'")
    if spec.core is None or spec.core.lattice_id is None:
        raise ValueError("core renderer requires CoreSpec.lattice_id")
    if not spec.materials:
        raise ValueError("core renderer requires materials")
    if not spec.cells:
        raise ValueError("core renderer requires cells")
    if not spec.universes:
        raise ValueError("core renderer requires universes")
    if not spec.lattices:
        raise ValueError("core renderer requires a RectLattice")
    if all(lattice.id != spec.core.lattice_id for lattice in spec.lattices):
        raise ValueError(f"core references missing lattice_id={spec.core.lattice_id!r}")
    if any(lattice.kind != "rect" for lattice in spec.lattices):
        raise ValueError("core renderer currently supports RectLattice only")
    for material in spec.materials:
        if _material_is_macroscopic(material):
            continue
        if material.density_unit is None or material.density_value is None:
            raise ValueError(f"material {material.id!r} is missing density")
        if not material.composition and not material.chemical_formula:
            raise ValueError(
                f"material {material.id!r} is missing composition, "
                "chemical_formula, or macroscopic"
            )
        if _material_has_mixed_percent_types(material) and material.chemical_formula is None:
            raise ValueError(
                f"material {material.id!r} mixes atom and weight percents without "
                "chemical_formula fallback"
            )
    cell_ids = {cell.id for cell in spec.cells}
    for universe in spec.universes:
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing:
            raise ValueError(f"universe {universe.id!r} references missing cells: {missing}")


def _complex_enrichment_kwargs(spec: ComplexMaterialSpec) -> dict[str, str | float]:
    if spec.enrichment_percent is None:
        return {}
    kwargs: dict[str, str | float] = {"enrichment": spec.enrichment_percent}
    if spec.enrichment_target and spec.enrichment_target != "U235":
        kwargs["enrichment_target"] = spec.enrichment_target
    if spec.enrichment_type and spec.enrichment_target != "U235":
        kwargs["enrichment_type"] = spec.enrichment_type
    return kwargs


def _render_complex_enrichment_args(spec: ComplexMaterialSpec) -> str:
    kwargs = _complex_enrichment_kwargs(spec)
    if not kwargs:
        return ""
    return "".join(f", {key}={value!r}" for key, value in kwargs.items())


def _render_complex_material_definition(spec: ComplexMaterialSpec) -> str:
    variable_name = _safe_name("material", spec.id)
    enrichment_args = _render_complex_enrichment_args(spec)
    lines = [
        f"{variable_name} = openmc.Material(name={spec.name!r})",
    ]
    if spec.density_unit is not None and spec.density_value is not None:
        lines.append(f"{variable_name}.set_density({spec.density_unit!r}, {spec.density_value!r})")
    if spec.temperature_k is not None:
        lines.append(f"{variable_name}.temperature = {spec.temperature_k!r}")
    if spec.depletable:
        lines.append(f"{variable_name}.depletable = {spec.depletable!r}")
    if spec.volume_cm3 is not None:
        lines.append(f"{variable_name}.volume = {spec.volume_cm3!r}")
    if spec.macroscopic is not None:
        lines.append(f"{variable_name}.add_macroscopic({spec.macroscopic!r})")
    elif _use_chemical_formula_for_complex_material(spec):
        lines.append(
            f"{variable_name}.add_elements_from_formula("
            f"{spec.chemical_formula!r}"
            f"{enrichment_args})"
        )
    elif spec.composition:
        for component in spec.composition:
            if component.kind == "element":
                lines.append(
                    f"{variable_name}.add_element("
                    f"{component.name!r}, {component.percent!r}, {component.percent_type!r}"
                    f"{enrichment_args})"
                )
            else:
                lines.append(
                    f"{variable_name}.add_nuclide("
                    f"{component.name!r}, {component.percent!r}, {component.percent_type!r})"
                )
    for sab_name in spec.sab:
        lines.append(f"{variable_name}.add_s_alpha_beta({sab_name!r})")
    lines.append(f"materials_by_id[{spec.id!r}] = {variable_name}")
    return "\n".join(lines)


def _render_triso_layer_universe(triso: TRISOSpec) -> str:
    lines: list[str] = []
    previous_surface = ""
    cell_names: list[str] = []
    for index, layer in enumerate(triso.layers):
        surface_name = _safe_name("triso_surface", layer.name)
        cell_name = _safe_name("triso_cell", layer.name)
        lines.append(f"{surface_name} = openmc.Sphere(r={layer.outer_radius_cm!r})")
        if index == 0:
            region_expr = f"-{surface_name}"
        else:
            region_expr = f"+{previous_surface} & -{surface_name}"
        lines.append(
            f"{cell_name} = openmc.Cell("
            f"name={layer.name!r}, "
            f"fill=materials_by_id[{layer.material_id!r}], "
            f"region={region_expr})"
        )
        cell_names.append(cell_name)
        previous_surface = surface_name
    lines.append(f"triso_universe = openmc.Universe(cells=[{', '.join(cell_names)}])")
    return "\n".join(lines)


def _triso_matrix_material_id(triso: TRISOSpec, pebble: PebbleSpec | None) -> str:
    if pebble is not None and pebble.matrix_material_id is not None:
        return pebble.matrix_material_id
    if triso.matrix_material_id is not None:
        return triso.matrix_material_id
    raise ValueError("TRISO renderer requires matrix_material_id")


def _triso_container_radius(triso: TRISOSpec, pebble: PebbleSpec | None) -> float:
    if pebble is not None:
        return pebble.outer_radius_cm
    return triso.layers[-1].outer_radius_cm * 5.0


def _triso_fuel_zone_radius(
    triso: TRISOSpec,
    pebble: PebbleSpec | None,
    container_radius: float,
) -> float:
    if pebble is not None and pebble.fuel_zone_radius_cm is not None:
        return pebble.fuel_zone_radius_cm
    return container_radius


def _triso_num_spheres(packed: PackedSphereSpec | None) -> int:
    if packed is None or packed.num_spheres is None:
        return 1
    return packed.num_spheres


def _render_surface_definitions(surface_specs: list[SurfaceSpec]) -> str:
    lines: list[str] = []
    for surface in surface_specs:
        variable_name = _safe_name("surface", surface.id)
        lines.append(f"{variable_name} = {_surface_constructor(surface)}")
        lines.append(f"surfaces[{surface.id!r}] = {variable_name}")
        if surface.kind in {"rectangular_prism", "hexagonal_prism"}:
            lines.append(f"regions[{surface.id!r}] = {variable_name}")
    return "\n".join(lines) if lines else "# No explicit surfaces were provided."


def _surface_constructor(surface: SurfaceSpec) -> str:
    params = dict(surface.parameters)
    if surface.boundary_type is not None:
        params["boundary_type"] = surface.boundary_type
    if surface.kind == "rectangular_prism":
        return _composite_surface_constructor(
            "openmc.model.RectangularPrism",
            _rectangular_prism_kwargs(params),
        )
    if surface.kind == "hexagonal_prism":
        if "pitch" in params and "edge_length" not in params:
            params["edge_length"] = params.pop("pitch")
        return _composite_surface_constructor("openmc.model.HexagonalPrism", params)
    constructor_by_kind = {
        "xplane": "openmc.XPlane",
        "yplane": "openmc.YPlane",
        "zplane": "openmc.ZPlane",
        "plane": "openmc.Plane",
        "zcylinder": "openmc.ZCylinder",
        "ycylinder": "openmc.YCylinder",
        "xcylinder": "openmc.XCylinder",
        "sphere": "openmc.Sphere",
    }
    constructor = constructor_by_kind.get(surface.kind)
    if constructor is None:
        raise ValueError(f"surface kind {surface.kind!r} is not supported by assembly renderer")
    args = ", ".join(f"{key}={value!r}" for key, value in sorted(params.items()))
    return f"{constructor}({args})"


def _composite_surface_constructor(constructor: str, params: dict[str, object]) -> str:
    args = ", ".join(f"{key}={value!r}" for key, value in sorted(params.items()))
    return f"(-{constructor}({args}))"


def _rectangular_prism_kwargs(params: dict[str, object]) -> dict[str, object]:
    """Normalize bounded rectangular-prism params to OpenMC's width/height API."""
    if "width" in params and "height" in params:
        return params

    intervals = {
        "x": _pop_interval(params, ("xmin", "x_min"), ("xmax", "x_max")),
        "y": _pop_interval(params, ("ymin", "y_min"), ("ymax", "y_max")),
        "z": _pop_interval(params, ("zmin", "z_min"), ("zmax", "z_max")),
    }
    present_axes = {axis: value for axis, value in intervals.items() if value is not None}
    if not present_axes:
        return params
    if len(present_axes) != 2:
        raise ValueError(
            "rectangular_prism requires exactly two bounded axes "
            "(for example xmin/xmax/ymin/ymax)"
        )

    axes = set(present_axes)
    if axes == {"x", "y"}:
        prism_axis = "z"
        first_axis, second_axis = "x", "y"
    elif axes == {"x", "z"}:
        prism_axis = "y"
        first_axis, second_axis = "x", "z"
    elif axes == {"y", "z"}:
        prism_axis = "x"
        first_axis, second_axis = "y", "z"
    else:
        raise ValueError(f"unsupported rectangular_prism axes: {sorted(axes)}")

    axis_hint = params.pop("axis", None)
    if axis_hint is not None and axis_hint != prism_axis:
        raise ValueError(
            f"rectangular_prism axis={axis_hint!r} conflicts with "
            f"{sorted(axes)} bounds"
        )

    first_min, first_max = present_axes[first_axis]
    second_min, second_max = present_axes[second_axis]
    params["axis"] = prism_axis
    params["height"] = second_max - second_min
    params["origin"] = (
        (first_min + first_max) / 2.0,
        (second_min + second_max) / 2.0,
    )
    params["width"] = first_max - first_min
    return params


def _pop_interval(
    params: dict[str, object],
    min_keys: tuple[str, ...],
    max_keys: tuple[str, ...],
) -> tuple[float, float] | None:
    lower = _pop_any(params, min_keys)
    upper = _pop_any(params, max_keys)
    if lower is None and upper is None:
        return None
    if lower is None:
        upper_value = _as_float(upper)
        lower_value = -upper_value
    elif upper is None:
        lower_value = _as_float(lower)
        upper_value = -lower_value
    else:
        lower_value = _as_float(lower)
        upper_value = _as_float(upper)
    if upper_value <= lower_value:
        raise ValueError(
            f"rectangular_prism bound max must exceed min, got {lower_value}..{upper_value}"
        )
    return lower_value, upper_value


def _pop_any(params: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in params:
            return params.pop(key)
    return None


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rectangular_prism bound must be numeric, got {value!r}") from exc


def _render_region_definitions(region_specs: list[RegionSpec]) -> str:
    lines: list[str] = []
    for region in region_specs:
        variable_name = _safe_name("region", region.id)
        lines.append(f"{variable_name} = {_region_expression_to_python(region.expression)}")
        lines.append(f"regions[{region.id!r}] = {variable_name}")
    return "\n".join(lines) if lines else "# No explicit regions were provided."


def _region_expression_to_python(expression: str) -> str:
    token_pattern = re.compile(r"\s*([()+\-&|~]|[A-Za-z_][A-Za-z0-9_\-]*)")
    tokens: list[str] = []
    index = 0
    pending_sign = ""
    while index < len(expression):
        if expression[index:].strip() == "":
            break
        match = token_pattern.match(expression, index)
        if match is None:
            raise ValueError(f"unsupported region expression near {expression[index:]!r}")
        token = match.group(1)
        index = match.end()
        if token in {"+", "-"}:
            pending_sign = token
            continue
        if token == "(":
            if pending_sign:
                raise ValueError(f"dangling half-space sign {pending_sign!r} in region expression")
            if _needs_implicit_intersection(tokens):
                tokens.append("&")
            tokens.append(token)
            continue
        if token in {")", "&", "|", "~"}:
            if pending_sign:
                raise ValueError(f"dangling half-space sign {pending_sign!r} in region expression")
            tokens.append(token)
            continue
        if _needs_implicit_intersection(tokens):
            tokens.append("&")
        if pending_sign:
            tokens.append(f"({pending_sign}surfaces[{token!r}])")
            pending_sign = ""
        else:
            tokens.append(f"surfaces[{token!r}]")
    if pending_sign:
        raise ValueError(f"dangling half-space sign {pending_sign!r} in region expression")
    return " ".join(tokens)


def _needs_implicit_intersection(tokens: list[str]) -> bool:
    """True when MCNP/OpenMC-style adjacency should become Python ``&``."""
    return bool(tokens) and tokens[-1] not in {"(", "&", "|", "~"}


def _render_cell_definitions(cell_specs: list[CellSpec]) -> str:
    lines: list[str] = []
    for cell in cell_specs:
        variable_name = _safe_name("cell", cell.id)
        fill_expr = _cell_fill_expression(cell)
        region_expr = f"regions[{cell.region_id!r}]" if cell.region_id is not None else "None"
        lines.append(
            f"{variable_name} = openmc.Cell("
            f"name={cell.name!r}, fill={fill_expr}, region={region_expr})"
        )
        if cell.temperature_k is not None:
            lines.append(f"{variable_name}.temperature = {cell.temperature_k!r}")
        lines.append(f"cells[{cell.id!r}] = {variable_name}")
    return "\n".join(lines)


def _cell_fill_expression(cell: CellSpec) -> str:
    if cell.fill_type == "void":
        return "None"
    if cell.fill_type == "material":
        return f"materials_by_id[{cell.fill_id!r}]"
    if cell.fill_type == "universe":
        return f"universes[{cell.fill_id!r}]"
    if cell.fill_type == "lattice":
        return f"lattices[{cell.fill_id!r}]"
    raise ValueError(f"unsupported fill_type {cell.fill_type!r}")


def _render_universe_definitions(universe_specs: list[UniverseSpec]) -> str:
    lines: list[str] = []
    for universe in universe_specs:
        variable_name = _safe_name("universe", universe.id)
        cell_refs = ", ".join(f"cells[{cell_id!r}]" for cell_id in universe.cell_ids)
        lines.append(
            f"{variable_name} = openmc.Universe(name={universe.name!r}, cells=[{cell_refs}])"
        )
        lines.append(f"universes[{universe.id!r}] = {variable_name}")
    return "\n".join(lines)


def _render_lattice_definitions(lattice_specs: list[LatticeSpec]) -> str:
    lines: list[str] = []
    for lattice in lattice_specs:
        if lattice.kind != "rect":
            raise ValueError("assembly renderer currently supports RectLattice only")
        variable_name = _safe_name("lattice", lattice.id)
        pitch = _rect_lattice_pitch(lattice)
        lower_left = lattice.lower_left_cm or _infer_rect_lattice_lower_left(lattice)
        lines.extend(
            [
                f"{variable_name} = openmc.RectLattice(name={lattice.name!r})",
                f"{variable_name}.pitch = {pitch!r}",
                f"{variable_name}.lower_left = {lower_left!r}",
                f"{variable_name}.universes = {_render_universe_pattern(lattice.universe_pattern)}",
            ]
        )
        if lattice.outer_universe_id is not None:
            lines.append(f"{variable_name}.outer = universes[{lattice.outer_universe_id!r}]")
        lines.append(f"lattices[{lattice.id!r}] = {variable_name}")
    return "\n".join(lines)


def _render_assembly_root(spec: ComplexModelSpec) -> str:
    assembly = spec.assemblies[0]
    return _render_lattice_root(
        spec,
        lattice_id=assembly.lattice_id,
        root_name=assembly.name,
        boundary=assembly.boundary,
    )


def _render_lattice_root(
    spec: ComplexModelSpec,
    *,
    lattice_id: str | None,
    root_name: str,
    boundary: str | None,
) -> str:
    lattice = _lattice_by_id(spec, lattice_id)
    pitch_x, pitch_y = _rect_lattice_pitch(lattice)
    rows = len(lattice.universe_pattern)
    cols = len(lattice.universe_pattern[0])
    lower_left_x, lower_left_y = lattice.lower_left_cm or _infer_rect_lattice_lower_left(lattice)
    upper_right_x = lower_left_x + cols * pitch_x
    upper_right_y = lower_left_y + rows * pitch_y
    boundary_type = boundary if boundary in {"transmission", "vacuum", "reflective", "white"} else "vacuum"
    extra_cells_block, extra_cell_refs = _render_root_extra_cells(spec)
    root_cells = ", ".join(["root_cell", *extra_cell_refs])
    return f'''assembly_x_min = {lower_left_x!r}
assembly_x_max = {upper_right_x!r}
assembly_y_min = {lower_left_y!r}
assembly_y_max = {upper_right_y!r}
assembly_xmin = openmc.XPlane(x0=assembly_x_min, boundary_type={boundary_type!r})
assembly_xmax = openmc.XPlane(x0=assembly_x_max, boundary_type={boundary_type!r})
assembly_ymin = openmc.YPlane(y0=assembly_y_min, boundary_type={boundary_type!r})
assembly_ymax = openmc.YPlane(y0=assembly_y_max, boundary_type={boundary_type!r})
assembly_boundary_region = +assembly_xmin & -assembly_xmax & +assembly_ymin & -assembly_ymax
root_cell = openmc.Cell(
    name={root_name!r},
    fill=lattices[{lattice_id!r}],
    region=assembly_boundary_region,
)
{extra_cells_block}
root_universe = openmc.Universe(cells=[{root_cells}])'''


def _render_root_extra_cells(spec: ComplexModelSpec) -> tuple[str, list[str]]:
    lines: list[str] = []
    refs: list[str] = []
    for reflector in spec.reflectors:
        if reflector.region_id is None:
            continue
        variable_name = _safe_name("reflector_cell", reflector.id)
        lines.append(
            f"{variable_name} = openmc.Cell("
            f"name={reflector.name!r}, "
            f"fill=materials_by_id[{reflector.material_id!r}], "
            f"region=regions[{reflector.region_id!r}])"
        )
        refs.append(variable_name)

    for control_rod in spec.control_rods:
        if control_rod.guide_tube_region_id is None:
            continue
        variable_name = _safe_name("control_rod_cell", control_rod.id)
        lines.append(
            f"{variable_name} = openmc.Cell("
            f"name={control_rod.name!r}, "
            f"fill=materials_by_id[{control_rod.absorber_material_id!r}], "
            f"region=regions[{control_rod.guide_tube_region_id!r}])"
        )
        refs.append(variable_name)

    return ("\n".join(lines), refs)


def _lattice_by_id(spec: ComplexModelSpec, lattice_id: str | None) -> LatticeSpec:
    for lattice in spec.lattices:
        if lattice.id == lattice_id:
            return lattice
    raise ValueError(f"assembly references missing lattice_id={lattice_id!r}")


def _rect_lattice_pitch(lattice: LatticeSpec) -> tuple[float, float]:
    if len(lattice.pitch_cm) == 1:
        return (lattice.pitch_cm[0], lattice.pitch_cm[0])
    return (lattice.pitch_cm[0], lattice.pitch_cm[1])


def _infer_rect_lattice_lower_left(lattice: LatticeSpec) -> tuple[float, float]:
    pitch_x, pitch_y = _rect_lattice_pitch(lattice)
    rows = len(lattice.universe_pattern)
    cols = len(lattice.universe_pattern[0])
    return (-cols * pitch_x / 2.0, -rows * pitch_y / 2.0)


def _render_universe_pattern(pattern: list[list[str]]) -> str:
    rows = []
    for row in pattern:
        rows.append("[" + ", ".join(f"universes[{universe_id!r}]" for universe_id in row) + "]")
    return "[" + ", ".join(rows) + "]"


def _safe_name(prefix: str, identifier: str) -> str:
    safe = re.sub(r"\W+", "_", identifier).strip("_")
    if not safe or safe[0].isdigit():
        safe = f"{prefix}_{safe}"
    return f"{prefix}_{safe}"


def _render_optional_seed(settings: RunSettingsSpec) -> str:
    if settings.seed is None:
        return ""
    return f"settings.seed = {settings.seed}"


def _model_uses_mgxs(spec: ComplexModelSpec | None = None) -> bool:
    if spec is None:
        return False
    return bool(spec.standard_mgxs_library or spec.mg_cross_sections_file)


def _render_optional_energy_mode(
    settings: RunSettingsSpec,
    spec: ComplexModelSpec | None = None,
) -> str:
    energy_mode = settings.energy_mode
    if energy_mode is None and _model_uses_mgxs(spec):
        energy_mode = "multi-group"
    if energy_mode is None:
        return ""
    return f"settings.energy_mode = {energy_mode!r}"


def _render_benchmark_imports(spec: ComplexModelSpec) -> str:
    if spec.standard_mgxs_library == "c5g7":
        return "\nfrom openmc_agent.benchmarks.c5g7 import export_mgxs_hdf5\n"
    return ""


def _mgxs_filename(spec: ComplexModelSpec) -> str:
    return spec.mg_cross_sections_file or "mgxs.h5"


def _render_mgxs_setup(spec: ComplexModelSpec) -> str:
    if spec.standard_mgxs_library == "c5g7":
        filename = _mgxs_filename(spec)
        return (
            f"mg_cross_sections_file = {filename!r}\n"
            "export_mgxs_hdf5(mg_cross_sections_file)\n"
        )
    if spec.mg_cross_sections_file:
        return f"mg_cross_sections_file = {spec.mg_cross_sections_file!r}\n"
    return ""


def _render_materials_cross_sections_assignment(spec: ComplexModelSpec) -> str:
    if not _model_uses_mgxs(spec):
        return ""
    return "materials.cross_sections = mg_cross_sections_file"


def _render_plots_block(plot_specs: list[PlotSpec]) -> str:
    if not plot_specs:
        return ""

    blocks: list[str] = ["", "# Geometry plots selected by the structured plan."]
    plot_names: list[str] = []
    for index, plot in enumerate(plot_specs):
        variable_name = f"plot_{index}"
        plot_names.append(variable_name)
        filename = _openmc_plot_filename(plot.filename)
        blocks.extend(
            [
                f"{variable_name} = openmc.Plot()",
                f"{variable_name}.basis = {plot.basis!r}",
                f"{variable_name}.origin = {plot.origin!r}",
                f"{variable_name}.width = {plot.width_cm!r}",
                f"{variable_name}.pixels = {plot.pixels!r}",
                f"{variable_name}.color_by = {plot.color_by!r}",
                f"{variable_name}.filename = {filename!r}",
                "",
            ]
        )

    blocks.append(f"plots = openmc.Plots([{', '.join(plot_names)}])")
    blocks.append("plots.export_to_xml()")
    return "\n".join(blocks)


def _openmc_plot_filename(filename: str) -> str:
    path = Path(filename)
    if path.suffix.lower() == ".png":
        return str(path.with_suffix(""))
    return filename
