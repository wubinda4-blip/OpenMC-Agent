import json

import pytest
from pydantic import ValidationError

from openmc_agent.schemas import (
    AxialLayerSpec,
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    CoreBoundarySpec,
    ExecutionCheckSpec,
    GeometrySpec,
    FillRefSpec,
    LatticeSpec,
    LatticeLoadingSpec,
    MaterialSpec,
    NuclideSpec,
    PebbleSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    SimulationSpec,
    TRISOLayerSpec,
    TRISOSpec,
    UniverseSpec,
)


def test_uo2_material_validates_and_serializes_to_json() -> None:
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

    payload = json.loads(spec.model_dump_json())

    assert payload["name"] == "UO2 fuel"
    assert payload["density_unit"] == "g/cm3"
    assert payload["density_value"] == 10.4
    assert payload["composition"][0]["name"] == "U235"


def test_core_3d_axial_layers_and_boundary_spec_validate() -> None:
    core = CoreSpec(
        id="core",
        name="quarter core",
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
                z_max_cm=192.78,
                fill={"type": "lattice", "id": "core_lattice"},
            ),
            AxialLayerSpec(
                id="top_water",
                name="top water reflector",
                z_min_cm=192.78,
                z_max_cm=214.2,
                fill={"type": "material", "id": "water"},
            ),
        ],
    )

    assert core.boundary_conditions.xmin == "reflective"
    assert core.axial_layers[1].fill.type == "material"


def test_axial_layer_rejects_empty_non_void_fill_and_bad_height() -> None:
    with pytest.raises(ValidationError):
        AxialLayerSpec(
            id="fuel",
            name="fuel",
            z_min_cm=1.0,
            z_max_cm=1.0,
            fill={"type": "lattice", "id": "core_lattice"},
        )
    with pytest.raises(ValidationError):
        AxialLayerSpec(
            id="fuel",
            name="fuel",
            z_min_cm=0.0,
            z_max_cm=1.0,
            fill={"type": "lattice"},
        )


def test_fill_ref_and_axial_layer_loading_fields_validate() -> None:
    assert FillRefSpec(type="void", id="ignored").id is None
    with pytest.raises(ValidationError):
        FillRefSpec(type="material")

    layer = AxialLayerSpec(
        id="fuel", name="fuel", z_min_cm=0.0, z_max_cm=10.0,
        fill={"type": "lattice", "id": "core_lattice"},
    )
    assert layer.loading_id is None

    loading_layer = AxialLayerSpec(
        id="fuel", name="fuel", z_min_cm=0.0, z_max_cm=10.0,
        fill={"type": "lattice", "id": "rodded_loading_lattice"},
        loading_id="rodded_loading",
    )
    loading = LatticeLoadingSpec(
        id="rodded_loading",
        base_lattice_id="core_lattice",
        overrides={"rod_assembly": [(0, 0), (1, 1)]},
    )
    assert loading_layer.loading_id == "rodded_loading"
    assert loading.overrides == {"rod_assembly": [(0, 0), (1, 1)]}


def test_material_without_density_value_fails_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MaterialSpec(
            name="UO2 fuel",
            density_unit="g/cm3",
            composition=[NuclideSpec(name="U235", percent=1.0)],
        )

    assert "density_value" in str(exc_info.value)


def test_material_with_empty_composition_fails_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MaterialSpec(
            name="Water",
            density_unit="g/cm3",
            density_value=1.0,
            composition=[],
        )

    assert "composition" in str(exc_info.value)


def _uo2_pin_cell_simulation() -> SimulationSpec:
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
    return SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=RunSettingsSpec(batches=50, inactive=10, particles=1000),
    )


def test_simulation_plan_validates_and_serializes_plot_and_smoke_settings() -> None:
    plan = SimulationPlan(
        model_spec=_uo2_pin_cell_simulation(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(500, 500),
                color_by="material",
                filename="pin_cell_xy.png",
                purpose="Check fuel/moderator geometry in the pin-cell midplane.",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100),
            expected_checks=["model exports XML", "smoke test starts without geometry errors"],
        ),
        expert_assumptions=["Reflective boundaries represent an infinite pin-cell."],
    )

    payload = json.loads(plan.model_dump_json())

    assert payload["schema_version"] == "simulation_plan.v1"
    assert payload["plot_specs"][0]["basis"] == "xy"
    assert payload["plot_specs"][0]["origin"] == [0.0, 0.0, 0.0]
    assert payload["plot_specs"][0]["width_cm"] == [1.26, 1.26]
    assert payload["execution_check"]["settings"]["particles"] == 100
    assert payload["expert_assumptions"] == [
        "Reflective boundaries represent an infinite pin-cell."
    ]


