import openmc
import pytest
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from openmc_agent.executor import (
    _region_expression_to_python,
    _surface_constructor,
    build_openmc_complex_material,
    build_openmc_material,
    render_openmc_assembly_script,
    render_openmc_plan_script,
    render_openmc_script,
    render_openmc_smoke_test_script,
)
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AssemblySpec,
    AxialLayerSpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    ControlRodSpec,
    CoreBoundarySpec,
    CoreSpec,
    ExecutionCheckSpec,
    GeometrySpec,
    LatticeLoadingSpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PackedSphereSpec,
    PebbleSpec,
    PinCellSpec,
    PlotSpec,
    ReflectorSpec,
    RenderCapabilityReport,
    RegionSpec,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
    SurfaceSpec,
    TRISOLayerSpec,
    TRISOSpec,
    UniverseSpec,
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


def test_build_openmc_complex_material_from_formula() -> None:
    spec = ComplexMaterialSpec(
        id="fuel",
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        chemical_formula="UO2",
        enrichment_percent=4.95,
        enrichment_target="U235",
        enrichment_type="wo",
    )

    material = build_openmc_complex_material(spec)

    assert isinstance(material, openmc.Material)
    assert material.name == "UO2 fuel"
    assert material.density == 10.4
    assert any(nuclide.name == "U235" for nuclide in material.nuclides)


def test_build_openmc_complex_material_from_macroscopic() -> None:
    spec = ComplexMaterialSpec(
        id="uo2",
        name="UO2",
        density_unit="macro",
        density_value=1.0,
        macroscopic="uo2",
    )

    material = build_openmc_complex_material(spec)

    assert isinstance(material, openmc.Material)
    assert material.name == "UO2"
    assert material.density_units == "macro"
    assert material.density == 1.0
    assert getattr(material, "_macroscopic") == "uo2"


def test_build_openmc_complex_material_uses_formula_for_mixed_percent_types() -> None:
    spec = ComplexMaterialSpec(
        id="fuel",
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=3.1, percent_type="wo"),
            NuclideSpec(name="U238", percent=96.9, percent_type="wo"),
            NuclideSpec(name="O16", percent=2.0, percent_type="ao"),
        ],
        chemical_formula="UO2",
        enrichment_percent=3.1,
        enrichment_target="U235",
        enrichment_type="wo",
    )

    material = build_openmc_complex_material(spec)

    assert material.name == "UO2 fuel"
    assert {nuclide.percent_type for nuclide in material.nuclides} == {"ao"}
    assert any(nuclide.name == "U235" for nuclide in material.nuclides)


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

    assert "os.makedirs('plots', exist_ok=True)" in script
    assert "plot_0_material = openmc.Plot()" in script
    assert "plot_0_cell = openmc.Plot()" in script
    assert "plot_0_cell.basis = 'xz'" in script
    assert "plot_0_cell.width = (1.26, 2.0)" in script
    assert "plot_0_cell.pixels = (640, 480)" in script
    assert "plot_0_cell.color_by = 'cell'" in script
    assert "plot_0_cell.filename = 'plots/pin_cell_xz_cell'" in script
    assert "plot_0_material.color_by = 'material'" in script
    assert "plot_0_material.filename = 'plots/pin_cell_xz_material'" in script
    assert "plots.export_to_xml()" in script
    assert "settings.batches = 60" in script
    assert "settings.particles = 2000" in script
    assert "settings.batches = 6" in smoke_script
    assert "settings.inactive = 1" in smoke_script
    assert "settings.particles = 120" in smoke_script


def _pin_cell_plan(*, temperature_interpolation: bool) -> SimulationPlan:
    spec = SimulationSpec(
        name="UO2 pin-cell",
        pin_cell=PinCellSpec(
            fuel=MaterialSpec(
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[NuclideSpec(name="U235", percent=4.95)],
                temperature_k=565.0,
            ),
            moderator=MaterialSpec(
                name="Water",
                density_unit="g/cm3",
                density_value=1.0,
                composition=[NuclideSpec(name="H1", percent=2.0)],
            ),
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=RunSettingsSpec(
            batches=6, inactive=1, particles=50,
            temperature_interpolation=temperature_interpolation,
        ),
    )
    return SimulationPlan(
        model_spec=spec,
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(200, 200),
                color_by="material",
                filename="pin_cell_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(
                batches=4, inactive=1, particles=20,
                temperature_interpolation=temperature_interpolation,
            )
        ),
    )


def test_temperature_interpolation_emits_method_key_for_ce_pin_cell() -> None:
    """Regression: 565 K operating temps need temperature method=interpolation.

    OpenMC's temperature dict has no 'interpolation' key; the correct API is
    settings.temperature['method'] = 'interpolation'. A wrong key is silently
    ignored by the C++ reader and OpenMC still aborts on the missing temp.
    """
    script = render_openmc_plan_script(_pin_cell_plan(temperature_interpolation=True))
    smoke_script = render_openmc_smoke_test_script(_pin_cell_plan(temperature_interpolation=True))
    assert "settings.temperature['method'] = 'interpolation'" in script
    assert "settings.temperature['method'] = 'interpolation'" in smoke_script
    # The legacy/wrong key must never be emitted.
    assert "temperature['interpolation']" not in script


def test_temperature_interpolation_disabled_omits_line() -> None:
    script = render_openmc_plan_script(_pin_cell_plan(temperature_interpolation=False))
    assert "temperature['method']" not in script


