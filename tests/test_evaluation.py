from pathlib import Path

from openmc_agent.evaluation import DEFAULT_TEST_CASES, format_summary, run_test_set
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.records import load_jsonl_records
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
from openmc_agent.tools import ToolResult


def material(name: str) -> MaterialSpec:
    return MaterialSpec(
        name=name,
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )


def simulation() -> SimulationSpec:
    return SimulationSpec(
        name="UO2 pin-cell criticality",
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
            moderator=material("Water moderator"),
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def simulation_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=simulation(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(200, 200),
                filename="pin_cell_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100)
        ),
    )


def invalid_simulation() -> SimulationSpec:
    spec = simulation()
    spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )
    return spec


def test_default_test_cases_cover_step_11_categories() -> None:
    assert len(DEFAULT_TEST_CASES) == 10
    assert sum(case.kind == "material" for case in DEFAULT_TEST_CASES) == 3
    assert sum(case.kind == "pin_cell" for case in DEFAULT_TEST_CASES) == 4
    assert sum(case.kind == "repair" for case in DEFAULT_TEST_CASES) == 2
    assert sum(case.kind == "impossible" for case in DEFAULT_TEST_CASES) == 1


def test_run_test_set_records_success_rate(tmp_path: Path) -> None:
    def fake_generate_material(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=material(requirement))

    def fake_generate_simulation(*, requirement: str, schema, model: str):
        if "异常" in requirement:
            return StructuredOutputResult(ok=True, value=invalid_simulation())
        if "无法完成" in requirement:
            return StructuredOutputResult(ok=False, error="unsupported boundary case")
        return StructuredOutputResult(ok=True, value=simulation())

    def fake_repair_simulation(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=simulation())

    summary = run_test_set(
        output_dir=tmp_path / "outputs",
        material_records_path=tmp_path / "materials.jsonl",
        simulation_records_path=tmp_path / "simulations.jsonl",
        generate_material=fake_generate_material,
        generate_simulation=fake_generate_simulation,
        repair_simulation=fake_repair_simulation,
    )

    assert summary.total == 10
    assert summary.completed == 9
    assert summary.success_rate == 0.9
    assert summary.meets_threshold is True
    assert len(load_jsonl_records(tmp_path / "materials.jsonl")) == 3
    assert len(load_jsonl_records(tmp_path / "simulations.jsonl")) == 7
    text = format_summary(summary)
    assert "completed=9/10" in text
    assert "success_rate=90.0%" in text
    assert "threshold_met=True" in text


def test_run_test_set_can_use_plan_workflow_with_tools(tmp_path: Path) -> None:
    calls = {"export": 0, "plot": 0, "smoke": 0}

    def fake_generate_material(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=material(requirement))

    def fake_generate_plan(*, requirement: str, schema, model: str):
        if "无法完成" in requirement:
            return StructuredOutputResult(ok=False, error="unsupported boundary case")
        return StructuredOutputResult(ok=True, value=simulation_plan())

    def fake_repair_plan(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationPlan,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=simulation_plan())

    def fake_export(model_path):
        calls["export"] += 1
        return ToolResult(name="export_xml", ok=True, returncode=0)

    def fake_plot(run_dir):
        calls["plot"] += 1
        return ToolResult(name="run_geometry_plots", ok=True, returncode=0)

    def fake_smoke(run_dir, plan):
        calls["smoke"] += 1
        return ToolResult(name="run_smoke_test", ok=True, returncode=0, stdout="ok")

    summary = run_test_set(
        output_dir=tmp_path / "outputs",
        material_records_path=tmp_path / "materials.jsonl",
        simulation_records_path=tmp_path / "simulations.jsonl",
        generate_material=fake_generate_material,
        use_plan=True,
        enable_plots=True,
        enable_smoke_test=True,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=fake_export,
        plot_tool=fake_plot,
        smoke_test_tool=fake_smoke,
    )

    assert summary.completed == 9
    assert calls == {"export": 6, "plot": 6, "smoke": 6}
