import json
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from openmc_agent.graph import _render_plan_script, build_graph, build_plan_graph
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.records import load_jsonl_records
from openmc_agent.renderers.base import RenderResult
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    ExecutionCheckSpec,
    GeometrySpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
    UniverseSpec,
    ValidationIssue,
    ValidationReport,
)
from openmc_agent.retrieval import RetrievalStep
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


def make_plan_with_fuel_confirmations(confirmations: list[str]) -> SimulationPlan:
    spec = make_simulation_spec()
    fuel = spec.pin_cell.fuel.model_copy(
        update={"requires_human_confirmation": confirmations}
    )
    pin_cell = spec.pin_cell.model_copy(update={"fuel": fuel})
    spec = spec.model_copy(update={"pin_cell": pin_cell})
    return SimulationPlan(
        model_spec=spec,
        capability_report=RenderCapabilityReport(
            is_executable=True,
            supported_renderer="pin_cell",
            required_human_confirmations=confirmations,
        ),
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


def make_complex_plan_for_temperature_patch() -> SimulationPlan:
    complex_model = ComplexModelSpec(
        name="assembly IR",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel",
                chemical_formula="UO2",
                temperature_k=600,
                requires_human_confirmation=["temperature"],
            )
        ],
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="assembly lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin"]],
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="assembly",
                lattice_id="assembly_lattice",
                boundary="vacuum",
            )
        ],
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=complex_model,
        capability_report=RenderCapabilityReport(
            is_executable=False,
            supported_renderer="none",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
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
        assert any("overlap" in error.lower() for error in validation_errors)
        assert "Overlap detected" in requirement
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
                stderr="ERROR: Overlap detected between cells 10 and 11",
                error="ERROR: Overlap detected between cells 10 and 11",
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


def test_plan_graph_does_not_reflect_cross_sections_missing(tmp_path: Path) -> None:
    calls = {"repair": 0, "smoke": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_repair_plan(**kwargs):
        calls["repair"] += 1
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export_xml(model_path: str | Path):
        return ToolResult(name="export_xml", ok=True, returncode=0)

    def fake_plot(run_dir: str | Path):
        return ToolResult(name="run_geometry_plots", ok=True, returncode=0)

    def fake_smoke(run_dir: str | Path, plan: SimulationPlan):
        calls["smoke"] += 1
        return ToolResult(
            name="run_smoke_test",
            ok=False,
            returncode=1,
            stderr="ERROR: No cross_sections.xml was specified",
            error="ERROR: No cross_sections.xml was specified",
        )

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

    assert calls == {"repair": 0, "smoke": 1}
    assert state["validation_report"].is_valid is False
    assert state["validation_report"].issues[0].code == "runtime.cross_sections_missing"
    assert state["validation_report"].issues[0].route_hint == "ask_expert"
    assert state["pending_expert_questions"]
    assert "runtime.cross_sections_missing" in state["pending_expert_questions"][0]


def test_plan_graph_reflects_after_plan_validation_failure(tmp_path: Path) -> None:
    invalid_plan = make_simulation_plan()
    invalid_plan.model_spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )
    calls = {"repair": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=invalid_plan)

    def fake_repair_plan(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationPlan,
        validation_errors: list[str],
    ):
        calls["repair"] += 1
        assert previous_spec is invalid_plan
        assert any("fuel_radius_cm" in error for error in validation_errors)
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
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

    assert calls["repair"] == 1
    assert state["retry_count"] == 1
    assert state["validation_report"].is_valid is True


def test_plan_graph_repairs_malformed_raw_plan_response(tmp_path: Path) -> None:
    calls = {"generate": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        if calls["generate"] == 1:
            return StructuredOutputResult(
                ok=False,
                error="Could not parse model response: response did not contain a JSON object",
                raw_response="not json",
            )
        assert "previous model response could not be parsed" in requirement
        assert "not json" in requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
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

    assert calls["generate"] == 2
    assert state["retry_count"] == 1
    assert state["validation_report"].is_valid is True
    assert state["raw_llm_outputs"] == ["not json"]

    first_meta = tmp_path / "plan_artifacts" / "000_generate_plan" / "meta.json"
    first_raw = tmp_path / "plan_artifacts" / "000_generate_plan" / "raw_response.txt"
    second_validated = tmp_path / "plan_artifacts" / "001_repair_plan_format" / "validated_plan.json"
    final_plan = tmp_path / "simulation_plan.json"
    assert first_raw.read_text(encoding="utf-8") == "not json"
    assert json.loads(first_meta.read_text(encoding="utf-8"))["ok"] is False
    assert not (tmp_path / "plan_artifacts" / "000_generate_plan" / "candidate_plan.json").exists()
    assert second_validated.exists()
    assert final_plan.exists()
    assert json.loads(final_plan.read_text(encoding="utf-8")) == state[
        "simulation_plan"
    ].model_dump(mode="json")


def test_plan_graph_format_repair_guides_truncated_large_patterns(tmp_path: Path) -> None:
    calls = {"generate": 0}
    truncated = '{"complex_model":{"lattices":[{"id":"lat","universe_pattern":[["u"'

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        if calls["generate"] == 1:
            return StructuredOutputResult(
                ok=False,
                error="Could not parse model response: Unterminated string",
                raw_response=truncated,
            )
        assert "appears truncated or too large" in requirement
        assert "set oversized or uncertain universe_pattern/rings to []" in requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        max_retries=1,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个大型堆芯模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls["generate"] == 2
    assert state["validation_report"].is_valid is True


def test_plan_graph_writes_candidate_payload_for_schema_failure(tmp_path: Path) -> None:
    calls = {"generate": 0}
    candidate = {"model_spec": {"name": "bad draft"}}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        if calls["generate"] == 1:
            return StructuredOutputResult(
                ok=False,
                error="Could not validate model response: plot_specs missing",
                raw_response='{"model_spec":{"name":"bad draft"}}',
                candidate_payload=candidate,
                parse_notes=["repaired_missing_json_commas"],
            )
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
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

    candidate_path = tmp_path / "plan_artifacts" / "000_generate_plan" / "candidate_plan.json"
    meta = json.loads(
        (tmp_path / "plan_artifacts" / "000_generate_plan" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    records = load_jsonl_records(tmp_path / "runs.jsonl")

    assert state["validation_report"].is_valid is True
    assert json.loads(candidate_path.read_text(encoding="utf-8")) == candidate
    assert meta["parse_notes"] == ["repaired_missing_json_commas"]
    assert str(candidate_path) in state["plan_artifacts"]
    assert str(candidate_path) in records[0]["plan_artifacts"]


def test_plan_graph_format_repair_uses_candidate_payload_for_missing_cell_fill_id(
    tmp_path: Path,
) -> None:
    calls = {"generate": 0}
    captured = {"repair_requirement": ""}
    candidate = {
        "schema_version": "simulation_plan.v2",
        "model_spec": None,
        "complex_model": {
            "name": "bad assembly draft",
            "kind": "assembly",
            "materials": [{"id": "fuel", "name": "UO2 fuel"}],
            "cells": [
                {
                    "id": "pin_fuel_cell",
                    "name": "pin fuel",
                    "fill_type": "material",
                    "purpose": "fuel cell set per pin universe",
                }
            ],
        },
        "capability_report": {"is_executable": False, "supported_renderer": "none"},
        "plot_specs": [
            {"basis": "xy", "width_cm": [2.0, 2.0], "filename": "assembly_xy.png"}
        ],
    }

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        if calls["generate"] == 1:
            return StructuredOutputResult(
                ok=False,
                error=(
                    "Could not validate model response: 1 validation error for "
                    "SimulationPlan\ncomplex_model.cells.0\n  Value error, "
                    "fill_id is required unless fill_type is void"
                ),
                raw_response=json.dumps(candidate),
                candidate_payload=candidate,
            )
        captured["repair_requirement"] = requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        max_retries=1,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 UO2 assembly 模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    prompt = captured["repair_requirement"]
    assert state["validation_report"].is_valid is True
    assert calls["generate"] == 2
    assert "cell.fill_id.missing" in prompt
    assert "complex_model.cells[0].fill_id" in prompt
    assert "pin_fuel_cell" in prompt
    assert "Parsed candidate JSON is available" in prompt
    assert "Do not invent material density, nuclide composition" in prompt


def test_plan_graph_records_expert_question_when_generation_never_parses(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(
            ok=False,
            error="Could not parse model response: Unterminated string",
            raw_response='{"complex_model":{"lattices":[["u"',
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        max_retries=1,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个大型堆芯模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert state["validation_report"].is_valid is False
    assert state["pending_expert_questions"]
    assert "truncated" in state["pending_expert_questions"][1]


def test_plan_graph_interrupts_for_expert_feedback_and_resumes(tmp_path: Path) -> None:
    calls = {"generate": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        complex_model = ComplexModelSpec(
            name="assembly IR",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    requires_human_confirmation=["density"],
                )
            ],
            cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")],
        )
        return StructuredOutputResult(
            ok=True,
            value=SimulationPlan(
                schema_version="simulation_plan.v2",
                model_spec=None,
                complex_model=complex_model,
                capability_report=RenderCapabilityReport(
                    is_executable=False,
                    supported_renderer="none",
                ),
                plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
            ),
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "expert-loop"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个材料缺密度的组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )

    assert "__interrupt__" in interrupted
    payload = interrupted["__interrupt__"][0].value
    assert payload["kind"] == "expert_feedback_request"
    assert any("material fuel: density" in question for question in payload["questions"])

    state = graph.invoke(
        Command(resume={"expert_feedback": "fuel density is 10.4 g/cm3"}),
        config,
    )

    assert calls["generate"] == 1
    assert state["expert_feedback"] == ["fuel density is 10.4 g/cm3"]
    assert state["human_loop_events"][0]["feedback"] == ["fuel density is 10.4 g/cm3"]
    assert any(event["event"] == "plan_patch_applied" for event in state["human_loop_events"])
    assert state["validation_report"].is_valid is True
    assert Path(state["model_path"]).exists()


def test_plan_graph_does_not_re_ask_confirmations_after_expert_feedback(tmp_path: Path) -> None:
    """Regression: after the expert answers one round, the workflow must NOT interrupt
    again to re-ask the same material confirmations, even when the regenerated plan
    still carries them (real LLMs often fail to strip already-confirmed items).
    Re-asking every round until max_expert_rounds is exhausted is the bug."""
    def plan_with_confirmation() -> SimulationPlan:
        complex_model = ComplexModelSpec(
            name="assembly IR",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    density_value=10.0,
                    density_unit="g/cm3",
                    requires_human_confirmation=["density"],
                )
            ],
            cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")],
        )
        return SimulationPlan(
            schema_version="simulation_plan.v2",
            model_spec=None,
            complex_model=complex_model,
            capability_report=RenderCapabilityReport(
                is_executable=False,
                supported_renderer="none",
            ),
            plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
        )

    def fake_generate_plan(*, requirement: str, schema, model: str):
        # Always return a plan that still carries the 'density' confirmation, simulating
        # a real LLM that did not consume the expert feedback.
        return StructuredOutputResult(ok=True, value=plan_with_confirmation())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "no-re-ask"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        },
        config,
    )
    assert "__interrupt__" in interrupted
    assert any(
        "material fuel: density" in q for q in interrupted["__interrupt__"][0].value["questions"]
    )

    state = graph.invoke(
        Command(resume={"expert_feedback": "fuel density is 10.4 g/cm3"}),
        config,
    )

    # The expert has answered. Even though fake_generate_plan STILL returns a plan with
    # the 'density' confirmation, the second round must NOT re-interrupt to ask it again.
    assert "__interrupt__" not in state
    assert state["expert_feedback"] == ["fuel density is 10.4 g/cm3"]
    assert state["validation_report"].is_valid is True
    assert Path(state["model_path"]).exists()


def test_plan_graph_empty_expert_feedback_continues_without_patch_or_regeneration(tmp_path: Path) -> None:
    calls = {"generate": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        return StructuredOutputResult(
            ok=True,
            value=make_plan_with_fuel_confirmations(["fuel temperature"]),
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "empty-feedback"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个需要确认燃料温度的 pin cell",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )
    assert "__interrupt__" in interrupted

    state = graph.invoke(Command(resume={"expert_feedback": ""}), config)

    assert calls["generate"] == 1
    assert not any(
        event["event"] in {"plan_patch_generated", "expert_feedback_regeneration_selected"}
        for event in state["human_loop_events"]
    )
    assert any(
        item["status"] == "declined" for item in state.get("resolved_expert_items", [])
    )


def test_plan_graph_patches_local_expert_feedback_without_regeneration(tmp_path: Path) -> None:
    calls = {"generate": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        return StructuredOutputResult(ok=True, value=make_complex_plan_for_temperature_patch())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "local-patch"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个燃料温度待确认的组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )
    assert "__interrupt__" in interrupted

    state = graph.invoke(
        Command(resume={"expert_feedback": "燃料温度 900K，边界条件 reflective。"}),
        config,
    )

    plan = state["simulation_plan"]
    assert calls["generate"] == 1
    assert plan.complex_model.materials[0].temperature_k == 900
    assert plan.complex_model.assemblies[0].boundary == "reflective"
    assert plan.complex_model.materials[0].requires_human_confirmation == []
    assert any(event["event"] == "plan_patch_applied" for event in state["human_loop_events"])
    assert not any(
        event["event"] == "expert_feedback_regeneration_selected"
        for event in state["human_loop_events"]
    )


def test_plan_graph_regenerates_for_requirement_level_expert_feedback(tmp_path: Path) -> None:
    calls = {"generate": 0}
    captured_requirements: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        captured_requirements.append(requirement)
        if calls["generate"] == 1:
            return StructuredOutputResult(
                ok=True,
                value=make_plan_with_fuel_confirmations(["fuel temperature"]),
            )
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "regenerate-feedback"}}

    graph.invoke(
        {
            "requirement": "建立一个 pin cell",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )
    state = graph.invoke(
        Command(
            resume={
                "expert_feedback": "This should not be a pin cell. Rebuild it as a C5G7 full-core benchmark."
            }
        ),
        config,
    )

    assert calls["generate"] == 2
    assert any(
        event["event"] == "expert_feedback_regeneration_selected"
        for event in state["human_loop_events"]
    )
    assert "Expert feedback consumption rules" in captured_requirements[-1]
    assert "Do not ask the same expert question again" in captured_requirements[-1]


def test_plan_graph_ambiguous_feedback_does_not_apply_unsafe_patch(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(
            ok=True,
            value=make_plan_with_fuel_confirmations(["fuel temperature"]),
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "manual-review-feedback"}}

    graph.invoke(
        {
            "requirement": "建立一个 pin cell",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )
    state = graph.invoke(
        Command(resume={"expert_feedback": "Use the more realistic thing we discussed earlier."}),
        config,
    )

    classified = [
        event for event in state["human_loop_events"] if event["event"] == "expert_feedback_classified"
    ]
    assert classified[-1]["action"] == "manual_review"
    assert not any(event["event"] == "plan_patch_applied" for event in state["human_loop_events"])


def test_plan_graph_filters_same_confirmations_even_when_regenerated_plan_keeps_them(
    tmp_path: Path,
) -> None:
    calls = {"generate": 0}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        calls["generate"] += 1
        return StructuredOutputResult(
            ok=True,
            value=make_plan_with_fuel_confirmations(["fuel temperature", "UO2 enrichment"]),
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "regenerated-still-dirty"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个需要确认燃料参数的 pin cell",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        },
        config,
    )
    assert "__interrupt__" in interrupted

    state = graph.invoke(
        Command(
            resume={
                "expert_feedback": (
                    "fuel temperature = 900 K; UO2 enrichment use benchmark value. "
                    "Rebuild the plan if needed."
                )
            }
        ),
        config,
    )

    assert calls["generate"] == 2
    assert "__interrupt__" not in state
    assert any(
        event["event"] == "expert_question_filtered_as_resolved"
        for event in state["human_loop_events"]
    )


def test_plan_graph_resolves_confirmed_assumption_without_reasking(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        plan = make_simulation_plan()
        return StructuredOutputResult(
            ok=True,
            value=plan.model_copy(
                update={
                    "expert_assumptions": [
                        "Assume benchmark UO2 enrichment is acceptable."
                    ]
                }
            ),
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        enable_plots=False,
        enable_smoke_test=False,
    )
    config = {"configurable": {"thread_id": "assumption-feedback"}}

    interrupted = graph.invoke(
        {
            "requirement": "建立一个 UO2 pin-cell 临界计算",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 1,
        },
        config,
    )
    assert "__interrupt__" in interrupted

    state = graph.invoke(
        Command(resume={"expert_feedback": "可以，按 benchmark UO2 enrichment。"}),
        config,
    )

    assert state["simulation_plan"].expert_assumptions == []
    assert "__interrupt__" not in state


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


def test_plan_graph_augmented_requirement_keeps_policy_out_of_user_context(tmp_path: Path) -> None:
    from openmc_agent.prompts import SIMULATION_PLAN_SYSTEM_PROMPT

    captured: dict = {}

    def fake_generate_plan(*, requirement: str, schema, model: str):
        captured["requirement"] = requirement
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
    )

    graph.invoke(
        {
            "requirement": "建立一个 17x17 组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    requirement = captured["requirement"]
    assert "OpenMC API context" in requirement
    assert "Few-shot" in requirement
    assert "Capability report consistency rule" not in requirement
    assert "non-executable complex-only plan" in SIMULATION_PLAN_SYSTEM_PROMPT
    assert "capability_report.supported_renderer='none'" in SIMULATION_PLAN_SYSTEM_PROMPT


def test_plan_graph_normalizes_inconsistent_capability_report_from_llm(tmp_path: Path) -> None:
    """Acceptance: an LLM draft that is non-executable but claims a concrete
    renderer must not collapse the plan to null. The normalizer relaxes the
    capability_report, validation passes, and the local assessor has the final
    word (here: non-executable assembly IR with missing material data)."""
    import json as _json
    from types import SimpleNamespace

    from openmc_agent.llm import generate_structured_output, normalize_capability_report

    inconsistent_payload = _json.dumps(
        {
            "schema_version": "simulation_plan.v2",
            "model_spec": None,
            "complex_model": {
                "name": "assembly IR",
                "kind": "assembly",
                "materials": [
                    {"id": "fuel", "name": "fuel", "requires_human_confirmation": ["density"]}
                ],
            },
            "capability_report": {
                "is_executable": False,
                "supported_renderer": "assembly",
                "executable_subsystems": ["assemblies"],
            },
            "plot_specs": [
                {"basis": "xy", "width_cm": [2.0, 2.0], "filename": "assembly_xy.png"}
            ],
        }
    )

    class _FakePlanClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=self)

        def create(self, *, model: str, messages: list, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=inconsistent_payload))]
            )

    def fake_generate_plan(*, requirement: str, schema, model: str):
        # Mirror the production default: generate_structured_output with the
        # capability_report normalizer bound.
        return generate_structured_output(
            requirement=requirement,
            schema=schema,
            model=model,
            client=_FakePlanClient(),
            normalizer=normalize_capability_report,
        )

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
    )

    state = graph.invoke(
        {
            "requirement": "建立一个组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert state["simulation_plan"] is not None
    assert state["validation_report"].is_valid is True
    capability = state["simulation_plan"].capability_report
    # The local assessor reconciled the report: the LLM's inconsistent draft
    # (is_executable=false with a concrete renderer) is gone, and the incomplete
    # assembly IR stays non-executable. Whether the renderer system emits a
    # review-only skeleton or stops is its concern; the key invariant here is
    # that the plan did not collapse to null.
    assert capability.is_executable is False
    assert capability.renderability in {"none", "skeleton"}


def test_plan_graph_renders_skeleton_for_incomplete_assembly_ir(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        assert "OpenMC API context" in requirement
        assert "Few-shot" in requirement
        assert schema is SimulationPlan
        complex_model = ComplexModelSpec(
            name="assembly IR",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    requires_human_confirmation=["density", "enrichment"],
                )
            ],
            cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")],
        )
        return StructuredOutputResult(
            ok=True,
            value=SimulationPlan(
                schema_version="simulation_plan.v2",
                model_spec=None,
                complex_model=complex_model,
                capability_report=RenderCapabilityReport(
                    is_executable=False,
                    supported_renderer="none",
                ),
                plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
            ),
        )

    def fake_export_xml(model_path: str | Path):
        calls.append("export_xml")
        return ToolResult(name="export_xml", ok=True)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export_xml,
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        retrieve_docs=lambda requirement: [{"symbol": "openmc.RectLattice", "signature": "()"}],
        select_examples=lambda requirement: [{"name": "rectangular_assembly_lattice"}],
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 17x17 组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    # Skeleton mode: model.py is produced for review, but no export/run happens.
    assert calls == []
    assert state["validation_report"].is_valid is True
    capability = state["simulation_plan"].capability_report
    assert capability.is_executable is False
    assert capability.renderability == "skeleton"
    assert capability.supported_renderer == "assembly"
    model_path = Path(state["model_path"])
    assert model_path.exists()
    model_text = model_path.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in model_text
    assert "TODO" in model_text
    # export_to_xml must never be actually called; any mention is in comments only.
    export_lines = [
        line for line in model_text.splitlines() if "export_to_xml()" in line
    ]
    assert export_lines, "skeleton should explain why export is omitted"
    assert all(line.lstrip().startswith("#") for line in export_lines)
    assert (tmp_path / "capability_report.json").exists()
    assert (tmp_path / "TODO.md").exists()
    assert state["openmc_api_docs"][0]["symbol"] == "openmc.RectLattice"


def test_plan_graph_renders_rectangular_assembly_ir(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        complex_model = ComplexModelSpec(
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
            cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                )
            ],
            assemblies=[AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")],
        )
        return StructuredOutputResult(
            ok=True,
            value=SimulationPlan(
                schema_version="simulation_plan.v2",
                model_spec=None,
                complex_model=complex_model,
                capability_report=RenderCapabilityReport(
                    is_executable=False,
                    supported_renderer="none",
                ),
                plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="assembly_xy.png")],
            ),
        )

    def fake_export_xml(model_path: str | Path):
        calls.append("export_xml")
        assert "openmc.RectLattice" in Path(model_path).read_text(encoding="utf-8")
        return ToolResult(name="export_xml", ok=True, returncode=0)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export_xml,
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        enable_plots=False,
        enable_smoke_test=False,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个 2x2 组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
        }
    )

    assert calls == ["export_xml"]
    assert state["validation_report"].is_valid is True
    assert state["simulation_plan"].capability_report.supported_renderer == "assembly"
    assert Path(state["model_path"]).exists()