def test_plot_spec_rejects_invalid_basis() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PlotSpec(
            basis="abc",
            origin=(0.0, 0.0, 0.0),
            width_cm=(1.0, 1.0),
            pixels=(100, 100),
            filename="bad.png",
        )

    assert "basis" in str(exc_info.value)


def test_run_settings_rejects_invalid_smoke_test_counts() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunSettingsSpec(batches=5, inactive=1, particles=-1)

    assert "particles" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        RunSettingsSpec(batches=5, inactive=5, particles=100)

    assert "inactive" in str(exc_info.value)


def test_complex_assembly_ir_validates_without_executable_material_cards() -> None:
    complex_model = ComplexModelSpec(
        name="PWR assembly IR",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel material from source document",
                chemical_formula="UO2",
                requires_human_confirmation=["density", "enrichment", "temperature"],
            )
        ],
        cells=[
            CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")
        ],
        universes=[UniverseSpec(id="pin_universe", name="pin", cell_ids=["fuel_cell"])],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="17x17 lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin_universe", "pin_universe"]],
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="single assembly",
                lattice_id="assembly_lattice",
                pitch_cm=21.42,
            )
        ],
    )
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=complex_model,
        capability_report=RenderCapabilityReport(
            is_executable=False,
            supported_renderer="none",
            unsupported_subsystems=["lattices", "assemblies"],
            reasons=["Assembly renderer is not implemented yet."],
        ),
        plot_specs=[
            PlotSpec(
                basis="xy",
                width_cm=(21.42, 21.42),
                filename="assembly_xy.png",
            )
        ],
    )

    payload = plan.model_dump(mode="json")

    assert payload["schema_version"] == "simulation_plan.v2"
    assert payload["complex_model"]["lattices"][0]["kind"] == "rect"
    assert payload["capability_report"]["is_executable"] is False


def test_complex_assembly_infers_missing_assembly_from_single_rect_lattice() -> None:
    complex_model = ComplexModelSpec(
        name="PWR assembly IR",
        kind="assembly",
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="15x15 lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=[["fuel_pin_universe"]],
            )
        ],
    )

    assert len(complex_model.assemblies) == 1
    assert complex_model.assemblies[0].lattice_id == "assembly_lattice"


def test_complex_assembly_infers_lattice_from_root_cell_fill() -> None:
    complex_model = ComplexModelSpec(
        name="PWR assembly IR",
        kind="assembly",
        cells=[
            CellSpec(
                id="assembly_cell",
                name="Assembly root cell",
                fill_type="lattice",
                fill_id="assembly_lattice",
            )
        ],
        lattices=[
            LatticeSpec(
                id="candidate_lattice",
                name="candidate lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=[["fuel_pin_universe"]],
            ),
            LatticeSpec(
                id="assembly_lattice",
                name="15x15 lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=[["fuel_pin_universe"]],
            ),
        ],
    )

    assert len(complex_model.assemblies) == 1
    assert complex_model.assemblies[0].lattice_id == "assembly_lattice"


def test_complex_assembly_does_not_guess_between_multiple_lattices() -> None:
    complex_model = ComplexModelSpec(
        name="ambiguous assembly IR",
        kind="assembly",
        lattices=[
            LatticeSpec(
                id="first_lattice",
                name="first lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=[["fuel_pin_universe"]],
            ),
            LatticeSpec(
                id="second_lattice",
                name="second lattice",
                kind="rect",
                pitch_cm=(1.43, 1.43),
                universe_pattern=[["fuel_pin_universe"]],
            ),
        ],
    )

    assert complex_model.assemblies == []


