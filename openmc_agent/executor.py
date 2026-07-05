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
    if _material_has_mixed_percent_types(spec) and spec.chemical_formula is None:
        raise ValueError(
            f"material {spec.name!r} mixes atom and weight percents without "
            "chemical_formula fallback"
        )
    material = openmc.Material(name=spec.name)
    material.set_density(spec.density_unit, spec.density_value)
    if spec.temperature_k is not None:
        material.temperature = spec.temperature_k
    material.depletable = spec.depletable
    if spec.volume_cm3 is not None:
        material.volume = spec.volume_cm3

    if _use_chemical_formula_for_material(spec):
        material.add_elements_from_formula(
            spec.chemical_formula,
            **_material_enrichment_kwargs(spec),
        )
    else:
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
    temperature_block = _render_optional_temperature_interpolation(settings)

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
{temperature_block}
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
    regions_block = _render_region_definitions(spec.regions, spec.surfaces)
    cells_block = _render_cell_definitions(active_cells)
    universes_block = _render_universe_definitions(active_universes)
    lattices_block = _render_lattice_definitions(spec.lattices)
    cell_fill_assignments = _render_cell_fill_assignments(active_cells)
    root_block = _render_assembly_root(spec)
    plots_block = _render_plots_block(plot_specs or [])
    energy_mode_block = _render_optional_energy_mode(settings, spec)
    temperature_block = _render_optional_temperature_interpolation(settings, spec)

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

{cell_fill_assignments}

{root_block}

geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
{temperature_block}
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
    temperature_block = _render_optional_temperature_interpolation(settings, spec)

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
{temperature_block}
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
    spec = _normalize_core_spec_for_rendering(spec)
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
    regions_block = _render_region_definitions(spec.regions, spec.surfaces)
    cells_block = _render_cell_definitions(spec.cells)
    universes_block = _render_universe_definitions(spec.universes)
    lattices_block = _render_lattice_definitions(spec.lattices)
    cell_fill_assignments = _render_cell_fill_assignments(spec.cells)
    core_universe_wrappers = "# Core universes were normalized before rendering."
    assert spec.core is not None
    root_block = _render_core_root(spec)
    plot_specs = _reconcile_plot_origins(spec, plot_specs or [])
    plots_block = _render_plots_block(plot_specs)
    energy_mode_block = _render_optional_energy_mode(settings, spec)
    temperature_block = _render_optional_temperature_interpolation(settings, spec)

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

{cell_fill_assignments}

{core_universe_wrappers}

{root_block}

geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = {settings.run_mode!r}
{energy_mode_block}
{temperature_block}
settings.batches = {settings.batches}
settings.inactive = {settings.inactive}
settings.particles = {settings.particles}
{_render_optional_seed(settings)}
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (assembly_x_min, assembly_y_min, assembly_z_min),
        (assembly_x_max, assembly_y_max, assembly_z_max),
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
    if _use_chemical_formula_for_material(spec):
        enrichment_args = _render_material_enrichment_args(spec)
        lines.append(
            f"{variable_name}.add_elements_from_formula("
            f"{spec.chemical_formula!r}{enrichment_args})"
        )
    else:
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


def _material_has_mixed_percent_types(material: MaterialSpec | ComplexMaterialSpec) -> bool:
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


def _use_chemical_formula_for_material(spec: MaterialSpec) -> bool:
    """Pin-cell counterpart of :func:`_use_chemical_formula_for_complex_material`.

    A pin-cell ``MaterialSpec`` always carries an explicit composition, so the
    formula fallback only needs to engage when those entries mix ``ao`` and
    ``wo`` percent types and a ``chemical_formula`` is available to fall back to.
    """
    if spec.chemical_formula is None:
        return False
    return _material_has_mixed_percent_types(spec)


def _material_enrichment_kwargs(spec: MaterialSpec) -> dict[str, float | str]:
    """Build enrichment kwargs for ``add_elements_from_formula`` on a pin-cell spec.

    Prefers an explicit ``enrichment_percent``. When it is missing, fall back to
    the ``U235`` weight percent found in ``composition``: plans commonly record
    the enrichment on the U235 nuclide entry (per benchmark tables such as
    VERA's wt% isotopics) and leave ``enrichment_percent`` null, so this recovers
    the intended enrichment for the chemical-formula fallback instead of
    silently rendering natural-uranium UO2.
    """
    enrichment = spec.enrichment_percent
    if enrichment is None:
        target = spec.enrichment_target or "U235"
        for component in spec.composition:
            if component.name == target and component.percent_type == "wo":
                enrichment = component.percent
                break
    if enrichment is None:
        return {}
    kwargs: dict[str, float | str] = {"enrichment": enrichment}
    if spec.enrichment_target and spec.enrichment_target != "U235":
        kwargs["enrichment_target"] = spec.enrichment_target
    return kwargs


