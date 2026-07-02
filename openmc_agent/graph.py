from pathlib import Path
import sys
from typing import Any, Callable, TypedDict

import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from openmc_agent.executor import render_openmc_plan_script, render_openmc_script
from openmc_agent.llm import (
    StructuredOutputResult,
    generate_structured_output,
    repair_structured_output,
)
from openmc_agent.records import append_simulation_record
from openmc_agent.schemas import SimulationPlan, SimulationSpec, ValidationReport
from openmc_agent.tools import (
    ToolResult,
    export_xml,
    parse_openmc_output,
    run_geometry_plots,
    run_smoke_test,
)
from openmc_agent.validator import validate_openmc_script, validate_simulation_spec


GenerateSpecFn = Callable[..., StructuredOutputResult[SimulationSpec]]
RepairSpecFn = Callable[..., StructuredOutputResult[SimulationSpec]]
GeneratePlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
RepairPlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
ExportXmlToolFn = Callable[[str | Path], ToolResult]
PlotToolFn = Callable[[str | Path], ToolResult]
SmokeTestToolFn = Callable[[str | Path, SimulationPlan], ToolResult]


class GraphState(TypedDict, total=False):
    requirement: str
    model: str
    output_dir: str
    records_path: str
    simulation_spec: SimulationSpec | None
    validation_report: ValidationReport
    script: str
    model_path: str
    error: str
    retry_count: int
    retry_history: list[dict[str, Any]]
    simulation_plan: SimulationPlan | None
    tool_results: list[dict[str, Any]]
    expert_feedback: list[str]
    raw_llm_outputs: list[str]
    verbose: bool


def build_graph(
    generate_spec: GenerateSpecFn = generate_structured_output,
    repair_spec: RepairSpecFn = repair_structured_output,
    max_retries: int = 3,
    checkpoint_path: str | Path | None = None,
    checkpointer: Any | None = None,
):
    if checkpoint_path is not None and checkpointer is not None:
        raise ValueError("Use either checkpoint_path or checkpointer, not both")
    if checkpoint_path is not None:
        checkpointer = _build_sqlite_checkpointer(checkpoint_path)

    graph = StateGraph(GraphState)
    graph.add_node("receive_requirement", _receive_requirement)
    graph.add_node("generate_spec", _make_generate_spec_node(generate_spec))
    graph.add_node("validate_spec", _make_validate_spec_node(max_retries))
    graph.add_node("repair_spec", _make_repair_spec_node(repair_spec, max_retries))
    graph.add_node("render_script", _render_script)
    graph.add_node("save_record", _save_record)

    graph.add_edge(START, "receive_requirement")
    graph.add_edge("receive_requirement", "generate_spec")
    graph.add_edge("generate_spec", "validate_spec")
    graph.add_conditional_edges(
        "validate_spec",
        _make_validation_router(max_retries),
        {
            "render": "render_script",
            "repair": "repair_spec",
            "stop": "save_record",
        },
    )
    graph.add_edge("repair_spec", "validate_spec")
    graph.add_edge("render_script", "save_record")
    graph.add_edge("save_record", END)
    return graph.compile(checkpointer=checkpointer)


