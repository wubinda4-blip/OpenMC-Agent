import openmc
import subprocess
import sys
from pathlib import Path

from openmc_agent.executor import (
    build_openmc_complex_material,
    build_openmc_material,
    render_openmc_assembly_script,
    render_openmc_plan_script,
    render_openmc_script,
    render_openmc_smoke_test_script,
)
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    ControlRodSpec,
    CoreSpec,
    ExecutionCheckSpec,
    GeometrySpec,
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
