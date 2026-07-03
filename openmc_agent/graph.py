from pathlib import Path
import sys
from typing import Any, Callable, TypedDict

import functools
import json
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from openmc_agent.executor import render_openmc_script
from openmc_agent.few_shots import select_few_shots
from openmc_agent.llm import (
    StructuredOutputResult,
    generate_structured_output,
    normalize_capability_report,
    repair_structured_output,
)
from openmc_agent.openmc_api import retrieve_openmc_context
from openmc_agent.records import append_simulation_record
from openmc_agent.renderers import choose_renderer
from openmc_agent.schemas import (
    ComplexModelSpec,
    ExecutionCheckSpec,
    ExpertFeedback,
    PlotSpec,
    RenderCapabilityReport,
    SimulationPlan,
    SimulationSpec,
    ValidationReport,
)
from openmc_agent.tools import (
    ToolResult,
    export_xml,
    parse_openmc_output,
    run_geometry_plots,
    run_smoke_test,
)
from openmc_agent.validator import (
    validate_openmc_script,
    validate_simulation_plan,
    validate_simulation_spec,
)


GenerateSpecFn = Callable[..., StructuredOutputResult[SimulationSpec]]
RepairSpecFn = Callable[..., StructuredOutputResult[SimulationSpec]]
GeneratePlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
RepairPlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
ExportXmlToolFn = Callable[[str | Path], ToolResult]
PlotToolFn = Callable[[str | Path], ToolResult]
SmokeTestToolFn = Callable[[str | Path, SimulationPlan], ToolResult]
RetrieveOpenMCDocsFn = Callable[[str], list[dict[str, str]]]
SelectFewShotsFn = Callable[[str], list[dict[str, str]]]


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
    pending_expert_questions: list[str]
    expert_round_count: int
    max_expert_rounds: int
    awaiting_expert_feedback: bool
    human_loop_events: list[dict[str, Any]]
    needs_regeneration: bool
    raw_llm_outputs: list[str]
    openmc_api_docs: list[dict[str, str]]
    few_shot_examples: list[dict[str, str]]
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
    generate_plan: GeneratePlanFn = functools.partial(
        generate_structured_output, normalizer=normalize_capability_report
    ),
    repair_plan: RepairPlanFn = functools.partial(
        repair_structured_output, normalizer=normalize_capability_report
    ),
    *,
    export_xml_tool: ExportXmlToolFn = export_xml,
    plot_tool: PlotToolFn = run_geometry_plots,
    smoke_test_tool: SmokeTestToolFn = run_smoke_test,
    retrieve_docs: RetrieveOpenMCDocsFn = retrieve_openmc_context,
    select_examples: SelectFewShotsFn = select_few_shots,
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
    graph.add_node("retrieve_openmc_docs", _make_retrieve_openmc_docs_node(retrieve_docs))
    graph.add_node("select_few_shots", _make_select_few_shots_node(select_examples))
    graph.add_node("generate_plan", _make_generate_plan_node(generate_plan))
    graph.add_node("validate_plan", _make_validate_plan_node(max_retries))
    graph.add_node("repair_plan_format", _make_repair_plan_format_node(generate_plan, max_retries))
    graph.add_node("assess_capability", _assess_plan_capability)
    graph.add_node("ask_expert", _ask_expert)
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
    graph.add_edge("receive_requirement", "retrieve_openmc_docs")
    graph.add_edge("retrieve_openmc_docs", "select_few_shots")
    graph.add_edge("select_few_shots", "generate_plan")
    graph.add_edge("generate_plan", "validate_plan")
    graph.add_conditional_edges(
        "validate_plan",
        _make_plan_validation_router(max_retries),
        {
            "assess": "assess_capability",
            "reflect": "reflect_plan",
            "repair_format": "repair_plan_format",
            "stop": "save_record",
        },
    )
    graph.add_edge("repair_plan_format", "validate_plan")
    graph.add_edge("reflect_plan", "validate_plan")
    graph.add_edge("assess_capability", "ask_expert")
    graph.add_conditional_edges(
        "ask_expert",
        _make_expert_feedback_router(),
        {
            "generate": "generate_plan",
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


def _make_retrieve_openmc_docs_node(retrieve_docs: RetrieveOpenMCDocsFn):
    def _retrieve_openmc_docs(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}
        _progress(state, "retrieve_openmc_docs", "retrieving local OpenMC API context")
        try:
            docs = retrieve_docs(state["requirement"])
        except Exception as exc:
            _progress(state, "retrieve_openmc_docs", f"failed: {exc}")
            return {"openmc_api_docs": []}
        _progress(state, "retrieve_openmc_docs", f"retrieved {len(docs)} API document(s)")
        return {"openmc_api_docs": docs}

    return _retrieve_openmc_docs


def _make_select_few_shots_node(select_examples: SelectFewShotsFn):
    def _select_few_shots(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}
        _progress(state, "select_few_shots", "selecting modeling few-shot examples")
        try:
            examples = select_examples(state["requirement"])
        except Exception as exc:
            _progress(state, "select_few_shots", f"failed: {exc}")
            return {"few_shot_examples": []}
        _progress(state, "select_few_shots", f"selected {len(examples)} example(s)")
        return {"few_shot_examples": examples}

    return _select_few_shots


def _make_generate_plan_node(generate_plan: GeneratePlanFn):
    def _generate_plan(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}

        model = state.get("model", "openai:gpt-4o")
        _progress(state, "generate_plan", f"calling LLM model={model}")
        result = generate_plan(
            requirement=_augmented_plan_requirement(state),
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
                f"generated SimulationPlan name={_plan_name(result.value)!r}, "
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


def _make_repair_plan_format_node(generate_plan: GeneratePlanFn, max_retries: int):
    def _repair_plan_format(state: GraphState) -> GraphState:
        retry_count = state.get("retry_count", 0)
        if retry_count >= max_retries:
            return {"retry_count": retry_count}

        model = state.get("model", "openai:gpt-4o")
        _progress(
            state,
            "repair_plan_format",
            f"calling LLM JSON-format repair retry={retry_count + 1}/{max_retries}",
        )
        result = generate_plan(
            requirement=_build_format_repair_requirement(state),
            schema=SimulationPlan,
            model=model,
        )
        if not result.ok or result.value is None:
            _progress(state, "repair_plan_format", f"failed: {result.error}")
            return {
                "simulation_plan": None,
                "simulation_spec": None,
                "retry_count": retry_count + 1,
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "error": result.error or "failed to repair SimulationPlan JSON format",
            }
        _progress(state, "repair_plan_format", "format repair produced a SimulationPlan")
        return {
            "simulation_plan": result.value,
            "simulation_spec": result.value.model_spec,
            "retry_count": retry_count + 1,
            "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
            "error": "",
        }

    return _repair_plan_format


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
        _progress(state, "validate_plan", "validating SimulationPlan")
        plan = _coerce_simulation_plan(state.get("simulation_plan"))
        retry_count = state.get("retry_count", 0)
        if plan is None:
            report = ValidationReport(
                is_valid=False,
                errors=[state.get("error", "SimulationPlan is missing")],
            )
        else:
            report = validate_simulation_plan(plan)

        history = list(state.get("retry_history", []))
        history.append(
            {
                "requirement": state.get("requirement", ""),
                "retry_count": retry_count,
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "validation_errors": report.errors,
                "fix_suggestion": (
                    (
                        "Non-executable complex-only plan used a supported_renderer other than "
                        "'none'; set capability_report.supported_renderer='none' and "
                        "executable_subsystems=[]."
                        if any(
                            "non-executable complex-only plans must use supported_renderer='none'"
                            in err
                            for err in report.errors
                        )
                        else "Ask the model to repair the plan using validation and execution errors."
                    )
                    if not report.is_valid and retry_count < max_retries
                    else ""
                ),
            }
        )

        if not report.is_valid:
            _progress(state, "validate_plan", f"failed with {len(report.errors)} error(s)")
            updates: GraphState = {
                "validation_report": report,
                "retry_history": history,
                "error": "; ".join(report.errors),
            }
            if plan is None and _plan_generation_needs_expert_question(report.errors):
                updates["pending_expert_questions"] = _plan_generation_expert_questions(
                    report.errors,
                    state.get("raw_llm_outputs", []),
                )
            return updates
        _progress(state, "validate_plan", "passed")
        return {
            "validation_report": report,
            "retry_history": history,
            "simulation_spec": plan.model_spec if plan is not None else None,
            "error": "",
        }

    return _validate_plan


def _plan_generation_needs_expert_question(errors: list[str]) -> bool:
    text = "\n".join(errors).lower()
    return "could not parse model response" in text or "could not validate model response" in text


def _plan_generation_expert_questions(
    errors: list[str],
    raw_outputs: list[str],
) -> list[str]:
    questions = [
        "The LLM did not return a valid SimulationPlan JSON object after retries. "
        "Review the modeling request or provide expert feedback that reduces large "
        "lattice/core patterns into explicit assumptions or confirmed pattern ids."
    ]
    latest_raw = raw_outputs[-1] if raw_outputs else ""
    if _looks_like_truncated_json(latest_raw, "\n".join(errors)):
        questions.append(
            "The response appears truncated while emitting a large lattice pattern. "
            "Confirm whether oversized universe_pattern/rings arrays may be omitted "
            "from the first pass and recorded as human-confirmation TODO items."
        )
    return questions


def _make_validation_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid:
            return "render"
        if state.get("simulation_spec") is not None and state.get("retry_count", 0) < max_retries:
            return "repair"
        return "stop"

    return _route


def _make_plan_validation_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid and _coerce_simulation_plan(state.get("simulation_plan")) is not None:
            return "assess"
        if state.get("retry_count", 0) >= max_retries:
            return "stop"
        if _coerce_simulation_plan(state.get("simulation_plan")) is not None:
            return "reflect"
        if state.get("raw_llm_outputs"):
            return "repair_format"
        return "stop"

    return _route


def _make_plan_capability_router():
    def _route(state: GraphState) -> str:
        plan = _coerce_simulation_plan(state.get("simulation_plan"))
        report = state.get("validation_report")
        if report is None or not report.is_valid or plan is None:
            return "stop"
        # skeleton / exportable / runnable all produce a model.py; only 'none' stops.
        if plan.capability_report.renderability == "none":
            return "stop"
        return "render"

    return _route


def _make_expert_feedback_router():
    def _route(state: GraphState) -> str:
        if state.get("needs_regeneration"):
            return "generate"
        return _make_plan_capability_router()(state)

    return _route


def _make_plan_execution_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid:
            return "save"
        if (
            _coerce_simulation_plan(state.get("simulation_plan")) is not None
            and state.get("retry_count", 0) < max_retries
        ):
            return "reflect"
        return "save"

    return _route


def _assess_plan_capability(state: GraphState) -> GraphState:
    _progress(state, "assess_capability", "checking executor support for structured plan")
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    if plan is None:
        return {}

    capability = _capability_for_plan(plan)
    updated_plan = plan.model_copy(update={"capability_report": capability})
    report = state.get("validation_report") or ValidationReport(is_valid=True)
    warnings = [
        warning
        for warning in report.warnings
        if warning != "Complex OpenMC IR was generated, but this executor version cannot render it yet."
    ]
    suggestions = [
        suggestion
        for suggestion in report.suggestions
        if suggestion
        != "Review complex_model and capability_report before implementing a renderer for this subsystem."
    ]

    if capability.renderability == "none":
        message = "; ".join(capability.reasons) or "no renderer can handle this plan"
        if message not in warnings:
            warnings.append(message)
        suggestions.append(
            "Use complex_model as reviewed IR before adding a renderer for this subsystem."
        )
    elif capability.renderability == "skeleton":
        warnings.append(
            "Renderer produced a review-only model.py skeleton; the model is NOT executable."
        )
        suggestions.append(
            "Fill the gaps listed in capability_report.json and TODO.md before exporting XML."
        )

    capability_warnings = [
        warning for warning in capability.warnings if warning not in warnings
    ]
    warnings.extend(capability_warnings)

    updated_report = ValidationReport(
        is_valid=report.is_valid,
        errors=report.errors,
        warnings=warnings,
        suggestions=suggestions,
    )
    _progress(
        state,
        "assess_capability",
        (
            f"renderer={capability.supported_renderer} "
            f"renderability={capability.renderability}"
        ),
    )
    return {
        "simulation_plan": updated_plan,
        "simulation_spec": updated_plan.model_spec,
        "validation_report": updated_report,
    }


def _ask_expert(state: GraphState) -> GraphState:
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    plan_update = {"simulation_plan": plan} if plan is not None else {}
    questions = _pending_expert_questions(state)
    max_rounds = state.get("max_expert_rounds", 0)
    round_count = state.get("expert_round_count", 0)
    events = list(state.get("human_loop_events", []))

    if not questions:
        return {
            **plan_update,
            "pending_expert_questions": [],
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
        }

    if round_count >= max_rounds:
        _progress(
            state,
            "ask_expert",
            (
                f"expert questions remain but max rounds reached "
                f"({round_count}/{max_rounds})"
            ),
        )
        events.append(
            {
                "event": "expert_questions_not_asked",
                "round": round_count,
                "questions": questions,
                "reason": "max_expert_rounds reached or expert loop disabled",
            }
        )
        return {
            **plan_update,
            "pending_expert_questions": questions,
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
            "human_loop_events": events,
        }

    prompt_round = round_count + 1
    _progress(
        state,
        "ask_expert",
        f"interrupting for expert feedback round {prompt_round}/{max_rounds}",
    )
    resume_payload = interrupt(
        {
            "kind": "expert_feedback_request",
            "round": prompt_round,
            "max_rounds": max_rounds,
            "questions": questions,
            "instruction": (
                "Provide expert feedback that fills, corrects, or explicitly defers "
                "the missing modeling facts."
            ),
        }
    )
    feedback_items, should_continue = _feedback_from_resume_payload(resume_payload)
    event = {
        "event": "expert_feedback_received",
        "round": prompt_round,
        "questions": questions,
        "feedback": feedback_items,
        "should_continue": should_continue,
    }
    events.append(event)

    if not should_continue or not feedback_items:
        return {
            **plan_update,
            "pending_expert_questions": questions,
            "expert_round_count": prompt_round,
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
            "human_loop_events": events,
        }

    feedback = list(state.get("expert_feedback", []))
    feedback.extend(feedback_items)
    return {
        **plan_update,
        "expert_feedback": feedback,
        "pending_expert_questions": [],
        "expert_round_count": prompt_round,
        "awaiting_expert_feedback": False,
        "needs_regeneration": True,
        "human_loop_events": events,
        "error": "",
    }


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
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    if plan is None or report is None or not report.is_valid:
        _progress(state, "render_plan_script", "skipped: plan is not valid")
        return {}

    renderer, capability = choose_renderer(plan)
    if renderer is None or capability.renderability == "none":
        _progress(state, "render_plan_script", "skipped: no renderer for this plan")
        return {}

    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    result = renderer.render(plan, output_dir)
    # The authoritative capability report lives on the plan (assess_capability),
    # so refresh capability_report.json from it to keep sidecars consistent.
    _write_capability_sidecar(output_dir, plan.capability_report)

    if result.errors:
        _progress(state, "render_plan_script", f"renderer failed: {result.errors}")
        return {
            "validation_report": ValidationReport(
                is_valid=False,
                errors=result.errors,
            ),
            "error": "; ".join(result.errors),
        }

    model_path = output_dir / "model.py"
    _progress(
        state,
        "render_plan_script",
        f"wrote {model_path} (renderer={result.renderer_name}, "
        f"renderability={result.renderability})",
    )
    return {"script": result.script, "model_path": str(model_path)}


def _make_execute_tools_node(
    *,
    export_xml_tool: ExportXmlToolFn,
    plot_tool: PlotToolFn,
    smoke_test_tool: SmokeTestToolFn,
    enable_plots: bool,
    enable_smoke_test: bool,
):
    def _execute_tools(state: GraphState) -> GraphState:
        plan = _coerce_simulation_plan(state.get("simulation_plan"))
        model_path = state.get("model_path")
        if plan is None or not model_path:
            return {}

        renderability = plan.capability_report.renderability
        output_dir = Path(state.get("output_dir", "data/runs"))
        results: list[ToolResult] = []

        if renderability not in {"exportable", "runnable"}:
            _progress(
                state,
                "execute_tools",
                (
                    f"skipping export/run: renderability={renderability}, "
                    "model is not executable"
                ),
            )
            report = _execution_report_from_tool_results(results)
            return {
                "tool_results": [result.model_dump() for result in results],
                "validation_report": report,
                "error": "",
            }

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

        if (
            enable_smoke_test
            and export_result.ok
            and renderability == "runnable"
            and plan.execution_check.enabled
        ):
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
        elif renderability != "runnable":
            _progress(
                state,
                "execute_tools",
                f"skipping run_smoke_test: renderability={renderability} (not runnable)",
            )

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
        plan = _coerce_simulation_plan(state.get("simulation_plan"))
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
        pending_expert_questions=state.get("pending_expert_questions", []),
        human_loop_events=state.get("human_loop_events", []),
    )
    _progress(state, "save_record", "record saved")
    return {}


def _save_plan_record(state: GraphState) -> GraphState:
    _progress(state, "save_record", "appending SimulationPlan run record")
    report = state.get("validation_report") or ValidationReport(
        is_valid=False,
        errors=[state.get("error", "unknown graph error")],
    )
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    records_path = state.get("records_path", "data/runs/simulation_runs.jsonl")
    append_simulation_record(
        requirement=state.get("requirement", ""),
        model=state.get("model", ""),
        simulation_spec=plan.model_spec if plan is not None else None,
        validation_report=report,
        path=records_path,
        simulation_plan=plan.model_dump(mode="json") if plan is not None else None,
        model_path=state.get("model_path"),
        error=state.get("error", ""),
        retry_count=state.get("retry_count", 0),
        retry_history=state.get("retry_history", []),
        pending_expert_questions=state.get("pending_expert_questions", []),
        human_loop_events=state.get("human_loop_events", []),
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


def _build_format_repair_requirement(state: GraphState) -> str:
    raw_outputs = state.get("raw_llm_outputs", [])
    latest_raw = raw_outputs[-1] if raw_outputs else ""
    truncation_guidance = (
        "\nThe previous response appears truncated or too large. Do NOT attempt to "
        "reproduce large 17x17, 34x34, assembly, or core universe_pattern arrays. "
        "Return a minimal valid review-only SimulationPlan instead: keep lattice "
        "shape/pitch/ids, set oversized or uncertain universe_pattern/rings to [], "
        "and add explicit requires_human_confirmation items such as "
        "'rect lattice universe_pattern is missing'.\n"
        if _looks_like_truncated_json(latest_raw, state.get("error", ""))
        else ""
    )
    return (
        f"{_augmented_plan_requirement(state)}\n\n"
        "The previous model response could not be parsed or validated as a "
        "SimulationPlan JSON object. Return a corrected SimulationPlan JSON object only. "
        "Preserve the reactor modeling facts from the case requirement and do not invent "
        "missing physical data.\n"
        f"{truncation_guidance}"
        f"Parse/validation error: {state.get('error', '')}\n"
        f"Previous raw response: {_truncate_text(latest_raw, 4000)}"
    )


def _looks_like_truncated_json(raw: str, error: str) -> bool:
    lowered = error.lower()
    if "unterminated string" in lowered:
        return True
    if raw.count("{") > raw.count("}") or raw.count("[") > raw.count("]"):
        return True
    tail = raw.rstrip()
    return bool(tail) and tail[-1] not in {"}", "]", "`"}


def _pending_expert_questions(state: GraphState) -> list[str]:
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    if plan is None:
        return []

    # Once the expert has answered at least one round, do NOT re-ask confirmation /
    # assumption items: the expert already covered them and the regenerating LLM is
    # instructed (via _requirement_with_expert_feedback) to consume the feedback.
    # Re-asking the same items every round until max_expert_rounds is exhausted is the
    # bug this guards against. Structural renderability gaps (skeleton/none
    # reasons/warnings) are a different concern and may still surface.
    feedback_already_given = bool(state.get("expert_feedback"))

    questions: list[str] = []
    capability = plan.capability_report
    if not feedback_already_given:
        for item in capability.required_human_confirmations:
            questions.append(f"Please provide or confirm: {item}")
        for item in plan.expert_assumptions:
            questions.append(f"Please confirm or correct this modeling assumption: {item}")

    if capability.renderability in {"none", "skeleton"}:
        for reason in capability.reasons:
            questions.append(f"What expert information resolves this renderability gap: {reason}")
        for warning in capability.warnings:
            questions.append(f"Please review this modeling warning: {warning}")

    return list(dict.fromkeys(q for q in questions if q.strip()))[:8]


def _feedback_from_resume_payload(payload: Any) -> tuple[list[str], bool]:
    if payload is None:
        return [], False
    if isinstance(payload, str):
        text = payload.strip()
        return ([text] if text else []), bool(text)
    if isinstance(payload, list):
        items = [str(item).strip() for item in payload if str(item).strip()]
        return items, bool(items)
    if isinstance(payload, dict):
        should_continue = bool(payload.get("should_continue", True))
        raw_feedback = (
            payload.get("expert_feedback")
            or payload.get("feedback")
            or payload.get("text")
            or payload.get("answer")
        )
        if isinstance(raw_feedback, list):
            items = [str(item).strip() for item in raw_feedback if str(item).strip()]
        elif raw_feedback is None:
            items = []
        else:
            text = str(raw_feedback).strip()
            items = [text] if text else []
        return items, should_continue
    text = str(payload).strip()
    return ([text] if text else []), bool(text)


def _coerce_simulation_plan(value: Any) -> SimulationPlan | None:
    if value is None:
        return None
    if isinstance(value, SimulationPlan):
        if isinstance(value.capability_report, dict) or isinstance(value.complex_model, dict):
            return _construct_simulation_plan_from_payload(dict(value.__dict__))
        return value
    if isinstance(value, dict):
        try:
            return SimulationPlan.model_validate(value)
        except Exception:
            return _construct_simulation_plan_from_payload(value)
    return None


def _construct_simulation_plan_from_payload(payload: dict[str, Any]) -> SimulationPlan:
    return SimulationPlan.model_construct(
        schema_version=payload.get("schema_version", "simulation_plan.v1"),
        model_spec=(
            SimulationSpec.model_validate(payload["model_spec"])
            if payload.get("model_spec") is not None
            else None
        ),
        complex_model=(
            ComplexModelSpec.model_validate(payload["complex_model"])
            if payload.get("complex_model") is not None
            else None
        ),
        capability_report=RenderCapabilityReport.model_validate(
            payload.get("capability_report") or {}
        ),
        plot_specs=[
            PlotSpec.model_validate(item) for item in payload.get("plot_specs", [])
        ],
        execution_check=ExecutionCheckSpec.model_validate(
            payload.get("execution_check") or {}
        ),
        expert_assumptions=list(payload.get("expert_assumptions", [])),
        expert_feedback=[
            ExpertFeedback.model_validate(item)
            for item in payload.get("expert_feedback", [])
        ],
    )


def _capability_for_plan(plan: SimulationPlan) -> RenderCapabilityReport:
    """Use the renderer registry as the single source of truth for capability."""
    _renderer, report = choose_renderer(plan)
    # Merge plan-level human confirmations (e.g. complex_model.requires_human_confirmation)
    # on top of the renderer-level report so the sidecar records everything.
    confirmations = list(
        dict.fromkeys(
            [
                *report.required_human_confirmations,
                *_plan_human_confirmations(plan),
            ]
        )
    )
    return report.model_copy(update={"required_human_confirmations": confirmations})


def _write_capability_sidecar(output_dir: Path, capability: RenderCapabilityReport) -> None:
    """Keep capability_report.json consistent with the authoritative plan report."""
    path = output_dir / "capability_report.json"
    path.write_text(
        json.dumps(capability.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _plan_human_confirmations(plan: SimulationPlan) -> list[str]:
    confirmations = list(plan.capability_report.required_human_confirmations)
    model = plan.complex_model
    if model is None:
        return confirmations
    confirmations.extend(model.requires_human_confirmation)
    for material in model.materials:
        confirmations.extend(
            f"material {material.id}: {item}"
            for item in material.requires_human_confirmation
        )
    for lattice in model.lattices:
        confirmations.extend(
            f"lattice {lattice.id}: {item}"
            for item in lattice.requires_human_confirmation
        )
    for triso in model.trisos:
        confirmations.extend(
            f"TRISO {triso.id}: {item}"
            for item in triso.requires_human_confirmation
        )
    for pebble in model.pebbles:
        confirmations.extend(
            f"pebble {pebble.id}: {item}"
            for item in pebble.requires_human_confirmation
        )
    return list(dict.fromkeys(confirmations))


def _requirement_with_expert_feedback(state: GraphState) -> str:
    requirement = state["requirement"]
    feedback = state.get("expert_feedback", [])
    if not feedback:
        return requirement
    return (
        f"{requirement}\n\n"
        "Human expert feedback that should guide the structured SimulationPlan:\n"
        + "\n".join(f"- {item}" for item in feedback)
        + "\n\n"
        "Expert-feedback consumption rule (IMPORTANT; do not re-ask answered items):\n"
        "- For every fact the expert confirmed or provided, write the concrete value "
        "into the corresponding material / geometry / settings field.\n"
        "- Remove the matching entries from requires_human_confirmation and "
        "expert_assumptions. Keep ONLY items the expert did NOT address.\n"
        "- Do not re-list already-answered items as requires_human_confirmation. "
        "Asking the expert the same question in a later round is a regression."
    )


def _augmented_plan_requirement(state: GraphState) -> str:
    base = _requirement_with_expert_feedback(state)
    docs = state.get("openmc_api_docs", [])
    few_shots = state.get("few_shot_examples", [])
    parts = [
        base,
        "",
        "OpenMC API context retrieved from local Python introspection and official docs references:",
        _compact_context(docs),
        "",
        "Few-shot modeling patterns to follow when relevant:",
        _compact_context(few_shots),
    ]
    return "\n".join(parts)


def _compact_context(items: list[dict[str, str]], *, limit: int = 6) -> str:
    if not items:
        return "[]"
    compact = []
    for item in items[:limit]:
        compact.append(
            {
                key: _truncate_text(str(value), 700)
                for key, value in item.items()
                if key in {"symbol", "signature", "doc_summary", "official_url", "name", "structured_outline"}
            }
        )
    return str(compact)


def _plan_name(plan: SimulationPlan) -> str:
    if plan.model_spec is not None:
        return plan.model_spec.name
    if plan.complex_model is not None:
        return plan.complex_model.name
    return "unknown"


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