def _render_material_enrichment_args(spec: MaterialSpec) -> str:
    kwargs = _material_enrichment_kwargs(spec)
    if not kwargs:
        return ""
    return "".join(f", {key}={value!r}" for key, value in kwargs.items())


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
    material_ids = {material.id for material in spec.materials}
    surface_ids = {surface.id for surface in spec.surfaces}
    region_ids = {region.id for region in spec.regions}
    composite_region_ids = {
        surface.id
        for surface in spec.surfaces
        if surface.kind in {"rectangular_prism", "hexagonal_prism"}
    }
    region_like_ids = region_ids | composite_region_ids
    cell_ids = {cell.id for cell in spec.cells}
    universe_ids = {universe.id for universe in spec.universes}
    lattice_ids = {lattice.id for lattice in spec.lattices}
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
    for region in spec.regions:
        missing = [surface_id for surface_id in region.surface_ids if surface_id not in surface_ids]
        if missing:
            raise ValueError(f"region {region.id!r} references missing surfaces: {missing}")
    for cell in spec.cells:
        if cell.region_id is not None and cell.region_id not in region_like_ids:
            raise ValueError(f"cell {cell.id!r} references missing region {cell.region_id!r}")
        if cell.fill_type == "material" and cell.fill_id not in material_ids:
            raise ValueError(f"cell {cell.id!r} references missing material {cell.fill_id!r}")
        if cell.fill_type == "universe" and cell.fill_id not in universe_ids:
            raise ValueError(f"cell {cell.id!r} references missing universe {cell.fill_id!r}")
        if cell.fill_type == "lattice" and cell.fill_id not in lattice_ids:
            raise ValueError(f"cell {cell.id!r} references missing lattice {cell.fill_id!r}")
    for universe in spec.universes:
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing:
            raise ValueError(f"universe {universe.id!r} references missing cells: {missing}")
    empty_universe_ids = {universe.id for universe in spec.universes if not universe.cell_ids}
    auto_wrappable_universe_ids = _core_auto_wrappable_universe_ids(spec)
    for lattice in spec.lattices:
        pattern = lattice.universe_pattern
        if not pattern:
            raise ValueError(f"lattice {lattice.id!r} requires universe_pattern before export")
        row_lengths = {len(row) for row in pattern}
        if len(row_lengths) > 1:
            raise ValueError(f"lattice {lattice.id!r} universe_pattern rows have unequal lengths")
        missing = sorted({universe_id for row in pattern for universe_id in row if universe_id not in universe_ids})
        if missing:
            raise ValueError(f"lattice {lattice.id!r} references missing universes: {missing}")
        empty_refs = sorted(
            {
                universe_id
                for row in pattern
                for universe_id in row
                if universe_id in empty_universe_ids
                and universe_id not in auto_wrappable_universe_ids
            }
        )
        if empty_refs:
            raise ValueError(f"lattice {lattice.id!r} references empty universes: {empty_refs}")
        if lattice.outer_universe_id is not None and lattice.outer_universe_id not in universe_ids:
            raise ValueError(
                f"lattice {lattice.id!r} references missing outer_universe_id "
                f"{lattice.outer_universe_id!r}"
            )
    for layer in spec.core.axial_layers:
        fill = layer.fill
        fill_schema = f"complex_model.core.axial_layers.{layer.id}.fill.id"
        if fill.type == "material" and fill.id not in material_ids:
            raise ValueError(f"axial layer {layer.id!r} references missing material {fill.id!r}")
        if fill.type == "universe" and fill.id not in universe_ids:
            raise ValueError(f"axial layer {layer.id!r} references missing universe {fill.id!r}")
        if fill.type == "lattice" and fill.id not in lattice_ids:
            if layer.loading_id is None:
                raise ValueError(f"axial layer {layer.id!r} references missing lattice {fill.id!r}")
            loading = _loading_by_id(spec, layer.loading_id)
            if fill.id != _loading_derived_lattice_id(loading):
                raise ValueError(f"axial layer {layer.id!r} references missing lattice {fill.id!r} at {fill_schema}")
        if layer.loading_id is not None:
            if fill.type != "lattice":
                raise ValueError(f"axial layer {layer.id!r} uses loading_id with non-lattice fill")
            loading = _loading_by_id(spec, layer.loading_id)
            if loading.base_lattice_id not in lattice_ids:
                raise ValueError(f"lattice loading {loading.id!r} references missing base lattice {loading.base_lattice_id!r}")
            base_lattice = _lattice_by_id(spec, loading.base_lattice_id)
            for universe_id, positions in loading.overrides.items():
                if universe_id not in universe_ids:
                    raise ValueError(f"lattice loading {loading.id!r} override references missing universe {universe_id!r}")
                _check_loading_positions_in_bounds(loading.id, base_lattice, universe_id, positions)


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
    width_pair = _as_float_pair(params.get("width"))
    if width_pair is not None:
        params["width"] = width_pair[0]
        params["height"] = width_pair[1]

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


def _as_float_pair(value: object) -> tuple[float, float] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return None
    if len(value) != 2:
        raise ValueError(
            f"rectangular_prism width sequence must contain exactly two values, got {value!r}"
        )
    return (_as_float(value[0]), _as_float(value[1]))


def _render_region_definitions(
    region_specs: list[RegionSpec],
    surface_specs: list[SurfaceSpec],
) -> str:
    composite_surface_ids = {
        surface.id
        for surface in surface_specs
        if surface.kind in {"rectangular_prism", "hexagonal_prism"}
    }
    lines: list[str] = []
    for region in region_specs:
        variable_name = _safe_name("region", region.id)
        lines.append(
            f"{variable_name} = "
            f"{_region_expression_to_python(region.expression, composite_surface_ids)}"
        )
        lines.append(f"regions[{region.id!r}] = {variable_name}")
    return "\n".join(lines) if lines else "# No explicit regions were provided."


def _region_expression_to_python(
    expression: str,
    composite_surface_ids: set[str] | None = None,
) -> str:
    composite_surface_ids = composite_surface_ids or set()
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
        if token in composite_surface_ids and pending_sign:
            if pending_sign == "+":
                tokens.append(f"(~surfaces[{token!r}])")
            else:
                tokens.append(f"surfaces[{token!r}]")
            pending_sign = ""
            continue
        if pending_sign:
            tokens.append(f"({pending_sign}surfaces[{token!r}])")
            pending_sign = ""
        elif token in composite_surface_ids:
            tokens.append(f"surfaces[{token!r}]")
        else:
            tokens.append(f"(+surfaces[{token!r}])")
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
        fill_expr = _cell_initial_fill_expression(cell)
        region_expr = f"regions[{cell.region_id!r}]" if cell.region_id is not None else "None"
        lines.append(
            f"{variable_name} = openmc.Cell("
            f"name={cell.name!r}, fill={fill_expr}, region={region_expr})"
        )
        if cell.temperature_k is not None:
            lines.append(f"{variable_name}.temperature = {cell.temperature_k!r}")
        lines.append(f"cells[{cell.id!r}] = {variable_name}")
    return "\n".join(lines)


def _cell_initial_fill_expression(cell: CellSpec) -> str:
    if cell.fill_type in {"universe", "lattice"}:
        return "None"
    return _cell_fill_expression(cell)


def _render_cell_fill_assignments(cell_specs: list[CellSpec]) -> str:
    lines: list[str] = []
    for cell in cell_specs:
        if cell.fill_type not in {"universe", "lattice"}:
            continue
        variable_name = _safe_name("cell", cell.id)
        lines.append(f"{variable_name}.fill = {_cell_fill_expression(cell)}")
    return "\n".join(lines) if lines else "# No deferred cell fills were required."


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


def _render_core_universe_wrappers(spec: ComplexModelSpec) -> str:
    """Populate empty core-lattice assembly universes with wrapper cells.

    The planning IR often models a core as a lattice of assembly universes while
    separately listing AssemblySpec entries that point at assembly lattices. In
    OpenMC, an empty universe referenced by a lattice is not exported as usable
    geometry, so the core lattice ends up pointing at missing universe numbers.
    """
    assert spec.core is not None
    lines: list[str] = []
    for universe_id in sorted(_core_auto_wrappable_universe_ids(spec)):
        fill_expr = _core_universe_wrapper_fill_expression(spec, universe_id)
        if fill_expr is None:
            continue
        cell_variable = _safe_name("wrapper_cell", universe_id)
        cell_id = f"__wrapper_{universe_id}"
        lines.append(
            f"{cell_variable} = openmc.Cell("
            f"name={f'auto wrapper for {universe_id}'!r}, fill={fill_expr})"
        )
        lines.append(f"cells[{cell_id!r}] = {cell_variable}")
        lines.append(f"universes[{universe_id!r}].add_cell({cell_variable})")
    return "\n".join(lines) if lines else "# No core universe wrappers were required."