def test_render_openmc_plan_script_for_rectangular_assembly_exports_xml(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="2x2 assembly",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="UO2 fuel",
                    density_unit="g/cm3",
                    density_value=10.4,
                    composition=[
                        NuclideSpec(name="U235", percent=4.95),
                        NuclideSpec(name="U238", percent=95.05),
                        NuclideSpec(name="O16", percent=200.0),
                    ],
                )
            ],
            cells=[
                CellSpec(
                    id="fuel_cell",
                    name="fuel",
                    fill_type="material",
                    fill_id="fuel",
                )
            ],
            universes=[
                UniverseSpec(id="pin", name="pin universe", cell_ids=["fuel_cell"])
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="2x2 rectangular lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[
                        ["pin", "pin"],
                        ["pin", "pin"],
                    ],
                )
            ],
            assemblies=[
                AssemblySpec(
                    id="assembly",
                    name="root assembly",
                    lattice_id="assembly_lattice",
                    boundary="reflective",
                )
            ],
            settings=RunSettingsSpec(batches=8, inactive=2, particles=100),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="assembly",
            executable_subsystems=["rect_lattice", "assembly"],
        ),
        plot_specs=[
            PlotSpec(
                basis="xy",
                width_cm=(2.52, 2.52),
                pixels=(200, 200),
                filename="assembly_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20)
        ),
    )

    script = render_openmc_plan_script(plan)
    smoke_script = render_openmc_smoke_test_script(plan)

    assert "openmc.RectLattice" in script
    assert "lattices['assembly_lattice']" in script
    assert "root_cell = openmc.Cell" in script
    assert "settings.batches = 8" in script
    assert "settings.batches = 4" in smoke_script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "materials.xml").exists()
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "settings.xml").exists()
    assert (tmp_path / "plots.xml").exists()


def test_render_assembly_with_mixed_uo2_percent_types_plots(
    tmp_path: Path,
) -> None:
    openmc_cli = shutil.which("openmc")
    if openmc_cli is None:
        pytest.skip("openmc executable is not available")

    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="1x1 assembly with enriched UO2",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="uo2",
                    name="UO2 fuel",
                    density_unit="g/cm3",
                    density_value=10.4,
                    composition=[
                        NuclideSpec(name="U235", percent=3.1, percent_type="wo"),
                        NuclideSpec(name="U238", percent=96.9, percent_type="wo"),
                        NuclideSpec(name="O16", percent=2.0, percent_type="ao"),
                    ],
                    chemical_formula="UO2",
                    enrichment_percent=3.1,
                    enrichment_target="U235",
                    enrichment_type="wo",
                )
            ],
            cells=[
                CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="uo2")
            ],
            universes=[
                UniverseSpec(id="pin", name="pin universe", cell_ids=["fuel_cell"])
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="1x1 rectangular lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[
                AssemblySpec(
                    id="assembly",
                    name="root assembly",
                    lattice_id="assembly_lattice",
                    boundary="reflective",
                )
            ],
            settings=RunSettingsSpec(batches=6, inactive=1, particles=50),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="assembly",
            executable_subsystems=["rect_lattice", "assembly"],
        ),
        plot_specs=[
            PlotSpec(
                basis="xy",
                width_cm=(1.26, 1.26),
                pixels=(100, 100),
                filename="assembly_xy.png",
            )
        ],
    )

    script = render_openmc_plan_script(plan)

    assert "add_elements_from_formula('UO2', enrichment=3.1)" in script
    assert "add_nuclide('U235', 3.1, 'wo')" not in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    plot_result = subprocess.run(
        [openmc_cli, "-p"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert plot_result.returncode == 0, plot_result.stderr or plot_result.stdout


def test_render_assembly_accepts_composite_prism_region_ids(
    tmp_path: Path,
) -> None:
    """LLM plans may use rectangular_prism surfaces directly as region ids."""
    spec = ComplexModelSpec(
        name="2x2 assembly with composite prism regions",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=4.95),
                    NuclideSpec(name="U238", percent=95.05),
                    NuclideSpec(name="O16", percent=200.0),
                ],
            ),
            ComplexMaterialSpec(
                id="water",
                name="Water",
                density_unit="g/cm3",
                density_value=0.743,
                composition=[
                    NuclideSpec(name="H1", percent=2.0),
                    NuclideSpec(name="O16", percent=1.0),
                ],
            ),
        ],
        surfaces=[
            SurfaceSpec(id="fuel_r", kind="zcylinder", parameters={"r": 0.3}),
            SurfaceSpec(
                id="pin_cell_boundary",
                kind="rectangular_prism",
                parameters={"xmin": -0.63, "xmax": 0.63, "ymin": -0.63, "ymax": 0.63},
            ),
            SurfaceSpec(
                id="assembly_boundary",
                kind="rectangular_prism",
                parameters={"xmin": -1.26, "xmax": 1.26, "ymin": -1.26, "ymax": 1.26},
                boundary_type="reflective",
            ),
        ],
        regions=[
            RegionSpec(id="fuel_region", expression="-fuel_r", surface_ids=["fuel_r"]),
            RegionSpec(
                id="moderator_region",
                expression="+fuel_r & pin_cell_boundary",
                surface_ids=["fuel_r", "pin_cell_boundary"],
            ),
        ],
        cells=[
            CellSpec(
                id="fuel_cell",
                name="fuel",
                region_id="fuel_region",
                fill_type="material",
                fill_id="fuel",
            ),
            CellSpec(
                id="moderator_cell",
                name="moderator",
                region_id="moderator_region",
                fill_type="material",
                fill_id="water",
            ),
            CellSpec(
                id="assembly_cell",
                name="assembly root",
                region_id="assembly_boundary",
                fill_type="lattice",
                fill_id="assembly_lattice",
            ),
        ],
        universes=[
            UniverseSpec(id="pin", name="pin universe", cell_ids=["fuel_cell", "moderator_cell"]),
            UniverseSpec(id="root_universe", name="root", cell_ids=["assembly_cell"]),
        ],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="2x2 rectangular lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[
                    ["pin", "pin"],
                    ["pin", "pin"],
                ],
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root assembly",
                lattice_id="assembly_lattice",
                boundary="reflective",
            )
        ],
        settings=RunSettingsSpec(batches=6, inactive=1, particles=50),
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        complex_model=spec,
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="assembly",
            executable_subsystems=["rect_lattice", "assembly"],
        ),
        plot_specs=[
            PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="assembly_xy.png")
        ],
    )

    capability = RectAssemblyRenderer().can_render(plan)
    assert capability.renderability in {"exportable", "runnable"}
    assert not any("assembly_boundary" in reason for reason in capability.reasons)

    script = render_openmc_assembly_script(spec)
    assert "openmc.model.RectangularPrism" in script
    assert "width=1.26" in script
    assert "regions['assembly_boundary'] = surface_assembly_boundary" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "geometry.xml").exists()