def test_render_plan_script_uses_actual_render_result_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_simulation_plan()
    optimistic = RenderCapabilityReport(
        renderability="runnable",
        is_executable=True,
        supported_renderer="pin_cell",
    )
    downgraded = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="pin_cell",
        reasons=["renderer validation failed"],
    )

    class FakeRenderer:
        name = "pin_cell"

        def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
            model_path = outdir / "model.py"
            script = "# NOT EXECUTABLE\n# export_to_xml() intentionally omitted\n"
            model_path.write_text(script, encoding="utf-8")
            return RenderResult(
                renderer_name="pin_cell",
                renderability="skeleton",
                is_executable=False,
                script=script,
                output_files=[str(model_path)],
                capability=downgraded,
            )

    def fake_choose_renderer(plan: SimulationPlan):
        return FakeRenderer(), optimistic

    monkeypatch.setattr("openmc_agent.graph.choose_renderer", fake_choose_renderer)

    state = _render_plan_script(
        {
            "simulation_plan": plan.model_copy(update={"capability_report": optimistic}),
            "validation_report": ValidationReport(is_valid=True),
            "output_dir": str(tmp_path),
        }
    )

    assert state["simulation_plan"].capability_report.renderability == "skeleton"
    sidecar = json.loads((tmp_path / "capability_report.json").read_text(encoding="utf-8"))
    final_plan = json.loads((tmp_path / "simulation_plan.json").read_text(encoding="utf-8"))
    assert sidecar["renderability"] == "skeleton"
    assert sidecar["is_executable"] is False
    assert final_plan["capability_report"]["renderability"] == "skeleton"
    assert final_plan == state["simulation_plan"].model_dump(mode="json")