def build_plan_graph(
    generate_plan: GeneratePlanFn = generate_structured_output,
    repair_plan: RepairPlanFn = repair_structured_output,
    *,
    export_xml_tool: ExportXmlToolFn = export_xml,
    plot_tool: PlotToolFn = run_geometry_plots,
    smoke_test_tool: SmokeTestToolFn = run_smoke_test,
    enable_plots: bool = True,
    enable_smoke_test: bool = True,
    max_retries: int = 3,
    checkpoint_path: str | Path | None = None,
    checkpointer: Any | None = None,
):
    if checkpoint_path is not None and checkpointer is not None:
        raise ValueError("Use either checkpoint_path or checkpointer, not both")
    if checkpoint_path is not None:
        checkpointer = _build_sqlite_checkpointer(checkpoint_path)

    graph = StateGraph(GraphState)
    graph.add_node("receive_requirement", _receive_requirement)
    graph.add_node("generate_plan", _make_generate_plan_node(generate_plan))
    graph.add_node("validate_plan", _make_validate_plan_node(max_retries))
    graph.add_node("render_plan_script", _render_plan_script)
    graph.add_node(
        "execute_tools",
        _make_execute_tools_node(
            export_xml_tool=export_xml_tool,
            plot_tool=plot_tool,
            smoke_test_tool=smoke_test_tool,
            enable_plots=enable_plots,
            enable_smoke_test=enable_smoke_test,
        ),
    )
    graph.add_node("reflect_plan", _make_reflect_plan_node(repair_plan))
    graph.add_node("save_record", _save_plan_record)

    graph.add_edge(START, "receive_requirement")
    graph.add_edge("receive_requirement", "generate_plan")
    graph.add_edge("generate_plan", "validate_plan")
    graph.add_conditional_edges(
        "validate_plan",
        _make_plan_validation_router(),
        {
            "render": "render_plan_script",
            "stop": "save_record",
        },
    )
    graph.add_edge("render_plan_script", "execute_tools")
    graph.add_conditional_edges(
        "execute_tools",
        _make_plan_execution_router(max_retries),
        {
            "reflect": "reflect_plan",
            "save": "save_record",
        },
    )
    graph.add_edge("reflect_plan", "validate_plan")
    graph.add_edge("save_record", END)
    return graph.compile(checkpointer=checkpointer)


def _build_sqlite_checkpointer(path: str | Path) -> SqliteSaver:
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def _receive_requirement(state: GraphState) -> GraphState:
    _progress(state, "receive_requirement", "reading and normalizing user requirement")
    requirement = state.get("requirement", "").strip()
    if not requirement:
        _progress(state, "receive_requirement", "failed: requirement is empty")
        return {"error": "requirement is required"}
    _progress(state, "receive_requirement", f"received {len(requirement)} characters")
    return {"requirement": requirement}


def _make_generate_spec_node(generate_spec: GenerateSpecFn):
    def _generate_spec(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}

        model = state.get("model", "openai:gpt-4o")
        _progress(state, "generate_spec", f"calling LLM model={model}")
        result = generate_spec(
            requirement=state["requirement"],
            schema=SimulationSpec,
            model=model,
        )
        if not result.ok or result.value is None:
            _progress(state, "generate_spec", f"failed: {result.error}")
            return {
                "simulation_spec": None,
                "error": result.error or "failed to generate SimulationSpec",
            }
        _progress(state, "generate_spec", f"generated SimulationSpec name={result.value.name!r}")
        return {"simulation_spec": result.value}

    return _generate_spec


def _make_generate_plan_node(generate_plan: GeneratePlanFn):
    def _generate_plan(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}

        model = state.get("model", "openai:gpt-4o")
        _progress(state, "generate_plan", f"calling LLM model={model}")
        result = generate_plan(
            requirement=_requirement_with_expert_feedback(state),
            schema=SimulationPlan,
            model=model,
        )
        if not result.ok or result.value is None:
            _progress(state, "generate_plan", f"failed: {result.error}")
            return {
                "simulation_plan": None,
                "simulation_spec": None,
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "error": result.error or "failed to generate SimulationPlan",
            }
        _progress(
            state,
            "generate_plan",
            (
                f"generated SimulationPlan name={result.value.model_spec.name!r}, "
                f"plots={len(result.value.plot_specs)}, "
                f"smoke_test={result.value.execution_check.enabled}"
            ),
        )
        return {
            "simulation_plan": result.value,
            "simulation_spec": result.value.model_spec,
            "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
        }

    return _generate_plan


def _make_repair_spec_node(repair_spec: RepairSpecFn, max_retries: int):
    def _repair_spec(state: GraphState) -> GraphState:
        report = state.get("validation_report")
        spec = state.get("simulation_spec")
        retry_count = state.get("retry_count", 0)
        if spec is None or report is None or report.is_valid or retry_count >= max_retries:
            return {"retry_count": retry_count}

        _progress(state, "repair_spec", f"calling LLM repair retry={retry_count + 1}/{max_retries}")
        result = repair_spec(
            requirement=state["requirement"],
            schema=SimulationSpec,
            model=state.get("model", "openai:gpt-4o"),
            previous_spec=spec,
            validation_errors=report.errors,
        )
        if not result.ok or result.value is None:
            _progress(state, "repair_spec", f"failed: {result.error}")
            return {
                "retry_count": retry_count + 1,
                "error": result.error or "failed to repair SimulationSpec",
            }
        _progress(state, "repair_spec", "repair produced a new SimulationSpec")
        return {
            "simulation_spec": result.value,
            "retry_count": retry_count + 1,
            "error": "",
        }

    return _repair_spec