def test_rectangular_prism_width_pair_is_normalized_for_openmc_api() -> None:
    surface = SurfaceSpec(
        id="pin_boundary",
        kind="rectangular_prism",
        parameters={"width": [1.43, 1.43], "axis": "z", "height": 1.0},
    )

    constructor = _surface_constructor(surface)

    assert "openmc.model.RectangularPrism" in constructor
    assert "width=1.43" in constructor
    assert "height=1.43" in constructor
    assert "width=[" not in constructor


def test_region_expression_adds_positive_halfspace_for_bare_primitive_surfaces() -> None:
    expression = _region_expression_to_python(
        "fuel_r -clad_inner_r",
        composite_surface_ids=set(),
    )

    assert expression == "(+surfaces['fuel_r']) & (-surfaces['clad_inner_r'])"


def test_render_assembly_with_reflector_and_control_rod_exports_xml(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="2x2 assembly with reflector and control rod",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="UO2 fuel",
                    density_unit="g/cm3",
                    density_value=10.4,
                    composition=[
                        NuclideSpec(name="U235", percent=4.95),
                        NuclideSpec(name="U238", percent=95.05),
                        NuclideSpec(name="O16", percent=200.0),
                    ],
                ),
                ComplexMaterialSpec(
                    id="absorber",
                    name="B4C absorber",
                    density_unit="g/cm3",
                    density_value=2.52,
                    composition=[
                        NuclideSpec(name="B10", percent=4.0),
                        NuclideSpec(name="C12", percent=1.0),
                    ],
                ),
                ComplexMaterialSpec(
                    id="reflector",
                    name="graphite reflector",
                    density_unit="g/cm3",
                    density_value=1.7,
                    composition=[NuclideSpec(name="C12", percent=1.0)],
                ),
            ],
            surfaces=[
                SurfaceSpec(id="inner_xmin", kind="xplane", parameters={"x0": -1.26}),
                SurfaceSpec(id="inner_xmax", kind="xplane", parameters={"x0": 1.26}),
                SurfaceSpec(id="inner_ymin", kind="yplane", parameters={"y0": -1.26}),
                SurfaceSpec(id="inner_ymax", kind="yplane", parameters={"y0": 1.26}),
                SurfaceSpec(id="outer_xmin", kind="xplane", parameters={"x0": -1.76}, boundary_type="vacuum"),
                SurfaceSpec(id="outer_xmax", kind="xplane", parameters={"x0": 1.76}, boundary_type="vacuum"),
                SurfaceSpec(id="outer_ymin", kind="yplane", parameters={"y0": -1.76}, boundary_type="vacuum"),
                SurfaceSpec(id="outer_ymax", kind="yplane", parameters={"y0": 1.76}, boundary_type="vacuum"),
            ],
            regions=[
                RegionSpec(
                    id="reflector_region",
                    expression=(
                        "+outer_xmin & -outer_xmax & +outer_ymin & -outer_ymax "
                        "& ~(+inner_xmin & -inner_xmax & +inner_ymin & -inner_ymax)"
                    ),
                )
            ],
            cells=[
                CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel"),
                CellSpec(id="control_cell", name="control rod", fill_type="material", fill_id="absorber"),
            ],
            universes=[
                UniverseSpec(id="fuel_pin", name="fuel pin", cell_ids=["fuel_cell"]),
                UniverseSpec(id="control_pin", name="control pin", cell_ids=["control_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="2x2 lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[
                        ["fuel_pin", "control_pin"],
                        ["fuel_pin", "fuel_pin"],
                    ],
                )
            ],
            assemblies=[
                AssemblySpec(
                    id="assembly",
                    name="root assembly",
                    lattice_id="assembly_lattice",
                    boundary="transmission",
                )
            ],
            reflectors=[
                ReflectorSpec(
                    id="radial_reflector",
                    name="radial reflector",
                    material_id="reflector",
                    region_id="reflector_region",
                )
            ],
            control_rods=[
                ControlRodSpec(
                    id="bank_a",
                    name="control rod bank A",
                    absorber_material_id="absorber",
                    position_ids=["control_pin"],
                    state="inserted",
                )
            ],
            settings=RunSettingsSpec(batches=6, inactive=1, particles=50),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="assembly",
            executable_subsystems=["rect_lattice", "reflector", "control_rod"],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(3.52, 3.52), filename="assembly_reflector_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "reflector_cell_radial_reflector = openmc.Cell" in script
    assert "universes['control_pin']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "plots.xml").exists()


def test_render_3d_rectangular_core_with_axial_water_layer_exports_xml(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="mini 3D core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    composition=[
                        NuclideSpec(name="H1", percent=2.0),
                        NuclideSpec(name="O16", percent=1.0),
                    ],
                    sab=["c_H_in_H2O"],
                ),
            ],
            cells=[
                CellSpec(id="pin_cell", name="fuel pin", fill_type="material", fill_id="fuel"),
                CellSpec(
                    id="assembly_cell",
                    name="assembly lattice cell",
                    fill_type="lattice",
                    fill_id="assembly_lattice",
                ),
            ],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="assembly", name="assembly", cell_ids=["assembly_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="2x2 assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                ),
                LatticeSpec(
                    id="core_lattice",
                    name="1x1 core lattice",
                    kind="rect",
                    pitch_cm=(2.52, 2.52),
                    lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["assembly"]],
                ),
            ],
            core=CoreSpec(
                id="core",
                name="3D root core",
                lattice_id="core_lattice",
                boundary="mixed",
                boundary_conditions=CoreBoundarySpec(
                    xmin="reflective",
                    xmax="vacuum",
                    ymin="reflective",
                    ymax="vacuum",
                    zmin="reflective",
                    zmax="vacuum",
                ),
                axial_layers=[
                    AxialLayerSpec(
                        id="fuel",
                        name="fuel active height",
                        z_min_cm=0.0,
                        z_max_cm=10.0,
                        fill={"type": "lattice", "id": "core_lattice"},
                    ),
                    AxialLayerSpec(
                        id="top_water",
                        name="top water reflector",
                        z_min_cm=10.0,
                        z_max_cm=12.0,
                        fill={"type": "material", "id": "water"},
                    ),
                ],
            ),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="core",
            executable_subsystems=["rect_lattice", "core", "axial_layers"],
        ),
        plot_specs=[
            PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="core_xy.png"),
            PlotSpec(basis="xz", origin=(1.26, 0.0, 6.0), width_cm=(2.52, 12.0), filename="core_xz.png"),
            PlotSpec(basis="yz", origin=(0.0, 1.26, 6.0), width_cm=(2.52, 12.0), filename="core_yz.png"),
        ],
    )

    script = render_openmc_plan_script(plan)

    assert "assembly_zmin = openmc.ZPlane(z0=assembly_z_min, boundary_type='reflective')" in script
    assert "root_cell_top_water = openmc.Cell" in script
    assert "fill=materials_by_id['water']" in script
    assert "plot_1_cell.basis = 'xz'" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "materials.xml").exists()
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "settings.xml").exists()
    assert (tmp_path / "plots.xml").exists()