def _normalize_core_spec_for_rendering(spec: ComplexModelSpec) -> ComplexModelSpec:
    """Return a core rendering copy with lattice-referenced universes exportable.

    OpenMC cells are single-owner objects in exported XML. If multiple universe
    definitions reuse one Cell object, that cell is emitted under only one
    universe, leaving other lattice universe numbers dangling. The core renderer
    can infer the intended hierarchy for assembly/material wrappers and can
    safely clone pin-level cell definitions for the remaining shared-cell case.
    """
    normalized = spec.model_copy(deep=True)
    if normalized.core is None or normalized.core.lattice_id is None:
        return normalized
    normalized = _materialize_missing_core_universe_cells(normalized)

    reachable = _core_reachable_universe_ids(normalized)
    direct_core_refs = _core_direct_lattice_universe_ids(normalized)
    cell_by_id = {cell.id: cell for cell in normalized.cells}
    existing_cell_ids = set(cell_by_id)
    wrapper_cells: list[CellSpec] = []

    normalized_universes: list[UniverseSpec] = []
    for universe in normalized.universes:
        if universe.id not in reachable:
            normalized_universes.append(universe)
            continue
        wrapper = _core_wrapper_cell_for_universe(
            normalized,
            universe.id,
            direct_core_refs=direct_core_refs,
            existing_cell_ids=existing_cell_ids,
        )
        if wrapper is None:
            normalized_universes.append(universe)
            continue
        wrapper_cells.append(wrapper)
        existing_cell_ids.add(wrapper.id)
        normalized_universes.append(universe.model_copy(update={"cell_ids": [wrapper.id]}))

    normalized = normalized.model_copy(
        update={
            "cells": [*normalized.cells, *wrapper_cells],
            "universes": normalized_universes,
        }
    )
    normalized = _clone_shared_core_universe_cells(normalized, reachable)
    normalized = _ensure_core_lattice_outer_universes(normalized)
    return _ensure_core_lattice_placement(normalized)


def _ensure_core_lattice_outer_universes(spec: ComplexModelSpec) -> ComplexModelSpec:
    """Add a deterministic moderator outer universe for reachable core lattices.

    OpenMC requires a lattice ``outer`` universe whenever particles can leave the
    defined lattice indices. Nested assembly lattices inside a core are especially
    sensitive to this because local coordinates can briefly step outside the
    finite pin map during plotting or tracking.
    """
    outer_universe_id, spec = _core_default_outer_universe(spec)
    if outer_universe_id is None:
        return spec

    reachable_lattice_ids = _core_reachable_lattice_ids(spec)
    if not reachable_lattice_ids:
        return spec

    changed = False
    lattices: list[LatticeSpec] = []
    for lattice in spec.lattices:
        if (
            lattice.kind == "rect"
            and lattice.id in reachable_lattice_ids
            and lattice.outer_universe_id is None
        ):
            lattices.append(lattice.model_copy(update={"outer_universe_id": outer_universe_id}))
            changed = True
        else:
            lattices.append(lattice)
    if not changed:
        return spec
    return spec.model_copy(update={"lattices": lattices})


def _ensure_core_lattice_placement(spec: ComplexModelSpec) -> ComplexModelSpec:
    """Place the core lattice at the non-negative corner; center nested lattices.

    The C5G7 quarter-core / case3.md convention is non-negative global
    coordinates: the core occupies ``[0, W] x [0, H]`` with the origin at a
    core corner, matching the plot origin the LLM writes under the non-negative
    mental model. When the core lattice has neither ``lower_left_cm`` nor
    ``center_cm`` set, place it at ``(0, 0)``; explicit LLM placement of the
    core lattice is always respected.

    Nested rectangular lattices (pin/assembly lattices wrapped in a universe and
    reused by the core lattice) live in their universe's local frame: OpenMC
    aligns the wrapped universe's origin to the center of each core-lattice
    cell, so a nested lattice must be centered on that origin
    (``lower_left = -cols*pitch/2, -rows*pitch/2``) to fill the cell. LLM plans
    often write ``lower_left_cm=[0,0]`` for these nested lattices too, which
    shifts the lattice into one quadrant of the cell and leaves only ~1/4 of it
    visible (the rest falls outside the cell and is replaced by ``outer``).
    Force the centered local frame for every non-core rectangular lattice so the
    full assembly renders. Standalone assembly rendering (which fills a root
    cell that carries an explicit region) does not route through this core
    normalization, so its non-negative frame is unaffected.
    """
    if spec.core is None or spec.core.lattice_id is None:
        return spec
    core_lattice_id = spec.core.lattice_id
    new_lattices: list[LatticeSpec] = []
    changed = False
    for lat in spec.lattices:
        if lat.id == core_lattice_id:
            if lat.lower_left_cm is None and lat.center_cm is None:
                lat = lat.model_copy(update={"lower_left_cm": (0.0, 0.0)})
                changed = True
        elif lat.kind == "rect" and lat.universe_pattern:
            # The lattice is reused inside a core-lattice cell via its universe,
            # so it must be centered in that universe's local frame; an LLM-style
            # [0,0] lower_left would push it into one quadrant of the cell.
            centered = _infer_rect_lattice_lower_left(lat)
            if tuple(lat.lower_left_cm or ()) != centered:
                lat = lat.model_copy(update={"lower_left_cm": centered})
                changed = True
        new_lattices.append(lat)
    if not changed:
        return spec
    return spec.model_copy(update={"lattices": new_lattices})