def test_structural_renderability_gap_survives_material_expert_feedback() -> None:
    """Regression for the over-correction of the re-ask bug.

    A structural capability gap (a universe referencing a missing cell, or a
    pin-count mismatch) is a plan-internal defect that a material-level expert
    answer can never resolve. Earlier, the resolved-question filter extracted
    no semantic keys from these gap messages, so ``len(空 & item_keys) >=
    min(2, 0)`` evaluated ``0 >= 0`` and matched them against the prior material
    answer -- silently dropping the gap so the expert was never re-interrupted
    and the model stayed stuck at skeleton. Only confirmation/assumption items
    may be semantically de-duplicated against prior feedback; capability gaps
    must survive until the plan is actually fixed.
    """
    from openmc_agent.graph import (
        _resolved_match_for_question,
        _update_resolved_expert_items_from_feedback,
    )
    from openmc_agent.schemas import ResolvedExpertItem

    material_q = "Please provide or confirm: material fuel: density"
    missing_cell_q = (
        "What expert information resolves this renderability gap: "
        "universe 'water_univ' references missing cells: ['water_cell']"
    )
    pincount_q = (
        "What expert information resolves this renderability gap: "
        "lattice mox_lat: pin count mismatch vs expected_counts"
    )

    # Round 1: the expert answered only the material question.
    items_payload = _update_resolved_expert_items_from_feedback(
        state={"resolved_expert_items": []},
        questions=[material_q, missing_cell_q, pincount_q],
        feedback_items=["fuel density 10.0 g/cm3, UO2 enrichment 3.3 wt%"],
        round_index=1,
    )
    resolved = [ResolvedExpertItem.model_validate(item) for item in items_payload]

    # The material question is resolved; the structural gaps are NOT (a material
    # answer cannot fix a missing-cell reference or a pin-count error).
    structural_items = [item for item in resolved if item.kind == "capability_reason"]
    assert structural_items, "capability reasons must be recorded as resolved candidates"
    assert all(item.status != "resolved" for item in structural_items)

    # Round 2 filtering behaviour:
    assert _resolved_match_for_question(material_q, resolved) is not None  # de-duped
    assert _resolved_match_for_question(missing_cell_q, resolved) is None  # re-ask
    assert _resolved_match_for_question(pincount_q, resolved) is None  # re-ask