def test_core_renderer_wraps_empty_axial_water_universe(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="axial water universe core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    chemical_formula="H2O",
                ),
            ],
            cells=[CellSpec(id="pin_cell", name="pin", fill_type="material", fill_id="fuel")],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="top_reflector_universe", name="top reflector", cell_ids=[]),
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            core=CoreSpec(
                id="core",
                name="root core",
                lattice_id="core_lattice",
                boundary="vacuum",
                axial_layers=[
                    AxialLayerSpec(
                        id="fuel",
                        name="fuel",
                        z_min_cm=0.0,
                        z_max_cm=10.0,
                        fill={"type": "lattice", "id": "core_lattice"},
                    ),
                    AxialLayerSpec(
                        id="top_reflector",
                        name="Top water reflector",
                        z_min_cm=10.0,
                        z_max_cm=12.0,
                        fill={"type": "universe", "id": "top_reflector_universe"},
                    ),
                ],
            ),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xz", width_cm=(1.26, 12.0), filename="axial_water_universe_xz.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "cell_wrapper_top_reflector_universe = openmc.Cell" in script
    assert "fill=materials_by_id['water']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_xml = (tmp_path / "geometry.xml").read_text(encoding="utf-8")
    assert 'name="auto wrapper for top_reflector_universe"' in geometry_xml


def test_axial_layer_lattice_loading_emits_derived_lattice(tmp_path: Path) -> None:
    """An axial layer with loading_id emits a dedicated derived RectLattice."""
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="override core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel", name="fuel", density_unit="g/cm3",
                    density_value=10.0, chemical_formula="UO2",
                ),
                ComplexMaterialSpec(
                    id="water", name="water", density_unit="g/cm3",
                    density_value=0.997,
                    composition=[
                        NuclideSpec(name="H1", percent=2.0),
                        NuclideSpec(name="O16", percent=1.0),
                    ],
                ),
            ],
            cells=[
                CellSpec(id="pin_cell", name="pin", fill_type="material", fill_id="fuel"),
                CellSpec(id="assembly_cell", name="assembly", fill_type="lattice", fill_id="assembly_lattice"),
                CellSpec(id="assembly2_cell", name="assembly2", fill_type="material", fill_id="fuel"),
            ],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="assembly", name="assembly", cell_ids=["assembly_cell"]),
                UniverseSpec(id="assembly2", name="assembly2", cell_ids=["assembly2_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice", name="asm", kind="rect",
                    pitch_cm=(1.26, 1.26), lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                ),
                LatticeSpec(
                    id="core_lattice", name="core", kind="rect",
                    pitch_cm=(2.52, 2.52), lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["assembly"]],
                ),
            ],
            lattice_loadings=[
                LatticeLoadingSpec(
                    id="rodded_loading",
                    base_lattice_id="core_lattice",
                    derived_lattice_id="rodded_loading_lattice",
                    overrides={"assembly2": [(0, 0)]},
                )
            ],
            core=CoreSpec(
                id="core", name="core", lattice_id="core_lattice",
                axial_layers=[
                    AxialLayerSpec(
                        id="fuel", name="fuel active", z_min_cm=0.0, z_max_cm=10.0,
                        fill={"type": "lattice", "id": "rodded_loading_lattice"},
                        loading_id="rodded_loading",
                    ),
                    AxialLayerSpec(
                        id="top_water", name="top water", z_min_cm=10.0, z_max_cm=12.0,
                        fill={"type": "material", "id": "water"},
                    ),
                ],
            ),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True, supported_renderer="core",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="core_xy.png")],
    )
    script = render_openmc_plan_script(plan)
    # Derived lattice for the fuel layer (it carries lattice-loading overrides).
    assert "axial_lattice_fuel = openmc.RectLattice" in script
    # Override applied: base [["assembly"]] with assembly2 at (0,0) -> [["assembly2"]].
    assert "axial_lattice_fuel.universes = [[universes['assembly2']]]" in script
    assert "lattices['rodded_loading_lattice'] = axial_lattice_fuel" in script
    # Fuel root cell fills the derived lattice, not the base core_lattice directly.
    assert "fill=axial_lattice_fuel" in script
    # Layer without overrides keeps its material fill.
    assert "fill=materials_by_id['water']" in script