def _make_validate_spec_node(max_retries: int):
    def _validate_spec(state: GraphState) -> GraphState:
        _progress(state, "validate_spec", "validating SimulationSpec")
        spec = state.get("simulation_spec")
        retry_count = state.get("retry_count", 0)
        if spec is None:
            report = ValidationReport(
                is_valid=False,
                errors=[state.get("error", "SimulationSpec is missing")],
            )
        else:
            report = validate_simulation_spec(spec)

        history = list(state.get("retry_history", []))
        history.append(
            {
                "requirement": state.get("requirement", ""),
                "retry_count": retry_count,
                "spec": spec.model_dump(mode="json") if spec is not None else None,
                "validation_errors": report.errors,
                "fix_suggestion": (
                    "Ask the model to repair the spec using the validation errors."
                    if not report.is_valid and retry_count < max_retries
                    else ""
                ),
            }
        )

        if not report.is_valid:
            _progress(state, "validate_spec", f"failed with {len(report.errors)} error(s)")
            return {
                "validation_report": report,
                "retry_history": history,
                "error": "; ".join(report.errors),
            }
        _progress(state, "validate_spec", "passed")
        return {
            "validation_report": report,
            "retry_history": history,
            "error": "",
        }

    return _validate_spec


def _make_validate_plan_node(max_retries: int):
    def _validate_plan(state: GraphState) -> GraphState:
        _progress(state, "validate_plan", "validating SimulationPlan.model_spec")
        plan = state.get("simulation_plan")
        retry_count = state.get("retry_count", 0)
        if plan is None:
            report = ValidationReport(
                is_valid=False,
                errors=[state.get("error", "SimulationPlan is missing")],
            )
        else:
            report = validate_simulation_spec(plan.model_spec)

        history = list(state.get("retry_history", []))
        history.append(
            {
                "requirement": state.get("requirement", ""),
                "retry_count": retry_count,
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "validation_errors": report.errors,
                "fix_suggestion": (
                    "Ask the model to repair the plan using validation and execution errors."
                    if not report.is_valid and retry_count < max_retries
                    else ""
                ),
            }
        )

        if not report.is_valid:
            _progress(state, "validate_plan", f"failed with {len(report.errors)} error(s)")
            return {
                "validation_report": report,
                "retry_history": history,
                "error": "; ".join(report.errors),
            }
        _progress(state, "validate_plan", "passed")
        return {
            "validation_report": report,
            "retry_history": history,
            "simulation_spec": plan.model_spec if plan is not None else None,
            "error": "",
        }

    return _validate_plan


def _make_validation_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid:
            return "render"
        if state.get("simulation_spec") is not None and state.get("retry_count", 0) < max_retries:
            return "repair"
        return "stop"

    return _route


def _make_plan_validation_router():
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid and state.get("simulation_plan") is not None:
            return "render"
        return "stop"

    return _route


def _make_plan_execution_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid:
            return "save"
        if state.get("simulation_plan") is not None and state.get("retry_count", 0) < max_retries:
            return "reflect"
        return "save"

    return _route


def _render_script(state: GraphState) -> GraphState:
    _progress(state, "render_script", "rendering OpenMC Python model.py")
    report = state.get("validation_report")
    spec = state.get("simulation_spec")
    if spec is None or report is None or not report.is_valid:
        return {}

    script = render_openmc_script(spec)
    script_report = validate_openmc_script(script, spec)
    if not script_report.is_valid:
        _progress(state, "render_script", f"failed script validation: {script_report.errors}")
        return {
            "validation_report": script_report,
            "error": "; ".join(script_report.errors),
        }

    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    _progress(state, "render_script", f"wrote {model_path}")
    return {"script": script, "model_path": str(model_path)}