def _reconcile_plot_origins(
    spec: ComplexModelSpec, plot_specs: list[PlotSpec]
) -> list[PlotSpec]:
    """Nudge slice-plot origins that land exactly on a core boundary surface.

    OpenMC cell regions are open intervals (``+x_min`` means ``x > x_min``), so a
    slice taken exactly at a reflective/vacuum boundary surface samples no cell
    and renders as a uniform fill. When a plot's slice-level coordinate (``y`` for
    an ``xz`` basis, ``x`` for a ``yz`` basis) coincides with a core lattice edge,
    move it to the core-center assembly coordinate so the slice intersects the
    active fuel/moderator interior (the edge nearest a vacuum boundary is often a
    reflector-only row, e.g. C5G7's outer water row), and append the adjustment to
    the plot's ``purpose`` so the rendered
    script records why the origin differs from the LLM-supplied value. ``xy``
    slices sample at ``z`` whose extent comes from axial layers rather than the
    core lattice, so they are left untouched here.
    """
    if spec.core is None or spec.core.lattice_id is None:
        return list(plot_specs)
    core_lattice = next(
        (lat for lat in spec.lattices if lat.id == spec.core.lattice_id), None
    )
    if (
        core_lattice is None
        or core_lattice.kind != "rect"
        or not core_lattice.universe_pattern
        or not core_lattice.universe_pattern[0]
    ):
        return list(plot_specs)
    lower_left = core_lattice.lower_left_cm
    if lower_left is None or len(lower_left) < 2:
        return list(plot_specs)
    pitch = core_lattice.pitch_cm
    if len(pitch) < 2:
        return list(plot_specs)
    pitch_x, pitch_y = pitch[0], pitch[1]
    cols = len(core_lattice.universe_pattern[0])
    rows = len(core_lattice.universe_pattern)
    x_min, y_min = lower_left[0], lower_left[1]
    x_max = x_min + cols * pitch_x
    y_max = y_min + rows * pitch_y
    x_centers = [x_min + pitch_x * (i + 0.5) for i in range(cols)]
    y_centers = [y_min + pitch_y * (j + 0.5) for j in range(rows)]

    tol = 1e-6

    def _nudge_to_interior(
        value: float,
        boundary_low: float,
        boundary_high: float,
        centers: list[float],
    ) -> tuple[float, bool]:
        # Either edge samples no cell; move the slice to the core-center assembly.
        # The centermost row/column always lies in the active fuel/moderator
        # interior, whereas the edge nearest a vacuum boundary is often a
        # reflector-only row (e.g. C5G7's outer water row) and would render as a
        # uniform fill even after the boundary nudge.
        if abs(value - boundary_low) < tol or abs(value - boundary_high) < tol:
            return centers[len(centers) // 2], True
        return value, False

    boundary_note = (
        "OpenMC cell regions are open intervals, so a slice exactly on a "
        "reflective/vacuum boundary surface samples no cell"
    )
    reconciled: list[PlotSpec] = []
    for plot in plot_specs:
        origin = list(plot.origin)
        adjustments: list[str] = []
        if plot.basis == "xz":
            moved_y, moved = _nudge_to_interior(origin[1], y_min, y_max, y_centers)
            if moved:
                adjustments.append(f"origin y {origin[1]:.6g} -> {moved_y:.6g}")
                origin[1] = moved_y
        elif plot.basis == "yz":
            moved_x, moved = _nudge_to_interior(origin[0], x_min, x_max, x_centers)
            if moved:
                adjustments.append(f"origin x {origin[0]:.6g} -> {moved_x:.6g}")
                origin[0] = moved_x
        if not adjustments:
            reconciled.append(plot)
            continue
        note = (
            "renderer nudged "
            + "; ".join(adjustments)
            + f" to the core-center assembly ({boundary_note})"
        )
        purpose = (plot.purpose + " | " if plot.purpose else "") + note
        reconciled.append(
            plot.model_copy(update={"origin": tuple(origin), "purpose": purpose})
        )
    return reconciled


def _core_default_outer_universe(
    spec: ComplexModelSpec,
) -> tuple[str | None, ComplexModelSpec]:
    preferred = (
        "water_universe",
        "moderator_universe",
        "reflector_universe",
        "coolant_universe",
        "water",
        "moderator",
        "reflector",
        "coolant",
    )
    universe_ids = {universe.id for universe in spec.universes}
    for universe_id in preferred:
        if universe_id in universe_ids:
            return universe_id, spec
    for universe in spec.universes:
        tokens = set(universe.id.split("_"))
        if tokens & {"water", "moderator", "reflector", "coolant"}:
            return universe.id, spec

    material_id = _core_default_outer_material_id(spec)
    if material_id is None:
        return None, spec

    cell_ids = {cell.id for cell in spec.cells}
    universe_ids = {universe.id for universe in spec.universes}
    cell_id = _unique_generated_id("__outer_water_cell", cell_ids)
    universe_id = _unique_generated_id("__outer_water_universe", universe_ids)
    cell = CellSpec(
        id=cell_id,
        name="auto outer moderator cell",
        fill_type="material",
        fill_id=material_id,
        purpose="Auto-generated outer universe for finite core RectLattice objects.",
    )
    universe = UniverseSpec(
        id=universe_id,
        name="auto outer moderator universe",
        cell_ids=[cell_id],
        purpose="Auto-generated outer universe for finite core RectLattice objects.",
    )
    return universe_id, spec.model_copy(
        update={
            "cells": [*spec.cells, cell],
            "universes": [*spec.universes, universe],
        }
    )


def _core_default_outer_material_id(spec: ComplexModelSpec) -> str | None:
    material_ids = [material.id for material in spec.materials]
    for material_id in ("water", "moderator", "coolant", "reflector"):
        if material_id in material_ids:
            return material_id
    for material_id in material_ids:
        tokens = set(material_id.split("_"))
        if tokens & {"water", "moderator", "coolant", "reflector"}:
            return material_id
    return None


def _materialize_missing_core_universe_cells(spec: ComplexModelSpec) -> ComplexModelSpec:
    cell_ids = {cell.id for cell in spec.cells}
    missing_by_universe = {
        universe.id: [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        for universe in spec.universes
    }
    missing_by_universe = {
        universe_id: missing
        for universe_id, missing in missing_by_universe.items()
        if missing and not _core_universe_has_wrapper_intent(spec, universe_id)
    }
    if not missing_by_universe:
        return spec

    generated_cells: list[CellSpec] = []
    generated_surfaces: list[SurfaceSpec] = []
    generated_regions: list[RegionSpec] = []
    region_ids = {region.id for region in spec.regions}
    surface_ids = {surface.id for surface in spec.surfaces}
    pin_radius = _infer_core_pin_radius(spec)

    for universe_id, missing_cell_ids in missing_by_universe.items():
        needs_pin_regions = _missing_cells_need_pin_regions(missing_cell_ids)
        inside_region_id: str | None = None
        outside_region_id: str | None = None
        if needs_pin_regions:
            surface_id = _unique_generated_id(f"__surface_{universe_id}_cyl", surface_ids)
            surface_ids.add(surface_id)
            inside_region_id = _unique_generated_id(f"__region_{universe_id}_inside", region_ids)
            region_ids.add(inside_region_id)
            outside_region_id = _unique_generated_id(f"__region_{universe_id}_outside", region_ids)
            region_ids.add(outside_region_id)
            generated_surfaces.append(
                SurfaceSpec(
                    id=surface_id,
                    kind="zcylinder",
                    parameters={"r": pin_radius},
                    purpose=f"Auto-generated local pin cylinder for {universe_id}.",
                )
            )
            generated_regions.extend(
                [
                    RegionSpec(
                        id=inside_region_id,
                        expression=f"-{surface_id}",
                        surface_ids=[surface_id],
                        purpose=f"Auto-generated inside-cylinder region for {universe_id}.",
                    ),
                    RegionSpec(
                        id=outside_region_id,
                        expression=f"+{surface_id}",
                        surface_ids=[surface_id],
                        purpose=f"Auto-generated moderator region for {universe_id}.",
                    ),
                ]
            )
        for cell_id in missing_cell_ids:
            material_id = _core_material_id_for_missing_cell(spec, universe_id, cell_id)
            if material_id is None:
                continue
            region_id = None
            if needs_pin_regions:
                region_id = outside_region_id if _cell_id_is_moderator(cell_id) else inside_region_id
            generated_cells.append(
                CellSpec(
                    id=cell_id,
                    name=f"auto cell for {cell_id}",
                    region_id=region_id,
                    fill_type="material",
                    fill_id=material_id,
                    purpose=f"Auto-generated from missing cell reference in universe {universe_id}.",
                )
            )

    if not generated_cells:
        return spec
    return spec.model_copy(
        update={
            "surfaces": [*spec.surfaces, *generated_surfaces],
            "regions": [*spec.regions, *generated_regions],
            "cells": [*spec.cells, *generated_cells],
        }
    )


def _core_universe_has_wrapper_intent(spec: ComplexModelSpec, universe_id: str) -> bool:
    lattice_ids = {lattice.id for lattice in spec.lattices}
    assembly = {assembly.id: assembly for assembly in spec.assemblies}.get(universe_id)
    if assembly is not None and assembly.lattice_id in lattice_ids:
        return True
    return _core_material_id_for_wrapper_universe(spec, universe_id) is not None


def _missing_cells_need_pin_regions(cell_ids: list[str]) -> bool:
    has_inner = any(_cell_id_is_inner_pin_region(cell_id) for cell_id in cell_ids)
    has_outer = any(_cell_id_is_moderator(cell_id) for cell_id in cell_ids)
    return has_inner and has_outer


def _cell_id_is_inner_pin_region(cell_id: str) -> bool:
    tokens = set(cell_id.split("_"))
    return bool(tokens & {"fuel", "cyl", "pin", "guide", "fiss", "chamber"})


def _cell_id_is_moderator(cell_id: str) -> bool:
    tokens = set(cell_id.split("_"))
    return bool(tokens & {"mod", "moderator", "water"})


def _infer_core_pin_radius(spec: ComplexModelSpec) -> float:
    radii = [
        float(surface.parameters["r"])
        for surface in spec.surfaces
        if surface.kind in {"zcylinder", "xcylinder", "ycylinder"}
        and isinstance(surface.parameters.get("r"), (int, float))
        and float(surface.parameters["r"]) > 0
    ]
    if radii:
        return min(radii)
    pitches = [
        min(_rect_lattice_pitch(lattice))
        for lattice in spec.lattices
        if lattice.kind == "rect" and lattice.pitch_cm
    ]
    if pitches:
        return min(pitches) * 0.42857142857142855
    return 0.54


def _core_material_id_for_missing_cell(
    spec: ComplexModelSpec,
    universe_id: str,
    cell_id: str,
) -> str | None:
    material_ids = {material.id for material in spec.materials}
    cell_tokens = set(cell_id.split("_"))
    universe_tokens = set(universe_id.split("_"))
    if cell_tokens & {"mod", "moderator", "water"} and "water" in material_ids:
        return "water"
    if cell_tokens & {"fiss", "chamber"}:
        if "fiss_chamber" in material_ids:
            return "fiss_chamber"
        if "guide_tube" in material_ids:
            return "guide_tube"
    if "guide" in cell_tokens or "guide" in universe_tokens:
        if "guide_tube" in material_ids:
            return "guide_tube"
        if "guide" in material_ids:
            return "guide"
    for token in [*cell_id.split("_"), *universe_id.split("_")]:
        if token in material_ids:
            return token
    if "fuel" in cell_tokens:
        for token in universe_id.split("_"):
            if token in material_ids:
                return token
        if "fuel" in material_ids:
            return "fuel"
    return _core_material_id_for_empty_universe(spec, universe_id)


def _core_material_id_for_wrapper_universe(
    spec: ComplexModelSpec,
    universe_id: str,
) -> str | None:
    if universe_id.startswith("pin_"):
        return None
    tokens = set(universe_id.split("_"))
    material_id = _core_material_id_for_empty_universe(spec, universe_id)
    if material_id is None:
        if "water" in {material.id for material in spec.materials} and tokens & {"water", "reflector"}:
            return "water"
        return None
    if material_id == "water" or tokens & {"water", "reflector", "moderator", "mod"}:
        return material_id
    return None


def _core_wrapper_cell_for_universe(
    spec: ComplexModelSpec,
    universe_id: str,
    *,
    direct_core_refs: set[str],
    existing_cell_ids: set[str],
) -> CellSpec | None:
    assembly = {assembly.id: assembly for assembly in spec.assemblies}.get(universe_id)
    lattice_ids = {lattice.id for lattice in spec.lattices}
    if assembly is not None and assembly.lattice_id in lattice_ids:
        return _core_wrapper_cell(
            universe_id,
            fill_type="lattice",
            fill_id=assembly.lattice_id,
            existing_cell_ids=existing_cell_ids,
        )

    universe = {universe.id: universe for universe in spec.universes}.get(universe_id)
    material_id = _core_material_id_for_wrapper_universe(spec, universe_id)
    if material_id is not None and (not universe or not universe.cell_ids or universe_id in direct_core_refs):
        return _core_wrapper_cell(
            universe_id,
            fill_type="material",
            fill_id=material_id,
            existing_cell_ids=existing_cell_ids,
        )
    return None


def _core_wrapper_cell(
    universe_id: str,
    *,
    fill_type: str,
    fill_id: str | None,
    existing_cell_ids: set[str],
) -> CellSpec:
    base_id = f"__wrapper_{universe_id}"
    cell_id = _unique_generated_id(base_id, existing_cell_ids)
    return CellSpec(
        id=cell_id,
        name=f"auto wrapper for {universe_id}",
        fill_type=fill_type,  # type: ignore[arg-type]
        fill_id=fill_id,
    )


def _clone_shared_core_universe_cells(
    spec: ComplexModelSpec,
    reachable: set[str],
) -> ComplexModelSpec:
    cell_by_id = {cell.id: cell for cell in spec.cells}
    existing_cell_ids = set(cell_by_id)
    seen_owner_by_cell_id: dict[str, str] = {}
    cloned_cells: list[CellSpec] = []
    normalized_universes: list[UniverseSpec] = []

    for universe in spec.universes:
        if universe.id not in reachable:
            normalized_universes.append(universe)
            continue
        normalized_cell_ids: list[str] = []
        for cell_id in universe.cell_ids:
            if cell_id not in cell_by_id or cell_id not in seen_owner_by_cell_id:
                seen_owner_by_cell_id[cell_id] = universe.id
                normalized_cell_ids.append(cell_id)
                continue
            source = cell_by_id[cell_id]
            clone_id = _unique_generated_id(f"{cell_id}__for_{universe.id}", existing_cell_ids)
            existing_cell_ids.add(clone_id)
            cloned_cells.append(
                source.model_copy(
                    update={
                        "id": clone_id,
                        "name": f"{source.name} for {universe.id}",
                    }
                )
            )
            normalized_cell_ids.append(clone_id)
        normalized_universes.append(universe.model_copy(update={"cell_ids": normalized_cell_ids}))

    if not cloned_cells:
        return spec
    return spec.model_copy(
        update={
            "cells": [*spec.cells, *cloned_cells],
            "universes": normalized_universes,
        }
    )


def _unique_generated_id(base_id: str, existing_ids: set[str]) -> str:
    if base_id not in existing_ids:
        return base_id
    index = 2
    while f"{base_id}_{index}" in existing_ids:
        index += 1
    return f"{base_id}_{index}"


def _core_direct_lattice_universe_ids(spec: ComplexModelSpec) -> set[str]:
    if spec.core is None or spec.core.lattice_id is None:
        return set()
    core_lattice = _lattice_by_id(spec, spec.core.lattice_id)
    universe_ids = {
        universe_id
        for row in core_lattice.universe_pattern
        for universe_id in row
    }
    universe_ids.update(
        layer.fill.id
        for layer in spec.core.axial_layers
        if layer.fill.type == "universe" and layer.fill.id is not None
    )
    return universe_ids


def _core_reachable_universe_ids(spec: ComplexModelSpec) -> set[str]:
    if spec.core is None or spec.core.lattice_id is None:
        return set()
    lattice_by_id = {lattice.id: lattice for lattice in spec.lattices}
    cell_by_id = {cell.id: cell for cell in spec.cells}
    universe_by_id = {universe.id: universe for universe in spec.universes}
    assembly_by_id = {assembly.id: assembly for assembly in spec.assemblies}
    pending_lattice_ids = [spec.core.lattice_id]
    pending_universe_ids = [
        layer.fill.id
        for layer in spec.core.axial_layers
        if layer.fill.type == "universe" and layer.fill.id is not None
    ]
    visited_lattice_ids: set[str] = set()
    reachable_universe_ids: set[str] = set()

    while pending_lattice_ids or pending_universe_ids:
        if pending_universe_ids:
            lattice_universe_ids = {pending_universe_ids.pop()}
        else:
            lattice_id = pending_lattice_ids.pop()
            if lattice_id in visited_lattice_ids:
                continue
            visited_lattice_ids.add(lattice_id)
            lattice = lattice_by_id.get(lattice_id)
            if lattice is None:
                continue
            lattice_universe_ids = {
                universe_id
                for row in lattice.universe_pattern
                for universe_id in row
            }
            if lattice.outer_universe_id is not None:
                lattice_universe_ids.add(lattice.outer_universe_id)
        for universe_id in lattice_universe_ids:
            if universe_id in reachable_universe_ids:
                continue
            reachable_universe_ids.add(universe_id)
            assembly = assembly_by_id.get(universe_id)
            if assembly is not None and assembly.lattice_id is not None:
                pending_lattice_ids.append(assembly.lattice_id)
            universe = universe_by_id.get(universe_id)
            if universe is None:
                continue
            for cell_id in universe.cell_ids:
                cell = cell_by_id.get(cell_id)
                if cell is not None and cell.fill_type == "lattice" and cell.fill_id is not None:
                    pending_lattice_ids.append(cell.fill_id)
                if cell is not None and cell.fill_type == "universe" and cell.fill_id is not None:
                    pending_universe_ids.append(cell.fill_id)
    return reachable_universe_ids


def _core_reachable_lattice_ids(spec: ComplexModelSpec) -> set[str]:
    if spec.core is None or spec.core.lattice_id is None:
        return set()
    lattice_by_id = {lattice.id: lattice for lattice in spec.lattices}
    cell_by_id = {cell.id: cell for cell in spec.cells}
    universe_by_id = {universe.id: universe for universe in spec.universes}
    assembly_by_id = {assembly.id: assembly for assembly in spec.assemblies}
    pending_lattice_ids = [spec.core.lattice_id]
    pending_universe_ids = [
        layer.fill.id
        for layer in spec.core.axial_layers
        if layer.fill.type == "universe" and layer.fill.id is not None
    ]
    reachable_lattice_ids: set[str] = set()
    visited_universe_ids: set[str] = set()

    while pending_lattice_ids or pending_universe_ids:
        if pending_lattice_ids:
            lattice_id = pending_lattice_ids.pop()
            if lattice_id in reachable_lattice_ids:
                continue
            lattice = lattice_by_id.get(lattice_id)
            if lattice is None:
                continue
            reachable_lattice_ids.add(lattice_id)
            pending_universe_ids.extend(
                universe_id
                for row in lattice.universe_pattern
                for universe_id in row
            )
            if lattice.outer_universe_id is not None:
                pending_universe_ids.append(lattice.outer_universe_id)
            continue

        universe_id = pending_universe_ids.pop()
        if universe_id in visited_universe_ids:
            continue
        visited_universe_ids.add(universe_id)
        assembly = assembly_by_id.get(universe_id)
        if assembly is not None and assembly.lattice_id is not None:
            pending_lattice_ids.append(assembly.lattice_id)
        universe = universe_by_id.get(universe_id)
        if universe is None:
            continue
        for cell_id in universe.cell_ids:
            cell = cell_by_id.get(cell_id)
            if cell is not None and cell.fill_type == "lattice" and cell.fill_id is not None:
                pending_lattice_ids.append(cell.fill_id)
            if cell is not None and cell.fill_type == "universe" and cell.fill_id is not None:
                pending_universe_ids.append(cell.fill_id)

    return reachable_lattice_ids


def _core_auto_wrappable_universe_ids(spec: ComplexModelSpec) -> set[str]:
    if spec.core is None or spec.core.lattice_id is None:
        return set()
    universe_by_id = {universe.id: universe for universe in spec.universes}
    core_universe_ids = _core_reachable_universe_ids(spec)
    direct_core_refs = _core_direct_lattice_universe_ids(spec)
    existing_cell_ids = {cell.id for cell in spec.cells}
    return {
        universe_id
        for universe_id in core_universe_ids
        if universe_id in universe_by_id
        and _core_wrapper_cell_for_universe(
            spec,
            universe_id,
            direct_core_refs=direct_core_refs,
            existing_cell_ids=existing_cell_ids,
        ) is not None
    }


def _core_universe_wrapper_fill_expression(
    spec: ComplexModelSpec,
    universe_id: str,
) -> str | None:
    lattice_ids = {lattice.id for lattice in spec.lattices}
    assembly_by_id = {assembly.id: assembly for assembly in spec.assemblies}
    assembly = assembly_by_id.get(universe_id)
    if assembly is not None and assembly.lattice_id in lattice_ids:
        return f"lattices[{assembly.lattice_id!r}]"

    material_id = _core_material_id_for_wrapper_universe(spec, universe_id)
    if material_id is not None:
        return f"materials_by_id[{material_id!r}]"
    return None


def _core_material_id_for_empty_universe(
    spec: ComplexModelSpec,
    universe_id: str,
) -> str | None:
    material_ids = [material.id for material in spec.materials]
    tokens = set(universe_id.split("_"))
    candidates = [
        material_id
        for material_id in material_ids
        if universe_id == material_id
        or universe_id.startswith(f"{material_id}_")
        or universe_id.endswith(f"_{material_id}")
        or material_id in tokens
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _render_assembly_root(spec: ComplexModelSpec) -> str:
    assembly = spec.assemblies[0]
    return _render_lattice_root(
        spec,
        lattice_id=assembly.lattice_id,
        root_name=assembly.name,
        boundary=assembly.boundary,
    )


def _render_core_root(spec: ComplexModelSpec) -> str:
    assert spec.core is not None
    if spec.core.axial_layers:
        return _render_axial_core_root(spec)
    return _render_lattice_root(
        spec,
        lattice_id=spec.core.lattice_id,
        root_name=spec.core.name,
        boundary=spec.core.boundary,
    )


def _apply_lattice_loading_overrides(
    pattern: list[list[str]],
    overrides: dict[str, list[tuple[int, int]]],
) -> list[list[str]]:
    """Return a copy of ``pattern`` with lattice-loading overrides applied.

    ``overrides`` maps a universe id to the (row, col) positions it should
    occupy, matching the ``LatticeSpec.overrides`` convention (row 0 = top,
    col 0 = left). Out-of-bounds positions raise so an invalid plan never
    silently produces a ragged derived lattice.
    """
    grid = [list(row) for row in pattern]
    for universe_id, positions in overrides.items():
        for position in positions:
            row, col = position
            if not (0 <= row < len(grid) and 0 <= col < len(grid[row])):
                raise ValueError(
                    f"lattice loading override position {(row, col)} for universe "
                    f"{universe_id!r} is out of bounds"
                )
            grid[row][col] = universe_id
    return grid


def _loading_derived_lattice_id(loading: object) -> str:
    derived = getattr(loading, "derived_lattice_id", None)
    return derived or f"{getattr(loading, 'id')}_lattice"


def _loading_by_id(spec: ComplexModelSpec, loading_id: str | None) -> object:
    for loading in spec.lattice_loadings:
        if loading.id == loading_id:
            return loading
    raise ValueError(f"axial layer references missing loading_id={loading_id!r}")


def _check_loading_positions_in_bounds(
    loading_id: str,
    base_lattice: LatticeSpec,
    universe_id: str,
    positions: list[tuple[int, int]],
) -> None:
    for row, col in positions:
        in_rows = 0 <= row < len(base_lattice.universe_pattern)
        in_cols = in_rows and 0 <= col < len(base_lattice.universe_pattern[row])
        if not (in_rows and in_cols):
            raise ValueError(
                f"lattice loading {loading_id!r} override position {(row, col)} "
                f"for universe {universe_id!r} is out of bounds"
            )


def _render_axial_core_root(spec: ComplexModelSpec) -> str:
    assert spec.core is not None
    lattice = _lattice_by_id(spec, spec.core.lattice_id)
    pitch_x, pitch_y = _rect_lattice_pitch(lattice)
    rows = len(lattice.universe_pattern)
    cols = len(lattice.universe_pattern[0])
    lower_left_x, lower_left_y = lattice.lower_left_cm or _infer_rect_lattice_lower_left(lattice)
    upper_right_x = lower_left_x + cols * pitch_x
    upper_right_y = lower_left_y + rows * pitch_y
    z_min = min(layer.z_min_cm for layer in spec.core.axial_layers)
    z_max = max(layer.z_max_cm for layer in spec.core.axial_layers)
    boundaries = spec.core.boundary_conditions
    fallback_boundary = _root_boundary_type(spec.core.boundary)

    lines = [
        f"assembly_x_min = {lower_left_x!r}",
        f"assembly_x_max = {upper_right_x!r}",
        f"assembly_y_min = {lower_left_y!r}",
        f"assembly_y_max = {upper_right_y!r}",
        f"assembly_z_min = {z_min!r}",
        f"assembly_z_max = {z_max!r}",
        (
            "assembly_xmin = openmc.XPlane("
            f"x0=assembly_x_min, boundary_type={_axis_boundary(boundaries, 'xmin', fallback_boundary)!r})"
        ),
        (
            "assembly_xmax = openmc.XPlane("
            f"x0=assembly_x_max, boundary_type={_axis_boundary(boundaries, 'xmax', fallback_boundary)!r})"
        ),
        (
            "assembly_ymin = openmc.YPlane("
            f"y0=assembly_y_min, boundary_type={_axis_boundary(boundaries, 'ymin', fallback_boundary)!r})"
        ),
        (
            "assembly_ymax = openmc.YPlane("
            f"y0=assembly_y_max, boundary_type={_axis_boundary(boundaries, 'ymax', fallback_boundary)!r})"
        ),
        (
            "assembly_zmin = openmc.ZPlane("
            f"z0=assembly_z_min, boundary_type={_axis_boundary(boundaries, 'zmin', fallback_boundary)!r})"
        ),
        (
            "assembly_zmax = openmc.ZPlane("
            f"z0=assembly_z_max, boundary_type={_axis_boundary(boundaries, 'zmax', fallback_boundary)!r})"
        ),
    ]

    internal_planes: dict[float, str] = {}
    for layer in spec.core.axial_layers:
        for z_value in (layer.z_min_cm, layer.z_max_cm):
            if z_value in {z_min, z_max} or z_value in internal_planes:
                continue
            plane_name = _safe_name("assembly_z", str(z_value).replace(".", "_"))
            internal_planes[z_value] = plane_name
            lines.append(f"{plane_name} = openmc.ZPlane(z0={z_value!r})")

    root_cell_refs: list[str] = []
    for layer in spec.core.axial_layers:
        cell_name = _safe_name("root_cell", layer.id)
        lower_plane = "assembly_zmin" if layer.z_min_cm == z_min else internal_planes[layer.z_min_cm]
        upper_plane = "assembly_zmax" if layer.z_max_cm == z_max else internal_planes[layer.z_max_cm]
        region_name = _safe_name("root_region", layer.id)
        lines.append(
            f"{region_name} = +assembly_xmin & -assembly_xmax & "
            f"+assembly_ymin & -assembly_ymax & +{lower_plane} & -{upper_plane}"
        )
        fill_expr = _axial_layer_fill_expression(layer)
        if layer.loading_id is not None:
            loading = _loading_by_id(spec, layer.loading_id)
            base_lattice = _lattice_by_id(spec, loading.base_lattice_id)
            derived_pattern = _apply_lattice_loading_overrides(
                base_lattice.universe_pattern, loading.overrides
            )
            derived_var = _safe_name("axial_lattice", layer.id)
            derived_id = _loading_derived_lattice_id(loading)
            base_lower_left = (
                base_lattice.lower_left_cm or _infer_rect_lattice_lower_left(base_lattice)
            )
            lines.append(f"{derived_var} = openmc.RectLattice(name={derived_id!r})")
            lines.append(f"{derived_var}.pitch = {_rect_lattice_pitch(base_lattice)!r}")
            lines.append(f"{derived_var}.lower_left = {base_lower_left!r}")
            lines.append(
                f"{derived_var}.universes = {_render_universe_pattern(derived_pattern)}"
            )
            if base_lattice.outer_universe_id is not None:
                lines.append(
                    f"{derived_var}.outer = universes[{base_lattice.outer_universe_id!r}]"
                )
            lines.append(f"lattices[{derived_id!r}] = {derived_var}")
            fill_expr = derived_var
        lines.append(
            f"{cell_name} = openmc.Cell("
            f"name={layer.name!r}, "
            f"fill={fill_expr}, "
            f"region={region_name})"
        )
        root_cell_refs.append(cell_name)

    root_cells = ", ".join(root_cell_refs)
    lines.append(f"root_cell = {root_cell_refs[0]}")
    lines.append(f"root_universe = openmc.Universe(cells=[{root_cells}])")
    return "\n".join(lines)


def _axis_boundary(boundaries: object, axis: str, fallback: str) -> str:
    if boundaries is None:
        return fallback
    value = getattr(boundaries, axis, None)
    return value or fallback


def _root_boundary_type(boundary: str | None) -> str:
    return boundary if boundary in {"transmission", "vacuum", "reflective", "white"} else "vacuum"


def _axial_layer_fill_expression(layer: object) -> str:
    fill = getattr(layer, "fill")
    fill_type = getattr(fill, "type")
    fill_id = getattr(fill, "id")
    if fill_type == "void":
        return "None"
    if fill_type == "material":
        return f"materials_by_id[{fill_id!r}]"
    if fill_type == "universe":
        return f"universes[{fill_id!r}]"
    if fill_type == "lattice":
        return f"lattices[{fill_id!r}]"
    raise ValueError(f"unsupported axial layer fill_type {fill_type!r}")


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
assembly_z_min = -1.0
assembly_z_max = 1.0
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


def _render_optional_temperature_interpolation(
    settings: RunSettingsSpec,
    spec: ComplexModelSpec | None = None,
) -> str:
    # Continuous-energy reactor models usually set material temperatures that
    # don't match the HDF5 library grid (e.g. 565 K vs 294/600 K). Without
    # temperature handling OpenMC aborts on the missing temperature. The
    # 'method=interpolation' setting linearly interpolates between the bracketing
    # library temperatures and is a no-op when the exact temperature exists.
    # It is irrelevant for multi-group models.
    if not settings.temperature_interpolation:
        return ""
    if settings.energy_mode == "multi-group":
        return ""
    if spec is not None and _model_uses_mgxs(spec):
        return ""
    return "settings.temperature['method'] = 'interpolation'"


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


_PLOT_OUTPUT_DIR = "plots"


def _render_plots_block(plot_specs: list[PlotSpec]) -> str:
    """Render one ``openmc.Plot`` per (plot, color_by) pair under ``plots/``.

    Each structured plot spec is expanded into two OpenMC plots — colored by
    ``material`` and by ``cell`` — so a reviewer sees both the material layout and
    the cell/universe structure from a single slice. Files land in the ``plots/``
    subdirectory (created at script run time) to keep the run folder tidy.
    """
    if not plot_specs:
        return ""

    blocks: list[str] = [
        "",
        "# Geometry plots selected by the structured plan.",
        "import os",
        f"os.makedirs({_PLOT_OUTPUT_DIR!r}, exist_ok=True)",
    ]
    plot_names: list[str] = []
    for index, plot in enumerate(plot_specs):
        stem = Path(plot.filename).stem
        for color_by in ("material", "cell"):
            variable_name = f"plot_{index}_{color_by}"
            plot_names.append(variable_name)
            filename = f"{_PLOT_OUTPUT_DIR}/{stem}_{color_by}"
            if plot.purpose:
                blocks.append(
                    f"# {variable_name} ({plot.basis}, by {color_by}): {plot.purpose}"
                )
            blocks.extend(
                [
                    f"{variable_name} = openmc.Plot()",
                    f"{variable_name}.basis = {plot.basis!r}",
                    f"{variable_name}.origin = {plot.origin!r}",
                    f"{variable_name}.width = {plot.width_cm!r}",
                    f"{variable_name}.pixels = {plot.pixels!r}",
                    f"{variable_name}.color_by = {color_by!r}",
                    f"{variable_name}.filename = {filename!r}",
                    "",
                ]
            )

    blocks.append(f"plots = openmc.Plots([{', '.join(plot_names)}])")
    blocks.append("plots.export_to_xml()")
    return "\n".join(blocks)