def test_capability_self_repair_classifies_structural_vs_material_gaps() -> None:
    """Structural defects (missing-cell refs, pin-count, bad radius) are LLM
    typos and route to reflect_plan; material gaps (density/composition) are
    expert facts and must NOT be classified self-repairable."""
    from openmc_agent.graph import _capability_self_repair_errors

    structural = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="core",
        reasons=[
            "universe 'water_univ' references missing cells: ['water_cell']",
            "universe 'uo2_assembly' references missing cells: ['uo2_assembly_cell']",
        ],
        required_human_confirmations=[
            "lattice mox_lat: pin count mismatch vs expected_counts: mox43: expected 64, got 62",
        ],
    )
    repaired = _capability_self_repair_errors(structural)
    # Legacy fallback path (no structured issues): returns ValidationIssue whose
    # message mirrors the old free-text reasons/confirmations.
    repaired_messages = [r.message for r in repaired]
    assert any("references missing cells" in m for m in repaired_messages)
    assert any("pin count mismatch" in m for m in repaired_messages)
    assert all(r.code == "legacy.self_repairable" for r in repaired)

    # Structured path: issues filtered by SELF_REPAIRABLE_CODES win over regex;
    # material gaps stay out (expert fact).
    structured_report = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="core",
        reasons=["universe 'water_univ' references missing cells"],
        issues=[
            ValidationIssue(
                severity="error",
                code="universe.cell_ref_missing",
                message="universe 'water_univ' references missing cells",
            ),
            ValidationIssue(
                severity="error",
                code="material.missing_density",
                message="material 'fuel' is missing density",
            ),
        ],
    )
    structured = _capability_self_repair_errors(structured_report)
    structured_codes = {i.code for i in structured}
    assert "universe.cell_ref_missing" in structured_codes
    assert "material.missing_density" not in structured_codes

    material = RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="assembly",
        reasons=["material 'fuel' is missing density"],
        required_human_confirmations=[
            "material fuel: is missing composition or chemical_formula"
        ],
    )
    # Material gaps are expert facts, not LLM typos.
    assert _capability_self_repair_errors(material) == []

    clean = RenderCapabilityReport(renderability="exportable", supported_renderer="assembly")
    assert _capability_self_repair_errors(clean) == []