def test_core_renderer_wraps_empty_assembly_universes_for_nested_lattices(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="nested core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    composition=[
                        NuclideSpec(name="H1", percent=2.0),
                        NuclideSpec(name="O16", percent=1.0),
                    ],
                ),
            ],
            cells=[
                CellSpec(id="pin_cell", name="fuel pin", fill_type="material", fill_id="fuel"),
            ],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="fuel_assembly", name="fuel assembly", cell_ids=[]),
                UniverseSpec(id="water_assembly", name="water assembly", cell_ids=[]),
            ],
            lattices=[
                LatticeSpec(
                    id="fuel_assembly_lattice",
                    name="2x2 fuel assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                ),
                LatticeSpec(
                    id="core_lattice",
                    name="2x1 core lattice",
                    kind="rect",
                    pitch_cm=(2.52, 2.52),
                    lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["fuel_assembly", "water_assembly"]],
                ),
            ],
            assemblies=[
                AssemblySpec(
                    id="fuel_assembly",
                    name="fuel assembly",
                    lattice_id="fuel_assembly_lattice",
                ),
            ],
            core=CoreSpec(
                id="core",
                name="root core",
                lattice_id="core_lattice",
                boundary="vacuum",
            ),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="core",
            executable_subsystems=["rect_lattice", "core"],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(5.04, 2.52), filename="nested_core_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "cell_wrapper_fuel_assembly = openmc.Cell" in script
    assert "cell_wrapper_fuel_assembly.fill = lattices['fuel_assembly_lattice']" in script
    assert "lattice_fuel_assembly_lattice.outer = universes['water_assembly']" in script
    assert "lattice_core_lattice.outer = universes['water_assembly']" in script
    assert "cell_wrapper_water_assembly = openmc.Cell" in script
    assert "fill=materials_by_id['water']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_xml = (tmp_path / "geometry.xml").read_text(encoding="utf-8")
    assert 'name="auto wrapper for fuel_assembly"' in geometry_xml
    assert 'name="auto wrapper for water_assembly"' in geometry_xml
    assert "<outer>" in geometry_xml
    assert (tmp_path / "plots.xml").exists()


def test_core_renderer_clones_shared_pin_cells_for_reachable_universes(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="shared pin cell core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    chemical_formula="H2O",
                ),
            ],
            cells=[
                CellSpec(id="pin_fuel_cell", name="pin fuel", fill_type="material", fill_id="fuel"),
                CellSpec(id="pin_mod_cell", name="pin moderator", fill_type="material", fill_id="water"),
            ],
            universes=[
                UniverseSpec(id="pin_uo2", name="UO2 pin", cell_ids=["pin_fuel_cell", "pin_mod_cell"]),
                UniverseSpec(id="pin_mox", name="MOX pin", cell_ids=["pin_fuel_cell", "pin_mod_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="2x1 core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin_uo2", "pin_mox"]],
                )
            ],
            core=CoreSpec(id="core", name="root core", lattice_id="core_lattice", boundary="vacuum"),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 1.26), filename="shared_core_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "pin_fuel_cell__for_pin_mox" in script
    assert "pin_mod_cell__for_pin_mox" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_root = ET.parse(tmp_path / "geometry.xml").getroot()
    exported_names = {cell.attrib.get("name") for cell in geometry_root.findall(".//cell")}
    assert "pin fuel for pin_mox" in exported_names
    assert "pin moderator for pin_mox" in exported_names


def test_core_renderer_prefers_assembly_lattice_wrapper_over_bad_pin_cells(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="assembly wrapper core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                )
            ],
            cells=[
                CellSpec(id="pin_fuel_cell", name="pin fuel", fill_type="material", fill_id="fuel"),
            ],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_fuel_cell"]),
                UniverseSpec(id="uo2_assembly", name="UO2 assembly", cell_ids=["pin_fuel_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="uo2_assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                ),
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["uo2_assembly"]],
                ),
            ],
            assemblies=[
                AssemblySpec(id="uo2_assembly", name="UO2 assembly", lattice_id="uo2_assembly_lattice"),
            ],
            core=CoreSpec(id="core", name="root core", lattice_id="core_lattice", boundary="vacuum"),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_wrapper_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "cells['__wrapper_uo2_assembly']" in script
    assert "cell_wrapper_uo2_assembly.fill = lattices['uo2_assembly_lattice']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_xml = (tmp_path / "geometry.xml").read_text(encoding="utf-8")
    assert 'name="auto wrapper for uo2_assembly"' in geometry_xml


