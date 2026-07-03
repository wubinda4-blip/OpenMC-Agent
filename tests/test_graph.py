from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from openmc_agent.graph import build_graph, build_plan_graph
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.records import load_jsonl_records
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
        if "fuel density is 10.4 g/cm3" in requirement:
            return StructuredOutputResult(ok=True, value=make_simulation_plan())
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

    assert calls["generate"] == 2
    assert state["expert_feedback"] == ["fuel density is 10.4 g/cm3"]
    assert state["human_loop_events"][0]["feedback"] == ["fuel density is 10.4 g/cm3"]
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