def test_plan_graph_reflects_on_structural_capability_errors(tmp_path: Path) -> None:
    """A structural plan defect (a universe referencing a missing cell) must be
    fixed by the LLM via reflect_plan, not presented to the expert. The expert
    can only supply material values; an id typo is the LLM's to fix."""
    reflect_calls = {"count": 0}

    def dirty_plan() -> SimulationPlan:
        complex_model = ComplexModelSpec(
            name="assembly IR",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    density_value=10.0,
                    density_unit="g/cm3",
                )
            ],
            cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
            universes=[
                UniverseSpec(id="pin", name="pin", cell_ids=["missing_cell"])  # bad ref
            ],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[
                AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")
            ],
        )
        return SimulationPlan(
            schema_version="simulation_plan.v2",
            model_spec=None,
            complex_model=complex_model,
            capability_report=RenderCapabilityReport(
                is_executable=False,
                supported_renderer="none",
            ),
            plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
        )

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=dirty_plan())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        reflect_calls["count"] += 1
        fixed = previous_spec.model_copy(deep=True)
        # Fix the bad cell reference that the renderer flagged.
        fixed.complex_model.universes[0].cell_ids = ["fuel_cell"]
        return StructuredOutputResult(ok=True, value=fixed)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        enable_plots=False,
        enable_smoke_test=False,
    )

    state = graph.invoke(
        {
            "requirement": "建立一个组件模型",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )

    # reflect_plan fixed the missing-cell reference.
    assert reflect_calls["count"] >= 1
    # A structural typo must never interrupt for expert feedback.
    assert "__interrupt__" not in state
    # The renderer accepted the fixed plan and produced model.py.
    assert state["validation_report"].is_valid is True
    assert Path(state["model_path"]).exists()


def _make_dirty_assembly_plan_missing_cell() -> SimulationPlan:
    """An assembly IR whose only defect is a universe pointing at a missing cell.

    The schema accepts this (reference consistency is checked at the capability
    layer), so the plan reaches assess_capability, which routes it to
    reflect_plan -- the entry point for the investigation patch path.
    """
    complex_model = ComplexModelSpec(
        name="assembly IR",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel",
                chemical_formula="UO2",
                density_value=10.0,
                density_unit="g/cm3",
            )
        ],
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["missing_cell"])],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="assembly lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin"]],
            )
        ],
        assemblies=[AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice")],
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=complex_model,
        capability_report=RenderCapabilityReport(
            is_executable=False,
            supported_renderer="none",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly_xy.png")],
    )


def _investigation_done(patch=None, findings=""):
    return StructuredOutputResult(
        ok=True,
        value=RetrievalStep(
            action="done",
            reasoning="investigation complete",
            findings=findings,
            patch=patch,
        ),
    )


def _investigation_graph(
    tmp_path: Path,
    *,
    generate_plan,
    repair_plan,
    investigation_llm,
):
    return build_plan_graph(
        generate_plan=generate_plan,
        repair_plan=repair_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        investigation_llm=investigation_llm,
        enable_plots=False,
        enable_smoke_test=False,
    )


def _fix_missing_cell(previous_spec: SimulationPlan) -> SimulationPlan:
    fixed = previous_spec.model_copy(deep=True)
    fixed.complex_model.universes[0].cell_ids = ["fuel_cell"]
    return fixed


def test_plan_graph_reflect_applies_auto_repair_patch(tmp_path: Path) -> None:
    """A uniquely-solvable id typo (cell id 'fuel_cel' -> 'fuel_cell') is fixed
    by deterministic auto_repair with no LLM call: repair_plan is not invoked,
    patch_reason='deterministic auto-repair', patch_confidence='high'."""
    repair_calls = {"n": 0}
    reflect_investigation_calls = {"n": 0}

    def dirty_plan() -> SimulationPlan:
        plan = _make_dirty_assembly_plan_missing_cell()
        # Typo that resolves uniquely to 'fuel_cell' (prefix match, edit distance 1).
        plan.complex_model.universes[0].cell_ids = ["fuel_cel"]
        return plan

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=dirty_plan())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        repair_calls["n"] += 1
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    def fake_investigation(prompt: str):
        if "reflect problem" in prompt:
            reflect_investigation_calls["n"] += 1
        # Investigation offers no patch; auto_repair must handle it alone.
        return _investigation_done(patch=None, findings="")

    graph = _investigation_graph(
        tmp_path,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        investigation_llm=fake_investigation,
    )
    state = graph.invoke(
        {
            "requirement": "fix the assembly typo",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    # auto_repair fixed it deterministically; no whole-plan regeneration.
    assert repair_calls["n"] == 0
    assert reflect_investigation_calls["n"] == 0
    plan_patch = state.get("plan_patch")
    assert plan_patch
    assert any(
        op["path"] == "/complex_model/universes/0/cell_ids/0" for op in plan_patch
    )
    assert plan_patch[0]["value"] == "fuel_cell"
    assert state.get("patch_reason") == "deterministic auto-repair"
    assert state.get("patch_confidence") == "high"
    assert state.get("patch_failure_count", 0) == 0
    assert state["validation_report"].is_valid is True


def test_plan_graph_reflect_applies_investigation_patch(tmp_path: Path, monkeypatch) -> None:
    """When the investigation produces a valid JSON Patch, reflect_plan applies
    it surgically and never calls repair_plan to regenerate the whole plan."""
    repair_calls = {"n": 0}

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=_make_dirty_assembly_plan_missing_cell())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        repair_calls["n"] += 1
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    patch = [
        {"op": "replace", "path": "/complex_model/universes/0/cell_ids", "value": ["fuel_cell"]}
    ]

    prompts: list[str] = []

    from openmc_agent.grep_search import RetrievedEvidence

    monkeypatch.setattr(
        "openmc_agent.graph.gather_grep_evidence_for_issues",
        lambda issues: [
            RetrievedEvidence(
                source_type="grep",
                locator="openmc_agent/schemas.py:100-103",
                text="100: class UniverseSpec\n101:     cell_ids: list[str]",
                issue_code=issues[0].code,
                schema_path=issues[0].schema_path,
                metadata={"matched_pattern": "cell_ids"},
            )
        ],
    )

    def fake_investigation(prompt: str):
        prompts.append(prompt)
        if "reflect problem" in prompt:
            return _investigation_done(patch=patch, findings="fixed missing cell reference")
        return _investigation_done()

    graph = _investigation_graph(
        tmp_path,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        investigation_llm=fake_investigation,
    )
    state = graph.invoke(
        {
            "requirement": "fix the assembly",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    assert repair_calls["n"] == 0
    assert state.get("plan_patch") == patch
    assert state.get("investigation_trace")
    assert state.get("grep_evidence")
    assert any("[Grep Evidence]" in prompt for prompt in prompts)
    assert any("Validation Issues" in prompt or "Structured diagnostic issues" in prompt for prompt in prompts)
    assert state["validation_report"].is_valid is True


def test_plan_graph_reflect_falls_back_when_patch_null(tmp_path: Path) -> None:
    """A null patch (defect too large) falls back to repair_plan, enriched by findings."""
    repair_calls = {"n": 0}

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=_make_dirty_assembly_plan_missing_cell())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        repair_calls["n"] += 1
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    def fake_investigation(prompt: str):
        if "reflect problem" in prompt:
            return _investigation_done(patch=None, findings="defect too large for a surgical patch")
        return _investigation_done()

    graph = _investigation_graph(
        tmp_path,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        investigation_llm=fake_investigation,
    )
    state = graph.invoke(
        {
            "requirement": "fix the assembly",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    assert repair_calls["n"] >= 1
    assert state.get("investigation_trace")


def test_plan_graph_reflect_falls_back_when_patch_invalid(tmp_path: Path) -> None:
    """A patch that targets a non-existent path must fail validation and fall
    back to repair_plan instead of corrupting the plan."""
    repair_calls = {"n": 0}

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=_make_dirty_assembly_plan_missing_cell())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        repair_calls["n"] += 1
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    bad_patch = [
        {"op": "replace", "path": "/complex_model/universes/999/cell_ids", "value": ["fuel_cell"]}
    ]

    def fake_investigation(prompt: str):
        if "reflect problem" in prompt:
            return _investigation_done(patch=bad_patch, findings="trying an invalid path")
        return _investigation_done()

    graph = _investigation_graph(
        tmp_path,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        investigation_llm=fake_investigation,
    )
    graph.invoke(
        {
            "requirement": "fix the assembly",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    assert repair_calls["n"] >= 1


def test_plan_graph_generate_uses_investigation_findings(tmp_path: Path) -> None:
    """generate_plan must receive the investigation findings in its requirement."""
    captured = {"requirement": ""}

    def fake_generate_plan(*, requirement, schema, model):
        captured["requirement"] = requirement
        return StructuredOutputResult(ok=True, value=_make_dirty_assembly_plan_missing_cell())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    marker = "SPECIAL_FINDING_MARKER_XYZ"

    def fake_investigation(prompt: str):
        return _investigation_done(findings=marker)

    graph = _investigation_graph(
        tmp_path,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        investigation_llm=fake_investigation,
    )
    graph.invoke(
        {
            "requirement": "build an assembly",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    assert marker in captured["requirement"]


def test_plan_graph_skips_investigation_when_llm_none(tmp_path: Path) -> None:
    """With no investigation_llm injected, behavior is unchanged and no
    investigation_trace is written."""
    repair_calls = {"n": 0}

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=_make_dirty_assembly_plan_missing_cell())

    def fake_repair_plan(*, requirement, schema, model, previous_spec, validation_errors):
        repair_calls["n"] += 1
        return StructuredOutputResult(ok=True, value=_fix_missing_cell(previous_spec))

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        enable_plots=False,
        enable_smoke_test=False,
    )
    state = graph.invoke(
        {
            "requirement": "fix the assembly",
            "model": "test:model",
            "output_dir": str(tmp_path),
            "records_path": str(tmp_path / "runs.jsonl"),
            "max_expert_rounds": 2,
        }
    )
    assert repair_calls["n"] >= 1
    assert not state.get("investigation_trace")