def test_complex_material_with_partial_density_and_confirmation_is_accepted() -> None:
    """A candidate material flagged for confirmation may carry a partial density.

    The LLM often knows the density unit (or an estimate) for a burnable-poison
    candidate material but not the value (or vice versa). The schema must accept
    that partial state so the plan can be constructed; the capability layer then
    decides whether the gap blocks based on whether the material is actually used
    by the default model.
    """
    # density_unit set, density_value omitted, flagged for confirmation.
    material = ComplexMaterialSpec(
        id="borosilicate_glass",
        name="borosilicate glass",
        density_unit="g/cm3",
        requires_human_confirmation=["density value", "composition", "boron isotope abundance"],
    )
    assert material.density_unit == "g/cm3"
    assert material.density_value is None

    # density_value set, density_unit omitted, flagged for confirmation.
    material_value_only = ComplexMaterialSpec(
        id="borosilicate_glass",
        name="borosilicate glass",
        density_value=2.23,
        requires_human_confirmation=["density unit", "composition"],
    )
    assert material_value_only.density_value == 2.23
    assert material_value_only.density_unit is None


def test_complex_material_accepts_multigroup_macroscopic_data() -> None:
    material = ComplexMaterialSpec(
        id="uo2",
        name="UO2",
        density_unit="macro",
        density_value=1.0,
        macroscopic="uo2",
        source="C5G7 seven-group macroscopic benchmark data",
    )

    assert material.macroscopic == "uo2"
    assert material.composition == []
    assert material.chemical_formula is None


def test_complex_material_partial_density_without_confirmation_still_rejected() -> None:
    """A partial density with no confirmation flag is still a malformed material."""
    with pytest.raises(ValidationError) as exc_info:
        ComplexMaterialSpec(
            id="fuel",
            name="fuel",
            density_unit="g/cm3",
            composition=[NuclideSpec(name="U235", percent=1.0)],
        )
    assert "provided together" in str(exc_info.value)


def test_complex_only_plan_must_be_marked_non_executable() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SimulationPlan(
            schema_version="simulation_plan.v2",
            model_spec=None,
            complex_model=ComplexModelSpec(
                name="complex model",
                kind="core",
                core=CoreSpec(id="core", name="core"),
                requires_human_confirmation=["core loading pattern"],
            ),
            plot_specs=[PlotSpec(basis="xy", width_cm=(1.0, 1.0), filename="core_xy.png")],
        )

    assert "pin_cell executable plans require model_spec" in str(exc_info.value)


def test_triso_and_pebble_ir_validate_layer_ordering() -> None:
    triso = TRISOSpec(
        id="triso",
        name="TRISO particle",
        layers=[
            TRISOLayerSpec(name="kernel", material_id="kernel", outer_radius_cm=0.025),
            TRISOLayerSpec(name="buffer", material_id="buffer", outer_radius_cm=0.035),
            TRISOLayerSpec(name="sic", material_id="sic", outer_radius_cm=0.045),
        ],
        matrix_material_id="graphite",
        packing_fraction=0.35,
        packing_algorithm="pack_spheres",
    )
    pebble = PebbleSpec(
        id="pebble",
        name="fuel pebble",
        outer_radius_cm=3.0,
        fuel_zone_radius_cm=2.5,
        triso_spec_id="triso",
        matrix_material_id="graphite",
    )

    assert triso.layers[-1].outer_radius_cm == 0.045
    assert pebble.fuel_zone_radius_cm == 2.5

    with pytest.raises(ValidationError) as exc_info:
        TRISOSpec(
            id="bad",
            name="bad TRISO",
            layers=[
                TRISOLayerSpec(name="outer", material_id="outer", outer_radius_cm=0.04),
                TRISOLayerSpec(name="inner", material_id="inner", outer_radius_cm=0.03),
            ],
        )

    assert "strictly increasing" in str(exc_info.value)


def test_lattice_tolerates_null_rings_and_universe_pattern() -> None:
    """LLMs sometimes emit ``null`` for optional list fields; coerce to empty lists."""
    lattice = LatticeSpec(
        id="assembly_lattice",
        name="17x17 lattice",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        universe_pattern=[["pin_universe", "pin_universe"]],
        rings=None,
    )
    assert lattice.rings == []

    incomplete = LatticeSpec(
        id="incomplete",
        name="incomplete",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        universe_pattern=None,
    )

    assert incomplete.universe_pattern == []
    assert incomplete.requires_human_confirmation == [
        "rect lattice universe_pattern is missing"
    ]