def test_core_renderer_material_wrapper_replaces_reused_water_cell(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="water wrapper core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    chemical_formula="H2O",
                )
            ],
            cells=[
                CellSpec(id="pin_mod_cell", name="pin moderator", fill_type="material", fill_id="water"),
            ],
            universes=[
                UniverseSpec(id="water_univ", name="water universe", cell_ids=["pin_mod_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["water_univ"]],
                )
            ],
            core=CoreSpec(id="core", name="root core", lattice_id="core_lattice", boundary="vacuum"),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="water_wrapper_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "cells['__wrapper_water_univ']" in script
    assert "fill=materials_by_id['water']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_xml = (tmp_path / "geometry.xml").read_text(encoding="utf-8")
    assert 'name="auto wrapper for water_univ"' in geometry_xml


def test_core_renderer_materializes_missing_pin_cells_before_validation(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="missing pin cells core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="uo2",
                    name="UO2",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="mox43",
                    name="MOX 4.3",
                    density_unit="g/cm3",
                    density_value=10.0,
                    composition=[NuclideSpec(name="U238", percent=1.0)],
                ),
                ComplexMaterialSpec(
                    id="guide_tube",
                    name="guide tube",
                    density_unit="g/cm3",
                    density_value=6.56,
                    composition=[NuclideSpec(name="Zr", percent=1.0, kind="element")],
                ),
                ComplexMaterialSpec(
                    id="fiss_chamber",
                    name="fission chamber",
                    density_unit="g/cm3",
                    density_value=6.56,
                    composition=[NuclideSpec(name="Zr", percent=1.0, kind="element")],
                ),
                ComplexMaterialSpec(
                    id="water",
                    name="water",
                    density_unit="g/cm3",
                    density_value=0.997,
                    chemical_formula="H2O",
                ),
            ],
            cells=[],
            universes=[
                UniverseSpec(id="pin_uo2", name="UO2 pin", cell_ids=["pin_uo2_fuel_cell", "pin_uo2_mod_cell"]),
                UniverseSpec(id="pin_mox43", name="MOX pin", cell_ids=["pin_mox43_fuel_cell", "pin_mox43_mod_cell"]),
                UniverseSpec(id="pin_guide", name="guide pin", cell_ids=["pin_guide_cyl_cell", "pin_guide_mod_cell"]),
                UniverseSpec(id="pin_fiss", name="fiss pin", cell_ids=["pin_fiss_cyl_cell", "pin_fiss_mod_cell"]),
                UniverseSpec(id="water_universe", name="water block", cell_ids=["water_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[
                        ["pin_uo2", "pin_mox43", "water_universe"],
                        ["pin_guide", "pin_fiss", "water_universe"],
                    ],
                )
            ],
            core=CoreSpec(id="core", name="root core", lattice_id="core_lattice", boundary="vacuum"),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(3.78, 2.52), filename="missing_cells_core_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "cell_pin_uo2_fuel_cell = openmc.Cell" in script
    assert "fill=materials_by_id['uo2']" in script
    assert "cell_pin_mox43_fuel_cell = openmc.Cell" in script
    assert "fill=materials_by_id['mox43']" in script
    assert "cell_pin_guide_cyl_cell = openmc.Cell" in script
    assert "fill=materials_by_id['guide_tube']" in script
    assert "cell_pin_fiss_cyl_cell = openmc.Cell" in script
    assert "fill=materials_by_id['fiss_chamber']" in script
    assert "cell_wrapper_water_universe = openmc.Cell" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    geometry_xml = (tmp_path / "geometry.xml").read_text(encoding="utf-8")
    assert 'name="auto cell for pin_uo2_fuel_cell"' in geometry_xml
    assert 'name="auto wrapper for water_universe"' in geometry_xml


def test_core_renderer_assembly_wrapper_replaces_missing_assembly_cell(
    tmp_path: Path,
) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="missing assembly cell core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="uo2",
                    name="UO2",
                    density_unit="g/cm3",
                    density_value=10.0,
                    chemical_formula="UO2",
                    enrichment_percent=3.3,
                )
            ],
            cells=[CellSpec(id="pin_cell", name="pin", fill_type="material", fill_id="uo2")],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="uo2_assembly", name="UO2 assembly", cell_ids=["uo2_assembly_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="uo2_assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                ),
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["uo2_assembly"]],
                ),
            ],
            assemblies=[
                AssemblySpec(id="uo2_assembly", name="UO2 assembly", lattice_id="uo2_assembly_lattice"),
            ],
            core=CoreSpec(id="core", name="root core", lattice_id="core_lattice", boundary="vacuum"),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_missing_cell_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "uo2_assembly_cell" not in script
    assert "cell_wrapper_uo2_assembly.fill = lattices['uo2_assembly_lattice']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_render_assembly_skips_inactive_candidate_material(tmp_path: Path) -> None:
    """An incomplete candidate material in an un-inserted universe must not block export.

    The default model emits only the active fuel/guide graph. The borosilicate
    glass lives in a candidate burnable-poison universe that is not referenced by
    the lattice, so it is dropped from model.py and the script still exports XML.
    """
    spec = ComplexModelSpec(
        name="assembly with a candidate BP universe",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=4.95),
                    NuclideSpec(name="U238", percent=95.05),
                    NuclideSpec(name="O16", percent=200.0),
                ],
            ),
            # Candidate material: partial density flagged for confirmation, and
            # no composition. Must be tolerated by the schema and skipped by the
            # renderer because its universe is not in the default lattice.
            ComplexMaterialSpec(
                id="borosilicate_glass",
                name="borosilicate glass",
                density_unit="g/cm3",
                requires_human_confirmation=["density value", "composition"],
            ),
        ],
        cells=[
            CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel"),
            CellSpec(
                id="bp_glass_cell",
                name="bp glass",
                fill_type="material",
                fill_id="borosilicate_glass",
            ),
        ],
        universes=[
            UniverseSpec(id="fuel_pin", name="fuel pin", cell_ids=["fuel_cell"]),
            UniverseSpec(
                id="burnable_poison",
                name="candidate BP",
                cell_ids=["bp_glass_cell"],
            ),
        ],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="2x2 lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[
                    ["fuel_pin", "fuel_pin"],
                    ["fuel_pin", "fuel_pin"],
                ],
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root assembly",
                lattice_id="assembly_lattice",
                boundary="reflective",
            )
        ],
        settings=RunSettingsSpec(batches=6, inactive=1, particles=50),
    )

    script = render_openmc_assembly_script(spec)

    assert "model.export_to_xml()" in script
    assert "borosilicate_glass" not in script
    assert "burnable_poison" not in script
    assert "fuel" in script

    # The generated model.py must be executable Python that exports XML.
    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "materials.xml").exists()


