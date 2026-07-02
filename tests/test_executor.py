import openmc

from openmc_agent.executor import (
    build_openmc_material,
    render_openmc_plan_script,
    render_openmc_script,
    render_openmc_smoke_test_script,
)
from openmc_agent.schemas import (
    ExecutionCheckSpec,
    GeometrySpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
)


def test_build_openmc_material_from_material_spec() -> None:
    spec = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95, percent_type="ao"),
            NuclideSpec(name="U238", percent=95.05, percent_type="ao"),
            NuclideSpec(name="O16", percent=200.0, percent_type="ao"),
        ],
    )

    material = build_openmc_material(spec)

    assert isinstance(material, openmc.Material)
    assert material.name == "UO2 fuel"
    assert material.density_units == "g/cm3"
    assert material.density == 10.4
    assert [(n.name, n.percent, n.percent_type) for n in material.nuclides] == [
        ("U235", 4.95, "ao"),
        ("U238", 95.05, "ao"),
        ("O16", 200.0, "ao"),
    ]
    assert "UO2 fuel" in str(material)


def test_render_openmc_script_for_minimal_pin_cell() -> None:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    moderator = MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )
    cladding = MaterialSpec(
        name="Zircaloy cladding",
        density_unit="g/cm3",
        density_value=6.55,
        composition=[NuclideSpec(name="Zr", percent=1.0, kind="element")],
    )
    spec = SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            cladding=cladding,
            geometry=GeometrySpec(
                fuel_radius_cm=0.41,
                clad_inner_radius_cm=0.42,
                clad_outer_radius_cm=0.48,
                pitch_cm=1.26,
            ),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )

    script = render_openmc_script(spec)

    assert "import openmc" in script
    assert "materials = openmc.Materials" in script
    assert "geometry = openmc.Geometry" in script
    assert "settings = openmc.Settings()" in script
    assert "tallies = openmc.Tallies" in script
    assert "model = openmc.Model" in script
    assert "model.export_to_xml()" in script
    assert "fuel_radius = 0.41" in script
    assert "pitch = 1.26" in script
    assert "openmc.ZCylinder(r=clad_outer_radius)" in script


def test_render_openmc_plan_script_uses_structured_plot_specs() -> None:
    spec = SimulationSpec(
        name="UO2 pin-cell with plots",
        pin_cell=PinCellSpec(
            fuel=MaterialSpec(
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=4.95),
                    NuclideSpec(name="U238", percent=95.05),
                    NuclideSpec(name="O16", percent=200.0),
                ],
            ),
            moderator=MaterialSpec(
                name="Water moderator",
                density_unit="g/cm3",
                density_value=1.0,
                composition=[
                    NuclideSpec(name="H1", percent=2.0),
                    NuclideSpec(name="O16", percent=1.0),
                ],
            ),
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=RunSettingsSpec(batches=60, inactive=15, particles=2000),
    )
    plan = SimulationPlan(
        model_spec=spec,
        plot_specs=[
            PlotSpec(
                basis="xz",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 2.0),
                pixels=(640, 480),
                color_by="cell",
                filename="pin_cell_xz.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=6, inactive=1, particles=120)
        ),
    )

    script = render_openmc_plan_script(plan)
    smoke_script = render_openmc_smoke_test_script(plan)

    assert "plot_0 = openmc.Plot()" in script
    assert "plot_0.basis = 'xz'" in script
    assert "plot_0.width = (1.26, 2.0)" in script
    assert "plot_0.pixels = (640, 480)" in script
    assert "plot_0.color_by = 'cell'" in script
    assert "plot_0.filename = 'pin_cell_xz'" in script
    assert "plots.export_to_xml()" in script
    assert "settings.batches = 60" in script
    assert "settings.particles = 2000" in script
    assert "settings.batches = 6" in smoke_script
    assert "settings.inactive = 1" in smoke_script
    assert "settings.particles = 120" in smoke_script