def _render_plan_script(state: GraphState) -> GraphState:
    _progress(state, "render_plan_script", "rendering OpenMC Python model.py from SimulationPlan")
    report = state.get("validation_report")
    plan = state.get("simulation_plan")
    if plan is None or report is None or not report.is_valid:
        return {}

    script = render_openmc_plan_script(plan)
    script_report = validate_openmc_script(script, plan.model_spec)
    if not script_report.is_valid:
        _progress(state, "render_plan_script", f"failed script validation: {script_report.errors}")
        return {
            "validation_report": script_report,
            "error": "; ".join(script_report.errors),
        }

    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    _progress(state, "render_plan_script", f"wrote {model_path}")
    return {"script": script, "model_path": str(model_path)}


def _make_execute_tools_node(
    *,
    export_xml_tool: ExportXmlToolFn,
    plot_tool: PlotToolFn,
    smoke_test_tool: SmokeTestToolFn,
    enable_plots: bool,
    enable_smoke_test: bool,
):
    def _execute_tools(state: GraphState) -> GraphState:
        plan = state.get("simulation_plan")
        model_path = state.get("model_path")
        if plan is None or not model_path:
            return {}

        output_dir = Path(state.get("output_dir", "data/runs"))
        results: list[ToolResult] = []
        _progress(state, "execute_tools", "running export_xml")
        export_result = export_xml_tool(Path(model_path))
        results.append(export_result)
        _progress(state, "execute_tools", f"export_xml ok={export_result.ok}")

        if enable_plots and export_result.ok and plan.plot_specs:
            _progress(state, "execute_tools", f"running run_geometry_plots count={len(plan.plot_specs)}")
            results.append(plot_tool(output_dir))
            _progress(state, "execute_tools", f"run_geometry_plots ok={results[-1].ok}")
        elif not enable_plots:
            _progress(state, "execute_tools", "skipping run_geometry_plots because --plot is disabled")

        if enable_smoke_test and export_result.ok and plan.execution_check.enabled:
            settings = plan.execution_check.settings
            _progress(
                state,
                "execute_tools",
                (
                    "running run_smoke_test "
                    f"batches={settings.batches} inactive={settings.inactive} "
                    f"particles={settings.particles}"
                ),
            )
            results.append(smoke_test_tool(output_dir, plan))
            _progress(state, "execute_tools", f"run_smoke_test ok={results[-1].ok}")
        elif not enable_smoke_test:
            _progress(state, "execute_tools", "skipping run_smoke_test because --smoke-test is disabled")

        report = _execution_report_from_tool_results(results)
        _progress(
            state,
            "execute_tools",
            f"tool checks {'passed' if report.is_valid else 'failed'} with {len(report.errors)} error(s)",
        )
        history = list(state.get("retry_history", []))
        if history:
            history[-1]["tool_results"] = [result.model_dump() for result in results]
            history[-1]["execution_errors"] = report.errors

        return {
            "tool_results": [result.model_dump() for result in results],
            "validation_report": report,
            "retry_history": history,
            "error": "; ".join(report.errors),
        }

    return _execute_tools


def _make_reflect_plan_node(repair_plan: RepairPlanFn):
    def _reflect_plan(state: GraphState) -> GraphState:
        plan = state.get("simulation_plan")
        report = state.get("validation_report")
        retry_count = state.get("retry_count", 0)
        if plan is None or report is None or report.is_valid:
            return {"retry_count": retry_count}

        reflection_requirement = _build_reflection_requirement(state)
        _progress(state, "reflect_plan", f"calling LLM reflection retry={retry_count + 1}")
        result = repair_plan(
            requirement=reflection_requirement,
            schema=SimulationPlan,
            model=state.get("model", "openai:gpt-4o"),
            previous_spec=plan,
            validation_errors=report.errors,
        )
        if not result.ok or result.value is None:
            _progress(state, "reflect_plan", f"failed: {result.error}")
            return {
                "retry_count": retry_count + 1,
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "error": result.error or "failed to repair SimulationPlan",
            }
        _progress(state, "reflect_plan", "reflection produced a new SimulationPlan")
        return {
            "simulation_plan": result.value,
            "simulation_spec": result.value.model_spec,
            "retry_count": retry_count + 1,
            "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
            "error": "",
        }

    return _reflect_plan