def test_render_triso_pebble_exports_xml(tmp_path: Path) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="single pebble TRISO model",
            kind="pebble",
            materials=[
                ComplexMaterialSpec(
                    id="kernel",
                    name="UO2 kernel",
                    density_unit="g/cm3",
                    density_value=10.4,
                    composition=[
                        NuclideSpec(name="U235", percent=4.95),
                        NuclideSpec(name="U238", percent=95.05),
                        NuclideSpec(name="O16", percent=200.0),
                    ],
                ),
                ComplexMaterialSpec(
                    id="buffer",
                    name="carbon buffer",
                    density_unit="g/cm3",
                    density_value=1.0,
                    composition=[NuclideSpec(name="C12", percent=1.0)],
                ),
                ComplexMaterialSpec(
                    id="sic",
                    name="SiC layer",
                    density_unit="g/cm3",
                    density_value=3.2,
                    composition=[
                        NuclideSpec(name="Si28", percent=1.0),
                        NuclideSpec(name="C12", percent=1.0),
                    ],
                ),
                ComplexMaterialSpec(
                    id="matrix",
                    name="graphite matrix",
                    density_unit="g/cm3",
                    density_value=1.7,
                    composition=[NuclideSpec(name="C12", percent=1.0)],
                ),
            ],
            trisos=[
                TRISOSpec(
                    id="triso",
                    name="TRISO particle",
                    matrix_material_id="matrix",
                    packing_algorithm="pack_spheres",
                    layers=[
                        TRISOLayerSpec(name="kernel", material_id="kernel", outer_radius_cm=0.025),
                        TRISOLayerSpec(name="buffer", material_id="buffer", outer_radius_cm=0.035),
                        TRISOLayerSpec(name="sic", material_id="sic", outer_radius_cm=0.045),
                    ],
                )
            ],
            packed_spheres=[
                PackedSphereSpec(
                    id="triso_packing",
                    name="TRISO packing",
                    sphere_radius_cm=0.045,
                    container_region_id="fuel_zone",
                    num_spheres=1,
                    seed=1,
                )
            ],
            pebbles=[
                PebbleSpec(
                    id="pebble",
                    name="fuel pebble",
                    outer_radius_cm=0.2,
                    fuel_zone_radius_cm=0.15,
                    matrix_material_id="matrix",
                    triso_spec_id="triso",
                )
            ],
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="triso",
            executable_subsystems=["triso_layers", "packing", "pebble"],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(0.4, 0.4), filename="triso_pebble_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "openmc.model.pack_spheres" in script
    assert "openmc.model.TRISO" in script
    assert "triso_universe = openmc.Universe" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "plots.xml").exists()


def test_render_rectangular_core_lattice_exports_xml(tmp_path: Path) -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="2x2 core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="assembly homogenized fuel",
                    density_unit="g/cm3",
                    density_value=3.0,
                    composition=[
                        NuclideSpec(name="U235", percent=4.95),
                        NuclideSpec(name="U238", percent=95.05),
                        NuclideSpec(name="O16", percent=200.0),
                    ],
                )
            ],
            cells=[
                CellSpec(
                    id="assembly_cell",
                    name="assembly material cell",
                    fill_type="material",
                    fill_id="fuel",
                )
            ],
            universes=[
                UniverseSpec(
                    id="assembly_universe",
                    name="homogenized assembly",
                    cell_ids=["assembly_cell"],
                )
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="2x2 core lattice",
                    kind="rect",
                    pitch_cm=(21.42, 21.42),
                    universe_pattern=[
                        ["assembly_universe", "assembly_universe"],
                        ["assembly_universe", "assembly_universe"],
                    ],
                )
            ],
            core=CoreSpec(
                id="core",
                name="root core",
                lattice_id="core_lattice",
                boundary="vacuum",
            ),
            settings=RunSettingsSpec(batches=4, inactive=1, particles=20),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="core",
            executable_subsystems=["rect_lattice", "core"],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(42.84, 42.84), filename="core_xy.png")],
    )

    script = render_openmc_plan_script(plan)

    assert "Generated OpenMC core model" in script
    assert "lattices['core_lattice']" in script

    model_path = tmp_path / "model.py"
    model_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "plots.xml").exists()


def _quarter_core_plan(
    *,
    lower_left_cm: tuple[float, float] | None,
    plot_specs: list[PlotSpec] | None = None,
) -> SimulationPlan:
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="quarter core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    density_unit="g/cm3",
                    density_value=3.0,
                    composition=[
                        NuclideSpec(name="U235", percent=4.95),
                        NuclideSpec(name="U238", percent=95.05),
                        NuclideSpec(name="O16", percent=200.0),
                    ],
                )
            ],
            cells=[
                CellSpec(
                    id="assembly_cell",
                    name="assembly",
                    fill_type="material",
                    fill_id="fuel",
                )
            ],
            universes=[
                UniverseSpec(
                    id="assembly_universe",
                    name="assembly",
                    cell_ids=["assembly_cell"],
                )
            ],
            lattices=[
                LatticeSpec(
                    id="core_lattice",
                    name="core lattice",
                    kind="rect",
                    pitch_cm=(21.42, 21.42),
                    lower_left_cm=lower_left_cm,
                    universe_pattern=[
                        ["assembly_universe", "assembly_universe"],
                        ["assembly_universe", "assembly_universe"],
                    ],
                )
            ],
            core=CoreSpec(
                id="core",
                name="core",
                lattice_id="core_lattice",
                boundary="mixed",
                boundary_conditions=CoreBoundarySpec(
                    xmin="reflective",
                    xmax="vacuum",
                    ymin="vacuum",
                    ymax="reflective",
                    zmin="reflective",
                    zmax="vacuum",
                ),
                axial_layers=[
                    AxialLayerSpec(
                        id="fuel_layer",
                        name="fuel layer",
                        z_min_cm=0.0,
                        z_max_cm=100.0,
                        fill={"type": "lattice", "id": "core_lattice"},
                    )
                ],
            ),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="core",
            executable_subsystems=["rect_lattice", "core"],
        ),
        plot_specs=plot_specs
        or [PlotSpec(basis="xy", width_cm=(42.84, 42.84), filename="core_xy.png")],
    )


def test_core_lattice_falls_back_to_non_negative_quadrant() -> None:
    """A core lattice with no explicit placement sits in [0, W] x [0, H].

    The C5G7 / case3.md convention is non-negative coordinates with the origin at
    a core corner, matching the plot origin the LLM writes. The renderer must
    place the *global* core lattice at (0, 0) when neither lower_left_cm nor
    center_cm is set, so the plot viewport lands on the fuel instead of the
    moderator-only outer ring.
    """
    script = render_openmc_plan_script(_quarter_core_plan(lower_left_cm=None))

    assert "lattice_core_lattice.lower_left = (0.0, 0.0)" in script
    assert "assembly_x_min = 0.0" in script
    assert "assembly_x_max = 42.84" in script
    assert "assembly_y_min = 0.0" in script
    assert "assembly_y_max = 42.84" in script


