from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from openmc_agent.graph import build_graph, build_plan_graph
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


def make_simulation_spec() -> SimulationSpec:
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
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def make_simulation_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=make_simulation_spec(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(300, 300),
                color_by="material",
                filename="pin_cell_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100),
            expected_checks=["OpenMC starts without geometry errors"],
        ),
    )


def test_graph_generates_model_script_and_record(tmp_path: Path) -> None:
    def fake_generate_spec(*, requirement: str, schema, model: str):
        assert requirement == "建立一个 UO2 pin-cell 临界计算"
        assert schema is SimulationSpec
        assert model == "test:model"
        return StructuredOutputResult(ok=True, value=make_simulation_spec())

    graph = build_graph(generate_spec=fake_generate_spec)
    records_path = tmp_path / "runs.jsonl"

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(records_path),
        }
    )

    model_path = Path(state["model_path"])
    assert model_path == tmp_path / "model.py"
    assert model_path.exists()
    assert "model.export_to_xml()" in model_path.read_text(encoding="utf-8")
    assert state["validation_report"].is_valid is True

    records = load_jsonl_records(records_path)
    assert len(records) == 1
    assert records[0]["requirement"] == "建立一个 UO2 pin-cell 临界计算"
    assert records[0]["model"] == "test:model"
    assert records[0]["simulation_spec"]["name"] == "UO2 pin-cell criticality"
    assert records[0]["validation_report"]["is_valid"] is True


def test_graph_repairs_invalid_spec_once_before_rendering(tmp_path: Path) -> None:
    invalid_spec = make_simulation_spec()
    invalid_spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )
    calls = {"generate": 0, "repair": 0}

    def fake_generate_spec(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        return StructuredOutputResult(ok=True, value=invalid_spec)

    def fake_repair_spec(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        calls["repair"] += 1
        assert previous_spec is invalid_spec
        assert any("fuel_radius_cm" in error for error in validation_errors)
        return StructuredOutputResult(ok=True, value=make_simulation_spec())

    graph = build_graph(
        generate_spec=fake_generate_spec,
        repair_spec=fake_repair_spec,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls == {"generate": 1, "repair": 1}
    assert state["retry_count"] == 1
    assert state["validation_report"].is_valid is True
    assert Path(state["model_path"]).exists()


def test_graph_stops_when_single_repair_still_fails(tmp_path: Path) -> None:
    invalid_spec = make_simulation_spec()
    invalid_spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )

    def fake_generate_spec(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=invalid_spec)

    def fake_repair_spec(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=invalid_spec)

    graph = build_graph(
        generate_spec=fake_generate_spec,
        repair_spec=fake_repair_spec,
        max_retries=1,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert state["retry_count"] == 1
    assert state["validation_report"].is_valid is False
    assert "fuel_radius_cm" in state["error"]
    assert "model_path" not in state


def test_graph_retries_up_to_three_times_before_rendering(tmp_path: Path) -> None:
    invalid_spec = make_simulation_spec()
    invalid_spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )
    calls = {"repair": 0}

    def fake_generate_spec(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=invalid_spec)

    def fake_repair_spec(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        calls["repair"] += 1
        if calls["repair"] < 3:
            return StructuredOutputResult(ok=True, value=invalid_spec)
        return StructuredOutputResult(ok=True, value=make_simulation_spec())

    graph = build_graph(
        generate_spec=fake_generate_spec,
        repair_spec=fake_repair_spec,
        max_retries=3,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls["repair"] == 3
    assert state["retry_count"] == 3
    assert state["validation_report"].is_valid is True
    assert len(state["retry_history"]) == 4
    assert Path(state["model_path"]).exists()


def test_graph_uses_sqlite_checkpointer(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoints.sqlite"

    def fake_generate_spec(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_spec())

    graph = build_graph(
        generate_spec=fake_generate_spec,
        checkpoint_path=checkpoint_path,
    )
    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        },
        {"configurable": {"thread_id": "step-10-test"}},
    )

    assert state["validation_report"].is_valid is True
    assert checkpoint_path.exists()
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        checkpoints = list(
            saver.list({"configurable": {"thread_id": "step-10-test"}})
        )
    assert checkpoints


def test_plan_graph_executes_export_plot_and_smoke_tools(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        assert schema is SimulationPlan
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export_xml(model_path: str | Path):
        calls.append("export_xml")
        return ToolResult(name="export_xml", ok=True, returncode=0, artifacts=["materials.xml"])

    def fake_plot(run_dir: str | Path):
        calls.append("run_geometry_plots")
        return ToolResult(name="run_geometry_plots", ok=True, returncode=0, artifacts=["pin_cell_xy.png"])

    def fake_smoke(run_dir: str | Path, plan: SimulationPlan):
        calls.append("run_smoke_test")
        assert plan.execution_check.settings.particles == 100
        return ToolResult(name="run_smoke_test", ok=True, returncode=0, stdout="k-effective 1.0")

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export_xml,
        plot_tool=fake_plot,
        smoke_test_tool=fake_smoke,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell，并绘制 xy 截面图",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls == ["export_xml", "run_geometry_plots", "run_smoke_test"]
    assert state["validation_report"].is_valid is True
    assert [result["name"] for result in state["tool_results"]] == [
        "export_xml",
        "run_geometry_plots",
        "run_smoke_test",
    ]
    assert "plot_0.basis = 'xy'" in Path(state["model_path"]).read_text(encoding="utf-8")


def test_plan_graph_reflects_after_smoke_test_failure(tmp_path: Path) -> None:
    calls = {"repair": 0, "smoke": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_repair_plan(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationPlan,
        validation_errors: list[str],
    ):
        calls["repair"] += 1
        assert schema is SimulationPlan
        assert any("cross section" in error for error in validation_errors)
        assert "No cross_sections.xml" in requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export_xml(model_path: str | Path):
        return ToolResult(name="export_xml", ok=True, returncode=0)

    def fake_plot(run_dir: str | Path):
        return ToolResult(name="run_geometry_plots", ok=True, returncode=0)

    def fake_smoke(run_dir: str | Path, plan: SimulationPlan):
        calls["smoke"] += 1
        if calls["smoke"] == 1:
            return ToolResult(
                name="run_smoke_test",
                ok=False,
                returncode=1,
                stderr="ERROR: No cross_sections.xml was specified",
                error="ERROR: No cross_sections.xml was specified",
            )
        return ToolResult(name="run_smoke_test", ok=True, returncode=0, stdout="ok")

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=fake_export_xml,
        plot_tool=fake_plot,
        smoke_test_tool=fake_smoke,
        max_retries=2,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls == {"repair": 1, "smoke": 2}
    assert state["retry_count"] == 1
    assert state["validation_report"].is_valid is True


def test_plan_graph_includes_expert_feedback_in_generation_prompt(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        assert "xy 图不够，要增加 xz 截面" in requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "expert_feedback": ["xy 图不够，要增加 xz 截面"],
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert state["validation_report"].is_valid is True
