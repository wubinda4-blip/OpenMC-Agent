from pathlib import Path

import openmc

from openmc_agent.schemas import (
    MaterialSpec,
    PlotSpec,
    RunSettingsSpec,
    SimulationPlan,
    SimulationSpec,
)


def build_openmc_material(spec: MaterialSpec) -> openmc.Material:
    material = openmc.Material(name=spec.name)
    material.set_density(spec.density_unit, spec.density_value)
    if spec.temperature_k is not None:
        material.temperature = spec.temperature_k

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


def render_openmc_plan_script(plan: SimulationPlan) -> str:
    return render_openmc_script(plan.model_spec, plot_specs=plan.plot_specs)


def render_openmc_smoke_test_script(plan: SimulationPlan) -> str:
    return render_openmc_script(
        plan.model_spec,
        settings_override=plan.execution_check.settings,
        plot_specs=plan.plot_specs,
    )


def _render_material_definition(spec: MaterialSpec, variable_name: str) -> str:
    lines = [
        f'{variable_name} = openmc.Material(name={spec.name!r})',
        f"{variable_name}.set_density({spec.density_unit!r}, {spec.density_value!r})",
    ]
    if spec.temperature_k is not None:
        lines.append(f"{variable_name}.temperature = {spec.temperature_k!r}")
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


def _render_optional_seed(settings: RunSettingsSpec) -> str:
    if settings.seed is None:
        return ""
    return f"settings.seed = {settings.seed}"


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