def test_core_lattice_respects_explicit_lower_left() -> None:
    """An explicit lower_left_cm is not overridden by the non-negative fallback."""
    script = render_openmc_plan_script(_quarter_core_plan(lower_left_cm=(-10.0, -10.0)))

    assert "lattice_core_lattice.lower_left = (-10.0, -10.0)" in script
    assert "assembly_x_min = -10.0" in script
    assert "assembly_x_max = 32.84" in script  # -10 + 2*21.42


def test_core_renderer_centers_nested_pin_lattice_in_local_frame() -> None:
    """Nested pin/assembly lattices are centered; only the core lattice is at (0,0).

    OpenMC aligns a lattice-filled universe's origin to the center of each
    core-lattice cell, so a pin lattice reused inside such a universe must be
    centered (lower_left = -cols*pitch/2) to fill the cell. An LLM-style
    lower_left=[0,0] pushes it into one quadrant and only ~1/4 of the pins
    render (the rest is replaced by ``outer``) — the C5G7 "assembly shrinks to
    a quarter" regression. Single-layer assembly rendering is unaffected: it
    fills a root cell that carries an explicit region.
    """
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="nested core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel", name="fuel", density_unit="g/cm3",
                    density_value=10.0, chemical_formula="UO2", enrichment_percent=3.3,
                ),
                ComplexMaterialSpec(
                    id="water", name="water", density_unit="g/cm3",
                    density_value=0.997, chemical_formula="H2O",
                ),
            ],
            cells=[CellSpec(id="pin_cell", name="fuel pin", fill_type="material", fill_id="fuel")],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["pin_cell"]),
                UniverseSpec(id="fuel_assembly", name="fuel assembly", cell_ids=[]),
                UniverseSpec(id="water_assembly", name="water assembly", cell_ids=[]),
            ],
            lattices=[
                LatticeSpec(
                    id="fuel_assembly_lattice", name="2x2 pin lattice", kind="rect",
                    pitch_cm=(1.26, 1.26), lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                ),
                LatticeSpec(
                    id="core_lattice", name="1x2 core lattice", kind="rect",
                    pitch_cm=(2.52, 2.52), lower_left_cm=(0.0, 0.0),
                    universe_pattern=[["fuel_assembly", "water_assembly"]],
                ),
            ],
            assemblies=[
                AssemblySpec(id="fuel_assembly", name="fuel assembly", lattice_id="fuel_assembly_lattice"),
            ],
            core=CoreSpec(
                id="core", name="root core", lattice_id="core_lattice",
                boundary="mixed",
                boundary_conditions=CoreBoundarySpec(
                    xmin="reflective", xmax="vacuum", ymin="vacuum",
                    ymax="reflective", zmin="reflective", zmax="vacuum",
                ),
                axial_layers=[
                    AxialLayerSpec(
                        id="fuel", name="fuel active", z_min_cm=0.0, z_max_cm=10.0,
                        fill={"type": "lattice", "id": "core_lattice"},
                    ),
                ],
            ),
        ),
        capability_report=RenderCapabilityReport(is_executable=True, supported_renderer="core"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="core_xy.png")],
    )
    script = render_openmc_plan_script(plan)
    # Nested pin lattice centered in its universe local frame: -2*1.26/2 = -1.26
    assert "lattice_fuel_assembly_lattice.lower_left = (-1.26, -1.26)" in script
    # The global core lattice keeps the non-negative frame.
    assert "lattice_core_lattice.lower_left = (0.0, 0.0)" in script


def test_plot_origin_on_x_boundary_nudged_to_assembly_center() -> None:
    """A yz slice at x = core x-edge is moved to the nearest assembly center.

    OpenMC cell regions are open intervals, so a slice at x=0 (reflective edge) or
    x=W (vacuum edge) samples no cell and renders as a uniform fill. The renderer
    must move it to the nearest assembly-center x and record the adjustment.
    """
    plan = _quarter_core_plan(
        lower_left_cm=None,
        plot_specs=[
            PlotSpec(
                basis="yz",
                origin=(0.0, 21.42, 50.0),
                width_cm=(42.84, 100.0),
                filename="core_yz_sym.png",
            )
        ],
    )
    script = render_openmc_plan_script(plan)

    # x=0 (xmin reflective edge) -> core-center assembly 32.13
    assert "plot_0_cell.origin = (32.13, 21.42, 50.0)" in script
    assert "renderer nudged origin x 0 -> 32.13" in script


def test_plot_origin_on_y_boundary_nudged_for_xz_slice() -> None:
    """An xz slice at y = core y-edge is moved to the nearest assembly center."""
    plan = _quarter_core_plan(
        lower_left_cm=None,
        plot_specs=[
            PlotSpec(
                basis="xz",
                origin=(21.42, 0.0, 50.0),
                width_cm=(42.84, 100.0),
                filename="core_xz_sym.png",
            )
        ],
    )
    script = render_openmc_plan_script(plan)

    # y=0 (ymin vacuum edge) -> core-center assembly 32.13
    assert "plot_0_cell.origin = (21.42, 32.13, 50.0)" in script
    assert "renderer nudged origin y 0 -> 32.13" in script


def test_plot_origin_interior_is_not_modified() -> None:
    """A slice whose coordinate is strictly interior is left untouched."""
    plan = _quarter_core_plan(
        lower_left_cm=None,
        plot_specs=[
            PlotSpec(
                basis="yz",
                origin=(10.71, 21.42, 50.0),
                width_cm=(42.84, 100.0),
                filename="core_yz_interior.png",
            )
        ],
    )
    script = render_openmc_plan_script(plan)

    assert "plot_0_cell.origin = (10.71, 21.42, 50.0)" in script
    assert "renderer nudged" not in script