def _save_record(state: GraphState) -> GraphState:
    _progress(state, "save_record", "appending simulation record")
    report = state.get("validation_report") or ValidationReport(
        is_valid=False,
        errors=[state.get("error", "unknown graph error")],
    )
    records_path = state.get("records_path", "data/runs/simulation_runs.jsonl")
    append_simulation_record(
        requirement=state.get("requirement", ""),
        model=state.get("model", ""),
        simulation_spec=state.get("simulation_spec"),
        validation_report=report,
        path=records_path,
        model_path=state.get("model_path"),
        error=state.get("error", ""),
        retry_count=state.get("retry_count", 0),
        retry_history=state.get("retry_history", []),
    )
    _progress(state, "save_record", "record saved")
    return {}


def _save_plan_record(state: GraphState) -> GraphState:
    _progress(state, "save_record", "appending SimulationPlan run record")
    report = state.get("validation_report") or ValidationReport(
        is_valid=False,
        errors=[state.get("error", "unknown graph error")],
    )
    plan = state.get("simulation_plan")
    records_path = state.get("records_path", "data/runs/simulation_runs.jsonl")
    append_simulation_record(
        requirement=state.get("requirement", ""),
        model=state.get("model", ""),
        simulation_spec=plan.model_spec if plan is not None else None,
        validation_report=report,
        path=records_path,
        model_path=state.get("model_path"),
        error=state.get("error", ""),
        retry_count=state.get("retry_count", 0),
        retry_history=state.get("retry_history", []),
    )
    _progress(state, "save_record", "record saved")
    return {}


def _execution_report_from_tool_results(results: list[ToolResult]) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    suggestions: list[str] = []
    for result in results:
        if not result.ok:
            message = result.error or result.stderr or result.stdout or "tool failed"
            errors.append(f"{result.name} failed: {message}")
        diagnostics = parse_openmc_output(result.stdout, result.stderr)
        errors.extend(diagnostics.errors)
        warnings.extend(diagnostics.warnings)
        suggestions.extend(diagnostics.suggestions)
    return ValidationReport(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        suggestions=suggestions,
    )


def _build_reflection_requirement(state: GraphState) -> str:
    tool_results = state.get("tool_results", [])
    expert_feedback = state.get("expert_feedback", [])
    return (
        f"{state.get('requirement', '')}\n\n"
        "The current SimulationPlan failed during OpenMC expert-style execution checks. "
        "Return a corrected SimulationPlan JSON object only. Do not modify Python code directly.\n"
        f"Validation and execution errors: {state.get('error', '')}\n"
        f"Tool results: {_compact_tool_results(tool_results)}\n"
        f"Human expert feedback: {expert_feedback}"
    )


def _requirement_with_expert_feedback(state: GraphState) -> str:
    requirement = state["requirement"]
    feedback = state.get("expert_feedback", [])
    if not feedback:
        return requirement
    return (
        f"{requirement}\n\n"
        "Human expert feedback that should guide the structured SimulationPlan:\n"
        + "\n".join(f"- {item}" for item in feedback)
    )


def _append_raw_llm_output(state: GraphState, raw_response: str) -> list[str]:
    outputs = list(state.get("raw_llm_outputs", []))
    if raw_response:
        outputs.append(raw_response)
    return outputs


def _compact_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for result in tool_results:
        compact.append(
            {
                "name": result.get("name"),
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
                "stdout": _truncate_text(result.get("stdout", "")),
                "stderr": _truncate_text(result.get("stderr", "")),
                "error": _truncate_text(result.get("error", "")),
                "artifacts": result.get("artifacts", []),
            }
        )
    return compact


def _truncate_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _progress(state: GraphState, node: str, message: str) -> None:
    if state.get("verbose"):
        print(f"[node:{node}] {message}", file=sys.stderr, flush=True)
