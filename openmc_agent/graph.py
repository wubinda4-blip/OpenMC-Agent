import os
from pathlib import Path
import sys
from typing import Any, Callable, Literal, TypedDict

import functools
import json
import re
import sqlite3
from datetime import datetime, timezone
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from openmc_agent.executor import render_openmc_script
from openmc_agent.auto_repair import auto_repair_lattice_structure
from openmc_agent.few_shots import select_few_shots
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.lattice_validation import (
    extract_canonical_pin_map,
    is_structural_error_confirmation,
    lattice_cell_mismatches,
)
from openmc_agent.llm import (
    StructuredOutputResult,
    generate_structured_output,
    normalize_capability_report,
    repair_structured_output,
)
from openmc_agent.openmc_api import retrieve_openmc_context
from openmc_agent.plan_builder import (
    should_use_incremental_planning,
    initialize_plan_build_state,
)
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.records import append_simulation_record
from openmc_agent.renderers import choose_renderer
from openmc_agent.retrieval import (
    RetrievalOutcome,
    ToolSpec,
    run_retrieval_loop,
)
from openmc_agent.retrieval_orchestrator import (
    RetrievalContext,
    RetrievalPolicy,
    format_retrieval_context,
    gather_retrieval_context_for_issues,
    retrieval_context_from_raw,
)
from openmc_agent.schemas import (
    ComplexModelSpec,
    ExecutionCheckSpec,
    ExpertFeedback,
    PlotSpec,
    RenderCapabilityReport,
    ResolvedExpertItem,
    SimulationPlan,
    SimulationSpec,
    ValidationIssue,
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
from openmc_agent.workflow_trace import (
    TraceConfig,
    TraceRecorder,
    preview_plan,
    summarize_capability_report,
    summarize_retrieval_context as summarize_retrieval_context_for_trace,
    summarize_validation_report,
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
    expert_feedback_action: Literal[
        "none",
        "classify",
        "continue",
        "patch_plan",
        "regenerate_plan",
        "manual_review",
    ]
    expert_feedback_interpretation: str | None
    plan_patch: list[dict[str, Any]] | None
    patch_confidence: Literal["high", "medium", "low"] | None
    patch_reason: str | None
    patch_error: str | None
    resolved_expert_items: list[dict[str, Any]]
    capability_repair_errors: list[str]
    raw_llm_outputs: list[str]
    candidate_payload: dict[str, Any] | None
    plan_artifacts: list[str]
    hard_count_constraints: str
    pin_count_mismatch_context: str
    openmc_api_docs: list[dict[str, str]]
    few_shot_examples: list[dict[str, str]]
    verbose: bool
    investigation_trace: list[dict[str, Any]]
    investigation_findings: str
    retrieval_context: dict[str, Any]
    retrieval_prompt: str
    grep_evidence: list[dict[str, Any]]
    graph_context: dict[str, Any]
    rag_evidence: list[dict[str, Any]]
    patch_failure_count: int
    trace: dict[str, Any]
    planning_mode_decision: dict[str, Any]
    plan_build_state: dict[str, Any]
    incremental_execution_result: dict[str, Any]
    use_incremental_executor: bool
    allow_monolithic_fallback_for_incremental_failure: bool


InvestigationLlmFn = Callable[[str], StructuredOutputResult]
RetrievalRootsResolver = Callable[[GraphState], list[Path]]


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
    investigation_llm: InvestigationLlmFn | None = None,
    retrieval_roots_resolver: RetrievalRootsResolver | None = None,
    retrieval_tool_dispatch: dict[str, Callable[..., ToolResult]] | None = None,
    retrieval_tool_specs: list[ToolSpec] | None = None,
    investigation_max_iterations: int = 4,
    enable_openmc_source_root: bool = False,
    enable_plots: bool = True,
    enable_smoke_test: bool = True,
    max_retries: int = 3,
    checkpoint_path: str | Path | None = None,
    checkpointer: Any | None = None,
    retrieval_policy: RetrievalPolicy | None = None,
    knowledge_graph_path: str | Path | None = None,
    patch_llm_client: Callable[[str], str] | None = None,
    use_incremental_executor: bool = True,
    allow_monolithic_fallback_for_incremental_failure: bool = False,
):
    if checkpoint_path is not None and checkpointer is not None:
        raise ValueError("Use either checkpoint_path or checkpointer, not both")
    if checkpoint_path is not None:
        checkpointer = _build_sqlite_checkpointer(checkpoint_path)

    if retrieval_roots_resolver is None:
        retrieval_roots_resolver = _make_default_retrieval_roots_resolver(
            enable_openmc_source_root=enable_openmc_source_root
        )

    effective_retrieval_policy = retrieval_policy or RetrievalPolicy()
    if (
        knowledge_graph_path is not None
        and effective_retrieval_policy.knowledge_graph_path is None
    ):
        effective_retrieval_policy = effective_retrieval_policy.model_copy(
            update={"knowledge_graph_path": str(knowledge_graph_path)}
        )

    graph = StateGraph(GraphState)
    graph.add_node("receive_requirement", _receive_requirement)
    graph.add_node("retrieve_openmc_docs", _make_retrieve_openmc_docs_node(retrieve_docs))
    graph.add_node("select_few_shots", _make_select_few_shots_node(select_examples))
    graph.add_node(
        "generate_plan",
        _make_generate_plan_node(
            generate_plan,
            investigation_llm=investigation_llm,
            retrieval_roots_resolver=retrieval_roots_resolver,
            retrieval_tool_dispatch=retrieval_tool_dispatch,
            retrieval_tool_specs=retrieval_tool_specs,
            investigation_max_iterations=investigation_max_iterations,
            patch_llm_client=patch_llm_client,
            use_incremental_executor=use_incremental_executor,
            allow_monolithic_fallback_for_incremental_failure=allow_monolithic_fallback_for_incremental_failure,
        ),
    )
    graph.add_node("validate_plan", _make_validate_plan_node(max_retries))
    graph.add_node("repair_plan_format", _make_repair_plan_format_node(generate_plan, max_retries))
    graph.add_node("assess_capability", _make_assess_plan_capability_node(max_retries))
    graph.add_node("ask_expert", _ask_expert)
    graph.add_node("classify_expert_feedback", _classify_expert_feedback)
    graph.add_node("patch_plan_from_expert_feedback", _patch_plan_from_expert_feedback)
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
    graph.add_node(
        "reflect_plan",
        _make_reflect_plan_node(
            repair_plan,
            investigation_llm=investigation_llm,
            retrieval_roots_resolver=retrieval_roots_resolver,
            retrieval_tool_dispatch=retrieval_tool_dispatch,
            retrieval_tool_specs=retrieval_tool_specs,
            investigation_max_iterations=investigation_max_iterations,
            retrieval_policy=effective_retrieval_policy,
        ),
    )
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
    graph.add_conditional_edges(
        "assess_capability",
        _make_plan_capability_assessment_router(),
        {
            "reflect": "reflect_plan",
            "ask": "ask_expert",
        },
    )
    graph.add_conditional_edges(
        "ask_expert",
        _make_expert_feedback_router(),
        {
            "classify": "classify_expert_feedback",
            "render": "render_plan_script",
            "stop": "save_record",
        },
    )
    graph.add_conditional_edges(
        "classify_expert_feedback",
        _make_expert_feedback_action_router(),
        {
            "patch": "patch_plan_from_expert_feedback",
            "generate": "generate_plan",
            "render": "render_plan_script",
            "stop": "save_record",
        },
    )
    graph.add_conditional_edges(
        "patch_plan_from_expert_feedback",
        _make_plan_patch_router(),
        {
            "validate": "validate_plan",
            "generate": "generate_plan",
        },
    )
    graph.add_edge("render_plan_script", "execute_tools")
    graph.add_conditional_edges(
        "execute_tools",
        _make_plan_execution_router(max_retries),
        {
            "ask": "ask_expert",
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

    # Phase 0: decide planning mode (monolithic vs incremental).
    decision = should_use_incremental_planning(
        requirement,
        retry_history=state.get("retry_history"),
    )
    updates: GraphState = {
        "requirement": requirement,
        "hard_count_constraints": _extract_hard_count_constraints(requirement),
        "planning_mode_decision": decision.model_dump(mode="json"),
    }
    _progress(
        state,
        "receive_requirement",
        f"planning mode={decision.mode} triggers={decision.triggers}",
    )
    if decision.mode == "incremental":
        # Phase 1: initialize PlanBuildState for observability.  Phase 0 does
        # NOT execute incremental planning — it records the state and falls
        # back to monolithic so existing behavior is unchanged.
        build_state = initialize_plan_build_state(
            requirement=requirement,
            decision=decision,
        )
        build_state.add_event(
            event_type="planning.incremental_recommended_but_not_executed",
            message=(
                "incremental planning recommended but executor is not yet "
                "implemented; falling back to monolithic"
            ),
            data={"fallback_reason": "incremental_executor_not_implemented"},
        )
        updates["plan_build_state"] = build_state.model_dump(mode="json")
    return updates


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


def _make_default_retrieval_roots_resolver(
    *, enable_openmc_source_root: bool = False
) -> RetrievalRootsResolver:
    """Build the default retrieval-roots resolver from GraphState.

    Roots cover the four evidence sources: the agent source tree, the run's
    output directory (rendered model.py / XML), the data/knowledge dir, and
    (optionally) the installed OpenMC library source.
    """

    def _resolve(state: GraphState) -> list[Path]:
        roots: list[Path] = []
        repo_root = Path(__file__).resolve().parent.parent
        roots.append(repo_root / "openmc_agent")
        output_dir = state.get("output_dir")
        if output_dir:
            roots.append(Path(output_dir))
        roots.append(repo_root / "data")
        if enable_openmc_source_root:
            try:
                import openmc as _openmc  # type: ignore[import-not-found]

                roots.append(Path(_openmc.__file__).resolve().parent)
            except Exception:
                pass
        return [root for root in roots if root.exists()]

    return _resolve


def _error_catalog_hints_for(errors: list[str]) -> list[dict[str, Any]]:
    """Pull matching error_catalog entries to seed the investigation prompt.

    ``ERROR_CATALOG`` entries are plain dicts, but their ``knowledge_refs`` /
    ``repair_hints`` elements are Pydantic models, so access is by attribute.
    """
    try:
        from openmc_agent.error_catalog import ERROR_CATALOG
    except Exception:
        return []
    hints: list[dict[str, Any]] = []
    for code, entry in ERROR_CATALOG.items():
        message = entry.get("message", "")
        schema_path = entry.get("schema_path", "")
        hit = any(
            (bool(message) and message[:40] in err)
            or (bool(schema_path) and schema_path in err)
            for err in errors
        )
        if not hit:
            continue
        retrieval_queries = [
            query
            for ref in entry.get("knowledge_refs", [])
            if (query := getattr(ref, "retrieval_query", None))
        ]
        repair_hints = [
            {
                "action": getattr(hint, "action", ""),
                "message": getattr(hint, "message", ""),
                "target_path": getattr(hint, "target_path", None),
                "example_patch": getattr(hint, "example_patch", None),
            }
            for hint in entry.get("repair_hints", [])
        ]
        hints.append(
            {
                "error_code": code,
                "schema_path": schema_path,
                "retrieval_queries": retrieval_queries,
                "repair_hints": repair_hints,
            }
        )
        if len(hints) >= 5:
            break
    return hints


def _run_investigation_safely(
    state: GraphState,
    *,
    phase: Literal["generate", "reflect"],
    task_brief: str,
    plan_summary: str,
    investigation_llm: InvestigationLlmFn | None,
    retrieval_roots_resolver: RetrievalRootsResolver | None,
    retrieval_tool_dispatch: dict[str, Callable[..., ToolResult]] | None,
    retrieval_tool_specs: list[ToolSpec] | None,
    investigation_max_iterations: int,
    error_catalog_hints: list[dict[str, Any]] | None,
) -> RetrievalOutcome | None:
    """Run the retrieval loop, returning None when disabled or when it fails.

    Never raises: investigation is best-effort and must not block the main
    generate/reflect flow.
    """
    if investigation_llm is None or retrieval_roots_resolver is None:
        return None
    roots = retrieval_roots_resolver(state)
    if not roots:
        return None
    _progress(state, "investigation", f"starting {phase} retrieval loop")
    try:
        outcome = run_retrieval_loop(
            phase=phase,
            task_brief=task_brief,
            plan_summary=plan_summary,
            roots=roots,
            investigation_llm=investigation_llm,
            tool_dispatch=retrieval_tool_dispatch,
            tool_specs=retrieval_tool_specs,
            max_iterations=investigation_max_iterations,
            error_catalog_hints=error_catalog_hints,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _progress(state, "investigation", f"failed: {exc}")
        return None
    patch_ops = len(outcome.patch) if outcome.patch else 0
    _progress(
        state,
        "investigation",
        f"finished iterations={len(outcome.trace)} ok={outcome.ok} patch_ops={patch_ops}",
    )
    return outcome


def _investigation_state_updates(outcome: RetrievalOutcome | None) -> dict[str, Any]:
    if outcome is None:
        return {}
    return {
        "investigation_trace": outcome.trace,
        "investigation_findings": outcome.findings,
    }


PatchLlmClient = Callable[[str], str]


def _run_incremental_plan_generation(
    state: GraphState,
    *,
    patch_llm_client: PatchLlmClient,
    allow_fallback: bool = False,
) -> GraphState:
    """Run the incremental patch executor and inject the assembled plan.

    This replaces the monolithic LLM full-plan call when mode=incremental.
    On success, the assembled SimulationPlan is stored in ``simulation_plan``
    and the existing validation/capability/renderer pipeline takes over.
    On failure, a structured error with patch-level diagnostics is returned.
    """
    from openmc_agent.plan_builder.executor import run_incremental_planning
    from openmc_agent.plan_builder.state import (
        PlanBuildState as _PlanBuildState,
        initialize_plan_build_state as _init_state,
    )
    from openmc_agent.plan_builder.mode import (
        PlanningModeDecision as _PlanningModeDecision,
    )

    requirement = state.get("requirement", "")
    _progress(state, "generate_plan", "incremental mode: running patch executor")

    # Reconstruct or initialize PlanBuildState from graph state.
    pmd_dict = state.get("planning_mode_decision") or {}
    build_state_dict = state.get("plan_build_state")

    if build_state_dict:
        try:
            build_state = _PlanBuildState.model_validate(build_state_dict)
        except Exception:
            decision = _PlanningModeDecision.model_validate(pmd_dict) if pmd_dict else None
            build_state = _init_state(requirement, decision) if decision else _PlanBuildState(
                state_id="pbs_incremental", requirement_text=requirement,
            )
    else:
        decision = _PlanningModeDecision.model_validate(pmd_dict) if pmd_dict else None
        build_state = _init_state(requirement, decision) if decision else _PlanBuildState(
            state_id="pbs_incremental", requirement_text=requirement,
        )

    exec_result = run_incremental_planning(
        requirement=requirement,
        state=build_state,
        llm_client=patch_llm_client,
        max_patch_attempts=2,
        strict=True,
    )

    # Serialize build state back into graph state regardless of outcome.
    state_updates: dict[str, Any] = {
        "plan_build_state": exec_result.state.model_dump(mode="json"),
        "incremental_execution_result": {
            "ok": exec_result.ok,
            "summary": exec_result.summary,
            "issues": [i.model_dump(mode="json") for i in exec_result.issues],
        },
    }

    if exec_result.ok and exec_result.assembled_plan:
        # Parse assembled plan dict into SimulationPlan model.
        try:
            plan = SimulationPlan.model_validate(exec_result.assembled_plan)
        except Exception as exc:
            _progress(state, "generate_plan", f"assembled plan schema invalid: {exc}")
            state_updates.update({
                "simulation_plan": None,
                "error": f"incremental.assembled_plan_schema_invalid: {exc}",
                **_trace_event_update(
                    state,
                    "plan_generated",
                    summary="incremental assembled plan failed schema validation",
                    metadata={
                        "success": False,
                        "reason": "assembled_plan_schema_invalid",
                        "planning_mode": "incremental",
                    },
                ),
            })
            return state_updates

        _progress(
            state,
            "generate_plan",
            f"incremental plan assembled: {len(plan.complex_model.materials)} materials, "
            f"{len(plan.complex_model.universes)} universes",
        )
        artifact_paths = _write_final_simulation_plan(state, plan)
        state_updates.update({
            "simulation_plan": plan,
            "simulation_spec": plan.model_spec,
            "plan_artifacts": artifact_paths,
            "needs_regeneration": False,
            "error": "",
            **_trace_event_update(
                state,
                "plan_generated",
                summary=(
                    f"incremental plan assembled "
                    f"({len(build_state.get_valid_patches())} valid patches)"
                ),
                plan=plan,
                metadata={
                    "success": True,
                    "planning_mode": "incremental",
                    "patch_order": [t.patch_type for t in build_state.component_tasks],
                    "valid_patch_count": len(build_state.get_valid_patches()),
                    "assembly_ok": True,
                },
            ),
        })
        return state_updates

    # Incremental execution failed.
    error_codes = [i.code for i in exec_result.issues if i.severity == "error"]
    _progress(
        state,
        "generate_plan",
        f"incremental execution failed: {error_codes}",
    )

    if allow_fallback:
        _progress(
            state,
            "generate_plan",
            "monolithic fallback enabled; falling back to full-plan generation",
        )
        state_updates.update({
            **_trace_event_update(
                state,
                "plan_generated",
                summary="incremental failed; monolithic fallback enabled",
                metadata={
                    "success": False,
                    "planning_mode": "incremental",
                    "fallback": "monolithic",
                    "error_codes": error_codes,
                },
            ),
        })
        # Return empty so the caller falls through to monolithic.
        return {**state_updates, "_fallback_to_monolithic": True}

    state_updates.update({
        "simulation_plan": None,
        "error": f"incremental.execution_failed: {'; '.join(error_codes[:3])}",
        **_trace_event_update(
            state,
            "plan_generated",
            summary=f"incremental execution failed: {error_codes[:3]}",
            metadata={
                "success": False,
                "planning_mode": "incremental",
                "failed_patch_type": exec_result.summary.get("failed_patch_type"),
                "valid_patch_types": [
                    e.patch_type for e in build_state.patches.values()
                    if e.status == "valid"
                ],
                "error_codes": error_codes,
            },
        ),
    })
    return state_updates


def _make_generate_plan_node(
    generate_plan: GeneratePlanFn,
    *,
    investigation_llm: InvestigationLlmFn | None = None,
    retrieval_roots_resolver: RetrievalRootsResolver | None = None,
    retrieval_tool_dispatch: dict[str, Callable[..., ToolResult]] | None = None,
    retrieval_tool_specs: list[ToolSpec] | None = None,
    investigation_max_iterations: int = 4,
    patch_llm_client: Callable[[str], str] | None = None,
    use_incremental_executor: bool = True,
    allow_monolithic_fallback_for_incremental_failure: bool = False,
):
    def _generate_plan(state: GraphState) -> GraphState:
        if state.get("error"):
            return {}

        # Phase 6: route to incremental executor when mode=incremental.
        pmd = state.get("planning_mode_decision") or {}
        if (
            pmd.get("mode") == "incremental"
            and use_incremental_executor
            and patch_llm_client is not None
        ):
            inc_result = _run_incremental_plan_generation(
                state,
                patch_llm_client=patch_llm_client,
                allow_fallback=allow_monolithic_fallback_for_incremental_failure,
            )
            # If fallback was requested, strip marker and continue to monolithic.
            if inc_result.pop("_fallback_to_monolithic", False):
                _progress(state, "generate_plan", "continuing to monolithic path")
            else:
                return inc_result

        # Phase 7: auto-construct patch client from model name when incremental.
        if (
            pmd.get("mode") == "incremental"
            and use_incremental_executor
            and patch_llm_client is None
        ):
            model_name = state.get("model", "openai:gpt-4o")
            try:
                from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
                auto_client = make_patch_llm_client(model_name=model_name)
                _progress(
                    state,
                    "generate_plan",
                    f"auto-constructed patch LLM client from model={model_name}",
                )
                inc_result = _run_incremental_plan_generation(
                    state,
                    patch_llm_client=auto_client,
                    # Auto-constructed client failures should fall through to
                    # monolithic, since the client was a best-effort attempt.
                    allow_fallback=True,
                )
                if inc_result.pop("_fallback_to_monolithic", False):
                    _progress(state, "generate_plan", "continuing to monolithic path")
                else:
                    return inc_result
            except Exception as exc:
                _progress(
                    state,
                    "generate_plan",
                    f"auto patch client construction failed: {exc}; "
                    "falling through to monolithic",
                )

        model = state.get("model", "openai:gpt-4o")
        _progress(state, "generate_plan", f"calling LLM model={model}")
        events = list(state.get("human_loop_events", []))
        if state.get("expert_feedback"):
            events.append(
                {
                    "event": "expert_feedback_consumption_prompt_applied",
                    "round": state.get("expert_round_count", 0),
                    "reason": "generation prompt includes expert feedback consumption rules",
                    "action": "generate_plan",
                }
            )
        investigation_outcome = _run_investigation_safely(
            state,
            phase="generate",
            task_brief=state.get("requirement", ""),
            plan_summary="",
            investigation_llm=investigation_llm,
            retrieval_roots_resolver=retrieval_roots_resolver,
            retrieval_tool_dispatch=retrieval_tool_dispatch,
            retrieval_tool_specs=retrieval_tool_specs,
            investigation_max_iterations=investigation_max_iterations,
            error_catalog_hints=None,
        )
        requirement = _augmented_plan_requirement(state)
        if (
            investigation_outcome is not None
            and investigation_outcome.ok
            and investigation_outcome.findings
        ):
            requirement = (
                f"{requirement}\n\n"
                "Investigation findings (from codebase retrieval; verify before relying on):\n"
                f"{_truncate_text(investigation_outcome.findings, 2000)}\n"
            )
        result = generate_plan(
            requirement=requirement,
            schema=SimulationPlan,
            model=model,
        )
        artifact_paths = _write_plan_generation_artifacts(
            state,
            phase="generate_plan",
            result=result,
            retry_count=state.get("retry_count", 0),
        )
        if not result.ok or result.value is None:
            _progress(state, "generate_plan", f"failed: {result.error}")
            return {
                "simulation_plan": None,
                "simulation_spec": None,
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "candidate_payload": result.candidate_payload,
                "plan_artifacts": artifact_paths,
                "error": result.error or "failed to generate SimulationPlan",
                "human_loop_events": events,
                **_investigation_state_updates(investigation_outcome),
                **_trace_event_update(
                    state,
                    "plan_generated",
                    summary="SimulationPlan generation failed",
                    metadata={"success": False, "error": result.error},
                ),
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
            "candidate_payload": result.candidate_payload,
            "plan_artifacts": _write_final_simulation_plan(
                state,
                result.value,
                existing_paths=artifact_paths,
            ),
            "needs_regeneration": False,
            "expert_feedback_action": "none",
            "human_loop_events": events,
            **_investigation_state_updates(investigation_outcome),
            **_trace_event_update(
                state,
                "plan_generated",
                summary=f"generated SimulationPlan name={_plan_name(result.value)!r}",
                plan=result.value,
                metadata={"success": True},
            ),
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
        artifact_paths = _write_plan_generation_artifacts(
            state,
            phase="repair_plan_format",
            result=result,
            retry_count=retry_count + 1,
        )
        if not result.ok or result.value is None:
            _progress(state, "repair_plan_format", f"failed: {result.error}")
            return {
                "simulation_plan": None,
                "simulation_spec": None,
                "retry_count": retry_count + 1,
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "candidate_payload": result.candidate_payload,
                "plan_artifacts": artifact_paths,
                "error": result.error or "failed to repair SimulationPlan JSON format",
            }
        _progress(state, "repair_plan_format", "format repair produced a SimulationPlan")
        return {
            "simulation_plan": result.value,
            "simulation_spec": result.value.model_spec,
            "retry_count": retry_count + 1,
            "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
            "candidate_payload": result.candidate_payload,
            "plan_artifacts": _write_final_simulation_plan(
                state,
                result.value,
                existing_paths=artifact_paths,
            ),
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
                **_trace_event_update(
                    state,
                    "validation_completed",
                    summary=f"SimulationSpec validation failed with {len(report.errors)} error(s)",
                    report=report,
                ),
            }
        _progress(state, "validate_spec", "passed")
        return {
            "validation_report": report,
            "retry_history": history,
            "error": "",
            **_trace_event_update(
                state,
                "validation_completed",
                summary="SimulationSpec validation passed",
                report=report,
            ),
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
            report = validate_simulation_plan(
                plan, requirement=state.get("requirement", "")
            )

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
            invalid_plan = _plan_with_validation_failure_capability(plan, report)
            state_plan = (
                invalid_plan
                if retry_count >= max_retries
                else plan
            )
            artifact_paths = (
                _write_final_simulation_plan(state, invalid_plan)
                if invalid_plan is not None
                else state.get("plan_artifacts", [])
            )
            updates: GraphState = {
                "simulation_plan": state_plan,
                "simulation_spec": state_plan.model_spec if state_plan is not None else None,
                "validation_report": report,
                "retry_history": history,
                "error": "; ".join(report.errors),
                "pin_count_mismatch_context": _pin_count_mismatch_context(
                    {**state, "validation_report": report}
                ),
                "plan_artifacts": artifact_paths,
                **_trace_event_update(
                    state,
                    "validation_completed",
                    summary=f"SimulationPlan validation failed with {len(report.errors)} error(s)",
                    report=report,
                    plan=invalid_plan or plan,
                ),
            }
            if plan is None and _plan_generation_needs_expert_question(report.errors):
                updates["pending_expert_questions"] = _plan_generation_expert_questions(
                    report.errors,
                    state.get("raw_llm_outputs", []),
                )
            return updates
        _progress(state, "validate_plan", "passed")
        artifact_paths = (
            _write_final_simulation_plan(state, plan)
            if plan is not None
            else state.get("plan_artifacts", [])
        )
        return {
            "validation_report": report,
            "retry_history": history,
            "simulation_plan": plan,
            "simulation_spec": plan.model_spec if plan is not None else None,
            "plan_artifacts": artifact_paths,
            "error": "",
            **_trace_event_update(
                state,
                "validation_completed",
                summary="SimulationPlan validation passed",
                report=report,
                plan=plan,
            ),
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
        if state.get("expert_feedback_action") == "classify":
            return "classify"
        return _make_plan_capability_router()(state)

    return _route


def _make_expert_feedback_action_router():
    def _route(state: GraphState) -> str:
        action = state.get("expert_feedback_action", "none")
        if action == "patch_plan":
            return "patch"
        if action == "regenerate_plan" or state.get("needs_regeneration"):
            return "generate"
        return _make_plan_capability_router()(state)

    return _route


def _make_plan_patch_router():
    def _route(state: GraphState) -> str:
        if state.get("expert_feedback_action") == "regenerate_plan" or state.get("patch_error"):
            return "generate"
        return "validate"

    return _route


def _make_plan_execution_router(max_retries: int):
    def _route(state: GraphState) -> str:
        report = state.get("validation_report")
        if report is not None and report.is_valid:
            return "save"
        if report is not None and _report_should_ask_expert(report):
            return "ask"
        if report is not None and not _report_should_reflect(report):
            return "save"
        if (
            _coerce_simulation_plan(state.get("simulation_plan")) is not None
            and state.get("retry_count", 0) < max_retries
        ):
            return "reflect"
        return "save"

    return _route


def _make_plan_capability_assessment_router():
    def _route(state: GraphState) -> str:
        # assess_capability injects validation errors for LLM-fixable structural
        # defects (missing-cell references, pin-count mismatches, bad radii). Send
        # those back to reflect_plan; material confirmations and clean plans
        # proceed to ask_expert, which is a no-op when there is nothing to ask.
        report = state.get("validation_report")
        if (
            report is not None
            and not report.is_valid
            and state.get("capability_repair_errors")
            and _coerce_simulation_plan(state.get("simulation_plan")) is not None
        ):
            return "reflect"
        return "ask"

    return _route


def _make_assess_plan_capability_node(max_retries: int):
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

        # Structural plan defects (a universe pointing at a missing cell, a pin-count
        # mismatch, a bad radius) are LLM typos, not missing expert facts. Route them
        # back to the LLM via reflect_plan instead of asking the expert, who can only
        # supply material values. Injecting them as validation errors makes the existing
        # reflect_plan path fire automatically; the assess_capability conditional edge
        # then picks reflect vs ask_expert based on retry_count.
        events = list(state.get("human_loop_events", []))
        retry_count = state.get("retry_count", 0)
        repair_issues: list[ValidationIssue] = (
            _capability_self_repair_errors(capability)
            if capability.renderability in {"none", "skeleton"}
            else []
        )
        repair_errors = [issue.message for issue in repair_issues]
        existing_issues = list(report.issues)
        deterministic_repair_available = bool(
            repair_issues
            and auto_repair_lattice_structure(
                updated_plan,
                repair_issues,
                requirement=state.get("requirement", ""),
            )
        )
        inject_invalid = bool(repair_errors) and (
            retry_count < max_retries or deterministic_repair_available
        )
        if inject_invalid:
            events.append(
                {
                    "event": "capability_structure_errors_delegated_to_reflect",
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                    "errors": repair_errors,
                    "reason": (
                        "structural plan defects are agent-fixable; routing to reflect_plan "
                        "instead of asking the expert"
                    ),
                }
            )
            updated_report = ValidationReport(
                is_valid=False,
                errors=list(repair_errors),
                warnings=warnings,
                suggestions=suggestions,
                issues=[*existing_issues, *repair_issues],
            )
        else:
            updated_report = ValidationReport(
                is_valid=report.is_valid,
                errors=report.errors,
                warnings=warnings,
                suggestions=suggestions,
                issues=existing_issues,
            )

        _progress(
            state,
            "assess_capability",
            (
                f"renderer={capability.supported_renderer} "
                f"renderability={capability.renderability}"
                + (f"; {len(repair_errors)} self-repair error(s)" if repair_errors else "")
            ),
        )
        return {
            "simulation_plan": updated_plan,
            "simulation_spec": updated_plan.model_spec,
            "validation_report": updated_report,
            "capability_repair_errors": repair_errors,
            "human_loop_events": events,
            "plan_artifacts": _write_final_simulation_plan(state, updated_plan),
            **_trace_event_update(
                state,
                "capability_assessed",
                summary=(
                    f"renderer={capability.supported_renderer} "
                    f"renderability={capability.renderability}"
                ),
                report=updated_report,
                capability=capability,
                plan=updated_plan,
                metadata={
                    "unsupported_subsystems": capability.unsupported_subsystems,
                    "required_human_confirmations": capability.required_human_confirmations,
                    "self_repair_error_count": len(repair_errors),
                },
            ),
        }

    return _assess_plan_capability


def _ask_expert(state: GraphState) -> GraphState:
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    plan_update = {"simulation_plan": plan} if plan is not None else {}
    questions = _pending_expert_questions(state)
    max_rounds = state.get("max_expert_rounds", 0)
    round_count = state.get("expert_round_count", 0)
    events = list(state.get("human_loop_events", []))

    if not questions:
        trace_update = _trace_event_update(
            state,
            "ask_expert_completed",
            summary="no expert questions pending",
            metadata={"question_count": 0, "feedback_present": False},
        )
        return {
            **plan_update,
            "pending_expert_questions": [],
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
            "expert_feedback_action": "none",
            "human_loop_events": state.get("human_loop_events", []),
            **trace_update,
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
        start_update = _trace_event_update(
            state,
            "ask_expert_started",
            summary=f"{len(questions)} expert question(s) pending",
            metadata={
                "question_count": len(questions),
                "questions": questions,
                "feedback_present": False,
            },
        )
        completed_update = _trace_event_update(
            {**state, **start_update},
            "ask_expert_completed",
            summary="expert questions not asked; max rounds reached or disabled",
            metadata={
                "question_count": len(questions),
                "questions": questions,
                "feedback_present": False,
                "requires_human_confirmation_count": len(questions),
            },
        )
        return {
            **plan_update,
            "pending_expert_questions": questions,
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
            "expert_feedback_action": "none",
            "human_loop_events": events,
            **completed_update,
        }

    prompt_round = round_count + 1
    _progress(
        state,
        "ask_expert",
        f"interrupting for expert feedback round {prompt_round}/{max_rounds}",
    )
    start_update = _trace_event_update(
        state,
        "ask_expert_started",
        summary=f"asking {len(questions)} expert question(s)",
        metadata={"question_count": len(questions), "questions": questions},
        round_index=prompt_round,
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
    resolved_items = _update_resolved_expert_items_from_feedback(
        state=state,
        questions=questions,
        feedback_items=feedback_items,
        round_index=prompt_round,
    )
    if resolved_items:
        events.append(
            {
                "event": "expert_feedback_resolved_items_extracted",
                "round": prompt_round,
                "items": resolved_items,
                "reason": "bound current pending questions to the expert resume payload",
            }
        )

    if not should_continue or not feedback_items:
        completed_update = _trace_event_update(
            {**state, **start_update},
            "ask_expert_completed",
            summary="expert feedback was empty or deferred",
            metadata={
                "question_count": len(questions),
                "questions": questions,
                "feedback_present": bool(feedback_items),
                "requires_human_confirmation_count": len(questions),
            },
            round_index=prompt_round,
        )
        return {
            **plan_update,
            "pending_expert_questions": questions,
            "expert_round_count": prompt_round,
            "awaiting_expert_feedback": False,
            "needs_regeneration": False,
            "expert_feedback_action": "continue",
            "resolved_expert_items": resolved_items,
            "human_loop_events": events,
            **completed_update,
        }

    feedback = list(state.get("expert_feedback", []))
    feedback.extend(feedback_items)
    completed_update = _trace_event_update(
        {**state, **start_update},
        "ask_expert_completed",
        summary=f"received {len(feedback_items)} expert feedback item(s)",
        metadata={
            "question_count": len(questions),
            "questions": questions,
            "feedback_present": True,
            "feedback_count": len(feedback_items),
        },
        round_index=prompt_round,
    )
    return {
        **plan_update,
        "expert_feedback": feedback,
        "pending_expert_questions": [],
        "expert_round_count": prompt_round,
        "awaiting_expert_feedback": False,
        "needs_regeneration": False,
        "expert_feedback_action": "classify",
        "resolved_expert_items": resolved_items,
        "human_loop_events": events,
        "error": "",
        **completed_update,
    }


def _classify_expert_feedback(state: GraphState) -> GraphState:
    feedback = _latest_expert_feedback(state)
    events = list(state.get("human_loop_events", []))
    round_index = state.get("expert_round_count", 0)
    if not feedback:
        action = "continue"
        reason = "empty or missing expert feedback"
        confidence = "high"
    else:
        action, reason, confidence = _classify_feedback_text(
            feedback,
            _coerce_simulation_plan(state.get("simulation_plan")),
        )

    event = {
        "event": "expert_feedback_classified",
        "round": round_index,
        "action": action,
        "reason": reason,
        "confidence": confidence,
        "feedback": feedback,
    }
    events.append(event)
    if action == "regenerate_plan":
        events.append(
            {
                "event": "expert_feedback_regeneration_selected",
                "round": round_index,
                "action": action,
                "reason": reason,
                "confidence": confidence,
            }
        )
    elif action == "continue":
        events.append(
            {
                "event": "expert_feedback_continue_selected",
                "round": round_index,
                "action": action,
                "reason": reason,
                "confidence": confidence,
            }
        )

    return {
        "expert_feedback_action": action,
        "expert_feedback_interpretation": reason,
        "patch_confidence": confidence,
        "patch_reason": reason,
        "needs_regeneration": action == "regenerate_plan",
        "human_loop_events": events,
    }


def _patch_plan_from_expert_feedback(state: GraphState) -> GraphState:
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    feedback = _latest_expert_feedback(state)
    events = list(state.get("human_loop_events", []))
    round_index = state.get("expert_round_count", 0)
    if plan is None:
        error = "cannot patch because SimulationPlan is missing"
        events.extend(
            [
                {
                    "event": "plan_patch_failed",
                    "round": round_index,
                    "error": error,
                    "reason": error,
                },
                {
                    "event": "patch_failed_fallback_to_regeneration",
                    "round": round_index,
                    "reason": error,
                    "action": "regenerate_plan",
                },
            ]
        )
        return {
            "patch_error": error,
            "expert_feedback_action": "regenerate_plan",
            "needs_regeneration": True,
            "human_loop_events": events,
        }

    patches, reason, confidence = _build_plan_patches(plan, feedback, state)
    events.append(
        {
            "event": "plan_patch_generated",
            "round": round_index,
            "reason": reason,
            "action": "patch_plan",
            "confidence": confidence,
            "patch": patches,
        }
    )
    if not patches:
        error = "expert feedback could not be mapped to a safe SimulationPlan field"
        events.extend(
            [
                {
                    "event": "plan_patch_failed",
                    "round": round_index,
                    "reason": reason,
                    "error": error,
                },
                {
                    "event": "patch_failed_fallback_to_regeneration",
                    "round": round_index,
                    "reason": error,
                    "action": "regenerate_plan",
                },
            ]
        )
        return {
            "plan_patch": patches,
            "patch_confidence": confidence,
            "patch_reason": reason,
            "patch_error": error,
            "expert_feedback_action": "regenerate_plan",
            "needs_regeneration": True,
            "human_loop_events": events,
        }

    try:
        patched_payload = _apply_json_patches(plan.model_dump(mode="json"), patches)
        patched_payload = _normalize_capability_report_for_plan_validation(patched_payload)
        patched_plan = SimulationPlan.model_validate(patched_payload)
    except Exception as exc:
        error = str(exc)
        events.extend(
            [
                {
                    "event": "plan_patch_failed",
                    "round": round_index,
                    "reason": reason,
                    "error": error,
                    "patch": patches,
                },
                {
                    "event": "patch_failed_fallback_to_regeneration",
                    "round": round_index,
                    "reason": "patched plan failed schema validation",
                    "action": "regenerate_plan",
                },
            ]
        )
        return {
            "plan_patch": patches,
            "patch_confidence": confidence,
            "patch_reason": reason,
            "patch_error": error,
            "expert_feedback_action": "regenerate_plan",
            "needs_regeneration": True,
            "human_loop_events": events,
        }

    events.append(
        {
            "event": "plan_patch_applied",
            "round": round_index,
            "reason": reason,
            "action": "patch_plan",
            "confidence": confidence,
            "patch": patches,
        }
    )
    return {
        "simulation_plan": patched_plan,
        "simulation_spec": patched_plan.model_spec,
        "plan_patch": patches,
        "patch_confidence": confidence,
        "patch_reason": reason,
        "patch_error": "",
        "expert_feedback_action": "none",
        "needs_regeneration": False,
        "human_loop_events": events,
        "plan_artifacts": _write_final_simulation_plan(state, patched_plan),
        "error": "",
    }


def _render_script(state: GraphState) -> GraphState:
    _progress(state, "render_script", "rendering OpenMC Python model.py")
    start_update = _trace_event_update(
        state,
        "render_started",
        summary="rendering SimulationSpec to model.py",
    )
    report = state.get("validation_report")
    spec = state.get("simulation_spec")
    if spec is None or report is None or not report.is_valid:
        return start_update

    script = render_openmc_script(spec)
    script_report = validate_openmc_script(script, spec)
    if not script_report.is_valid:
        _progress(state, "render_script", f"failed script validation: {script_report.errors}")
        completed_update = _trace_event_update(
            {**state, **start_update},
            "render_completed",
            summary="SimulationSpec render failed script validation",
            report=script_report,
            metadata={"success": False},
        )
        return {
            "validation_report": script_report,
            "error": "; ".join(script_report.errors),
            **completed_update,
        }

    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    _clean_stale_render_artifacts(output_dir)
    model_path = output_dir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    _progress(state, "render_script", f"wrote {model_path}")
    completed_update = _trace_event_update(
        {**state, **start_update},
        "render_completed",
        summary=f"wrote {model_path}",
        report=report,
        metadata={"success": True, "model_path": str(model_path)},
    )
    return {"script": script, "model_path": str(model_path), **completed_update}


def _render_plan_script(state: GraphState) -> GraphState:
    _progress(state, "render_plan_script", "rendering OpenMC Python model.py from SimulationPlan")
    start_update = _trace_event_update(
        state,
        "render_started",
        summary="rendering SimulationPlan to model.py",
    )
    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    # Always start from a clean render-output set so a non-exportable run never
    # leaves a prior run's model.py / XML / optimistic capability_report.json.
    _clean_stale_render_artifacts(output_dir)

    report = state.get("validation_report")
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    if plan is None or report is None or not report.is_valid:
        _progress(state, "render_plan_script", "skipped: plan is not valid")
        _write_non_executable_marker(output_dir, report, plan)
        return start_update

    renderer, capability = choose_renderer(plan)
    if renderer is None or capability.renderability == "none":
        _progress(state, "render_plan_script", "skipped: no renderer for this plan")
        _write_non_executable_marker(output_dir, report, plan, capability)
        completed_update = _trace_event_update(
            {**state, **start_update},
            "render_completed",
            summary="render skipped: no renderer",
            capability=capability,
            metadata={"success": False, "reason": "no renderer"},
        )
        return {
            "simulation_plan": plan,
            "plan_artifacts": _write_final_simulation_plan(state, plan),
            **completed_update,
        }

    result = renderer.render(plan, output_dir)
    # Rendering may defensively downgrade an apparently exportable plan to a
    # skeleton if executor/script validation fails. The render result is the
    # final authority for sidecars and downstream tool execution.
    plan = plan.model_copy(update={"capability_report": result.capability})
    _write_capability_sidecar(output_dir, result.capability)

    if result.errors:
        _progress(state, "render_plan_script", f"renderer failed: {result.errors}")
        result_report = ValidationReport(
            is_valid=False,
            errors=result.errors,
        )
        completed_update = _trace_event_update(
            {**state, **start_update},
            "render_completed",
            summary="renderer failed",
            report=result_report,
            capability=result.capability,
            plan=plan,
            metadata={
                "success": False,
                "renderer": result.renderer_name,
                "errors": result.errors,
            },
        )
        return {
            "simulation_plan": plan,
            "simulation_spec": plan.model_spec,
            "validation_report": result_report,
            "plan_artifacts": _write_final_simulation_plan(state, plan),
            "error": "; ".join(result.errors),
            **completed_update,
        }

    model_path = output_dir / "model.py"
    _progress(
        state,
        "render_plan_script",
        f"wrote {model_path} (renderer={result.renderer_name}, "
        f"renderability={result.renderability})",
    )
    completed_update = _trace_event_update(
        {**state, **start_update},
        "render_completed",
        summary=(
            f"wrote {model_path} with renderer={result.renderer_name} "
            f"renderability={result.renderability}"
        ),
        report=report,
        capability=result.capability,
        plan=plan,
        metadata={
            "success": True,
            "renderer": result.renderer_name,
            "model_path": str(model_path),
        },
    )
    return {
        "simulation_plan": plan,
        "simulation_spec": plan.model_spec,
        "script": result.script,
        "model_path": str(model_path),
        "plan_artifacts": _write_final_simulation_plan(state, plan),
        **completed_update,
    }


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
                **_trace_event_update(
                    state,
                    "export_xml_completed",
                    summary="export skipped because model is not executable",
                    report=report,
                    capability=plan.capability_report,
                    metadata={"success": False, "skipped": True, "renderability": renderability},
                ),
            }

        _progress(state, "execute_tools", "running export_xml")
        export_result = export_xml_tool(Path(model_path))
        results.append(export_result)
        _progress(state, "execute_tools", f"export_xml ok={export_result.ok}")
        trace_update = _trace_event_update(
            state,
            "export_xml_completed",
            summary=f"export_xml ok={export_result.ok}",
            report=ValidationReport.from_issues(
                export_result.issues,
                is_valid=export_result.ok,
            ),
            capability=plan.capability_report,
            metadata={
                "success": export_result.ok,
                "returncode": export_result.returncode,
                "error": export_result.error,
                "artifacts": export_result.artifacts,
            },
        )

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
            # Pre-flight source/settings check: do not waste a smoke run (and
            # risk a segfault cascade) when the source box provably misses the
            # active fuel region or the fuel is non-fissionable.
            from openmc_agent.source_settings import (
                source_bounds_for_plan,
                validate_source_settings,
            )
            from openmc_agent.geometry_bounds import validate_bounds_consistency

            source_issues = validate_source_settings(plan)
            if plan.complex_model is not None:
                src = source_bounds_for_plan(plan.complex_model)
                if src is not None:
                    src_tuple = (src.x_min, src.x_max, src.y_min, src.y_max, src.z_min, src.z_max)
                    source_issues.extend(validate_bounds_consistency(
                        plan.complex_model, source_bounds=src_tuple,
                        plot_bounds=_plot_bounds_metadata(plan),
                    ))
            blocking_source = [i for i in source_issues if i.severity == "error"]
            if blocking_source:
                _progress(
                    state,
                    "execute_tools",
                    f"skipping run_smoke_test: {len(blocking_source)} blocking source issue(s)",
                )
                source_report = ValidationReport.from_issues(source_issues, is_valid=False)
                trace_update = _trace_event_update(
                    {**state, **trace_update},
                    "smoke_test_completed",
                    summary="run_smoke_test skipped: source/settings pre-flight failed",
                    report=source_report,
                    metadata={
                        "success": False,
                        "skipped": True,
                        "source_issue_codes": [i.code for i in blocking_source],
                    },
                )
                return {
                    "validation_report": source_report,
                    "tool_results": [result.model_dump() for result in results],
                    **trace_update,
                }

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
            smoke_result = results[-1]
            trace_update = _trace_event_update(
                {**state, **trace_update},
                "smoke_test_completed",
                summary=f"run_smoke_test ok={smoke_result.ok}",
                report=ValidationReport.from_issues(
                    smoke_result.issues,
                    is_valid=smoke_result.ok,
                ),
                capability=plan.capability_report,
                metadata={
                    "success": smoke_result.ok,
                    "returncode": smoke_result.returncode,
                    "error": smoke_result.error,
                },
            )
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
            **trace_update,
        }

    return _execute_tools


# Number of consecutive patch failures (auto-repair or investigation) after which
# reflect_plan stops trying patches and regenerates the whole SimulationPlan.
PATCH_FALLBACK_THRESHOLD = 2


def _make_reflect_plan_node(
    repair_plan: RepairPlanFn,
    *,
    investigation_llm: InvestigationLlmFn | None = None,
    retrieval_roots_resolver: RetrievalRootsResolver | None = None,
    retrieval_tool_dispatch: dict[str, Callable[..., ToolResult]] | None = None,
    retrieval_tool_specs: list[ToolSpec] | None = None,
    investigation_max_iterations: int = 4,
    retrieval_policy: RetrievalPolicy | None = None,
):
    def _reflect_plan(state: GraphState) -> GraphState:
        plan = _coerce_simulation_plan(state.get("simulation_plan"))
        report = state.get("validation_report")
        retry_count = state.get("retry_count", 0)
        patch_failures = state.get("patch_failure_count", 0)
        if plan is None or report is None or report.is_valid:
            return {"retry_count": retry_count, "patch_failure_count": patch_failures}

        reflect_start_update = _trace_event_update(
            state,
            "reflect_plan_started",
            summary="reflect_plan handling invalid SimulationPlan",
            report=report,
            plan=plan,
            metadata={"llm_called": False, "patch_failure_count": patch_failures},
        )
        trace_state: GraphState = {**state, **reflect_start_update}

        # Patch sources, in priority order: (1) deterministic auto-repair of
        # uniquely-solvable id-reference typos -- no LLM call; (2) an
        # investigation patch from the retrieval loop. Whole-plan regeneration
        # below is the last resort, used only when no patch applies or recent
        # patches keep failing to validate.
        auto_patch: list[dict[str, Any]] | None = None
        if patch_failures < PATCH_FALLBACK_THRESHOLD:
            auto_attempt_update = _trace_event_update(
                trace_state,
                "auto_repair_attempted",
                summary="attempting deterministic lattice auto-repair",
                report=report,
                plan=plan,
                metadata={
                    "attempted_issue_codes": [
                        issue.code for issue in [*plan.capability_report.issues, *report.issues]
                    ],
                    "patch_failure_count": patch_failures,
                },
            )
            trace_state = {**trace_state, **auto_attempt_update}
            auto_patch = auto_repair_lattice_structure(
                plan,
                issues=[*plan.capability_report.issues, *report.issues],
                requirement=state.get("requirement", ""),
            )

        if auto_patch is not None and patch_failures < PATCH_FALLBACK_THRESHOLD:
            try:
                patched_payload = _apply_json_patches(
                    plan.model_dump(mode="json"), auto_patch
                )
                patched_payload = _normalize_capability_report_for_plan_validation(
                    patched_payload
                )
                patched_plan = SimulationPlan.model_validate(patched_payload)
                _progress(
                    state,
                    "reflect_plan",
                    f"applied {len(auto_patch)} patch op(s) via deterministic auto-repair",
                )
                auto_completed_update = _trace_event_update(
                    trace_state,
                    "auto_repair_completed",
                    summary="deterministic auto-repair succeeded",
                    report=report,
                    plan=patched_plan,
                    metadata={
                        "success": True,
                        "patch_count": len(auto_patch),
                        "changed_paths": [patch.get("path") for patch in auto_patch],
                    },
                )
                reflect_completed_update = _trace_event_update(
                    {**trace_state, **auto_completed_update},
                    "reflect_plan_completed",
                    summary="reflect_plan completed via deterministic auto-repair without LLM",
                    report=report,
                    plan=patched_plan,
                    metadata={
                        "llm_called": False,
                        "plan_changed": True,
                        "patch_count": len(auto_patch),
                    },
                )
                return {
                    "simulation_plan": patched_plan,
                    "simulation_spec": patched_plan.model_spec,
                    "retry_count": retry_count + 1,
                    "plan_patch": auto_patch,
                    "patch_confidence": "high",
                    "patch_reason": "deterministic auto-repair",
                    "patch_error": "",
                    "error": "",
                    "patch_failure_count": patch_failures,
                    "plan_artifacts": _write_final_simulation_plan(state, patched_plan),
                    **reflect_completed_update,
                }
            except Exception as exc:
                patch_failures += 1
                _progress(
                    state,
                    "reflect_plan",
                    f"deterministic auto-repair patch failed: {exc}; "
                    f"patch_failure_count={patch_failures}",
                )
                auto_completed_update = _trace_event_update(
                    trace_state,
                    "auto_repair_completed",
                    summary="deterministic auto-repair failed to apply",
                    report=report,
                    plan=plan,
                    metadata={
                        "success": False,
                        "patch_count": len(auto_patch or []),
                        "failure_reason": str(exc),
                    },
                )
                trace_state = {**trace_state, **auto_completed_update}

        retrieval_started_update = _trace_event_update(
            trace_state,
            "retrieval_started",
            summary="running retrieval orchestrator for reflect_plan",
            report=report,
            plan=plan,
            metadata={"policy": "default", "issue_count": len(report.issues)},
        )
        trace_state = {**trace_state, **retrieval_started_update}
        retrieval_context = _retrieval_context_for_report(report, policy=retrieval_policy)
        kg_summary = retrieval_context.knowledge_graph_summary or {}
        retrieval_completed_update = _trace_event_update(
            trace_state,
            "retrieval_completed",
            summary=retrieval_context.summary or "retrieval completed",
            report=report,
            retrieval_context=retrieval_context,
            plan=plan,
            metadata={
                "warnings": retrieval_context.warnings,
                "knowledge_graph_attempted": bool(kg_summary.get("attempted", False)),
                "knowledge_graph_loaded": bool(kg_summary.get("loaded", False)),
                "knowledge_graph_node_count": int(kg_summary.get("node_count", 0) or 0),
                "knowledge_graph_edge_count": int(kg_summary.get("edge_count", 0) or 0),
                "knowledge_graph_warning_count": len(
                    retrieval_context.knowledge_graph_warnings
                ),
            },
        )
        trace_state = {**trace_state, **retrieval_completed_update}
        state_with_evidence: GraphState = {
            **state,
            **trace_state,
            **_retrieval_state_updates(retrieval_context),
        }
        base_requirement = _build_reflection_requirement(state_with_evidence)
        plan_summary = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
        hints = _error_catalog_hints_for(report.errors)
        investigation_outcome = _run_investigation_safely(
            state_with_evidence,
            phase="reflect",
            task_brief=base_requirement,
            plan_summary=_truncate_text(plan_summary, 4000),
            investigation_llm=investigation_llm,
            retrieval_roots_resolver=retrieval_roots_resolver,
            retrieval_tool_dispatch=retrieval_tool_dispatch,
            retrieval_tool_specs=retrieval_tool_specs,
            investigation_max_iterations=investigation_max_iterations,
            error_catalog_hints=hints,
        )

        investigation_patch = (
            investigation_outcome.patch
            if investigation_outcome is not None
            and investigation_outcome.ok
            and investigation_outcome.patch
            else None
        )
        candidate_patch = investigation_patch

        if candidate_patch is not None and patch_failures < PATCH_FALLBACK_THRESHOLD:
            patch_source = "investigation"
            try:
                patched_payload = _apply_json_patches(
                    plan.model_dump(mode="json"), candidate_patch
                )
                patched_payload = _normalize_capability_report_for_plan_validation(
                    patched_payload
                )
                patched_plan = SimulationPlan.model_validate(patched_payload)
                _progress(
                    state,
                    "reflect_plan",
                    f"applied {len(candidate_patch)} patch op(s) via {patch_source}",
                )
                reflect_completed_update = _trace_event_update(
                    state_with_evidence,
                    "reflect_plan_completed",
                    summary=f"reflect_plan completed via {patch_source} patch",
                    report=report,
                    retrieval_context=retrieval_context,
                    plan=patched_plan,
                    metadata={
                        "llm_called": False,
                        "plan_changed": True,
                        "patch_count": len(candidate_patch),
                        "changed_paths": [patch.get("path") for patch in candidate_patch],
                    },
                )
                return {
                    "simulation_plan": patched_plan,
                    "simulation_spec": patched_plan.model_spec,
                    "retry_count": retry_count + 1,
                    "plan_patch": candidate_patch,
                    "patch_confidence": "high",
                    "patch_reason": (
                        _truncate_text(investigation_outcome.findings, 500)
                        or "investigation patch"
                    ),
                    "patch_error": "",
                    "error": "",
                    "patch_failure_count": patch_failures,
                    "plan_artifacts": _write_final_simulation_plan(state, patched_plan),
                    **_retrieval_state_updates(retrieval_context),
                    **_investigation_state_updates(investigation_outcome),
                    **reflect_completed_update,
                }
            except Exception as exc:
                patch_failures += 1
                _progress(
                    state,
                    "reflect_plan",
                    f"{patch_source} patch failed: {exc}; "
                    f"patch_failure_count={patch_failures}",
                )

        # Fallback: regenerate the whole plan, enriched by findings when available.
        reflection_requirement = base_requirement
        if patch_failures > 0:
            reflection_requirement += (
                "\n\nNote: deterministic / investigation patches were attempted but "
                "did not validate. Carefully re-check id references (cell.fill_id, "
                "universe.cell_ids, lattice.universe_pattern, region.surface_ids, "
                "core.axial_layers.fill.id, core.axial_layers.loading_id, "
                "lattice_loadings.base_lattice_id) "
                "against the defined ids rather than guessing.\n"
            )
        if (
            investigation_outcome is not None
            and investigation_outcome.ok
            and investigation_outcome.findings
        ):
            reflection_requirement = (
                f"{reflection_requirement}\n\n"
                "Investigation findings (verified against codebase):\n"
                f"{_truncate_text(investigation_outcome.findings, 2000)}\n"
            )
        _progress(
            state,
            "reflect_plan",
            f"calling LLM reflection retry={retry_count + 1}, "
            f"patch_failures={patch_failures}",
        )
        result = repair_plan(
            requirement=reflection_requirement,
            schema=SimulationPlan,
            model=state.get("model", "openai:gpt-4o"),
            previous_spec=plan,
            validation_errors=report.errors,
        )
        artifact_paths = _write_plan_generation_artifacts(
            state,
            phase="reflect_plan",
            result=result,
            retry_count=retry_count + 1,
        )
        if not result.ok or result.value is None:
            _progress(state, "reflect_plan", f"failed: {result.error}")
            reflect_completed_update = _trace_event_update(
                state_with_evidence,
                "reflect_plan_completed",
                summary="LLM reflection failed",
                report=report,
                retrieval_context=retrieval_context,
                plan=plan,
                metadata={
                    "llm_called": True,
                    "plan_changed": False,
                    "failure_reason": result.error,
                },
            )
            return {
                "retry_count": retry_count + 1,
                "patch_failure_count": patch_failures,
                **_retrieval_state_updates(retrieval_context),
                "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
                "plan_artifacts": artifact_paths,
                "error": result.error or "failed to repair SimulationPlan",
                **_investigation_state_updates(investigation_outcome),
                **reflect_completed_update,
            }
        _progress(state, "reflect_plan", "reflection produced a new SimulationPlan")
        reflect_completed_update = _trace_event_update(
            state_with_evidence,
            "reflect_plan_completed",
            summary="LLM reflection produced a new SimulationPlan",
            report=report,
            retrieval_context=retrieval_context,
            plan=result.value,
            metadata={"llm_called": True, "plan_changed": True},
        )
        return {
            "simulation_plan": result.value,
            "simulation_spec": result.value.model_spec,
            "retry_count": retry_count + 1,
            "patch_failure_count": patch_failures,
            **_retrieval_state_updates(retrieval_context),
            "raw_llm_outputs": _append_raw_llm_output(state, result.raw_response),
            "plan_artifacts": _write_final_simulation_plan(
                state,
                result.value,
                existing_paths=artifact_paths,
            ),
            "error": "",
            **_investigation_state_updates(investigation_outcome),
            **reflect_completed_update,
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
        investigation_trace=state.get("investigation_trace", []),
        plan_artifacts=state.get("plan_artifacts", []),
    )
    _progress(state, "save_record", "record saved")
    return _trace_event_update(
        state,
        "workflow_failed" if state.get("error") else "workflow_completed",
        summary="SimulationSpec workflow saved",
        report=report,
        metadata={
            "model_path": state.get("model_path"),
            "retry_count": state.get("retry_count", 0),
        },
    )


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
        investigation_trace=state.get("investigation_trace", []),
        plan_artifacts=state.get("plan_artifacts", []),
    )
    _progress(state, "save_record", "record saved")
    return _trace_event_update(
        state,
        "workflow_failed" if state.get("error") else "workflow_completed",
        summary="SimulationPlan workflow saved",
        report=report,
        plan=plan,
        metadata={
            "model_path": state.get("model_path"),
            "retry_count": state.get("retry_count", 0),
            "pending_expert_questions": state.get("pending_expert_questions", []),
        },
    )


def _execution_report_from_tool_results(results: list[ToolResult]) -> ValidationReport:
    issues: list[ValidationIssue] = []
    legacy_errors: list[str] = []
    legacy_warnings: list[str] = []
    for result in results:
        if not result.ok:
            message = result.error or result.stderr or result.stdout or "tool failed"
            legacy_errors.append(f"{result.name} failed: {message}")
            issues.extend(result.issues)
        diagnostics = parse_openmc_output(result.stdout, result.stderr)
        issues.extend(diagnostics.issues)
        legacy_warnings.extend(diagnostics.warnings)
    report = ValidationReport.from_issues(_dedupe_validation_issues(issues))
    return report.model_copy(
        update={
            "is_valid": not (legacy_errors or report.errors),
            "errors": [*legacy_errors, *report.errors],
            "warnings": [*report.warnings, *legacy_warnings],
        }
    )


def _report_should_reflect(report: ValidationReport) -> bool:
    if not report.issues:
        return True
    reflect_routes = {"auto_repair", "reflect_plan", "retrieval"}
    blocking_routes = {"ask_expert", "manual_review", "capability_downgrade"}
    if any(issue.route_hint in reflect_routes for issue in report.issues):
        return True
    if all(issue.route_hint in blocking_routes for issue in report.issues):
        return False
    return True


def _report_should_ask_expert(report: ValidationReport) -> bool:
    return bool(report.issues) and all(
        issue.route_hint == "ask_expert" or issue.requires_human_confirmation
        for issue in report.issues
    )


def _plan_with_validation_failure_capability(
    plan: SimulationPlan | None,
    report: ValidationReport,
) -> SimulationPlan | None:
    if plan is None:
        return None
    existing = plan.capability_report
    supported_renderer = existing.supported_renderer
    if supported_renderer == "none" and plan.complex_model is not None:
        supported_renderer = plan.complex_model.kind if plan.complex_model.kind in {"assembly", "core", "triso"} else "none"
    capability = existing.model_copy(
        update={
            "renderability": "skeleton" if supported_renderer != "none" else "none",
            "is_executable": False,
            "supported_renderer": supported_renderer,
            "executable_subsystems": [],
            "reasons": list(dict.fromkeys([*existing.reasons, *report.errors])),
            "issues": _dedupe_validation_issues([*existing.issues, *report.issues]),
        }
    )
    return plan.model_copy(update={"capability_report": capability})


def _dedupe_validation_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.code, issue.message, issue.schema_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _structured_issue_context(issues: list[ValidationIssue]) -> str:
    if not issues:
        return ""
    payload: list[dict[str, Any]] = []
    for issue in issues[:12]:
        payload.append(
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "schema_path": issue.schema_path,
                "route_hint": issue.route_hint,
                "concept_id": issue.concept_id,
                "grep_patterns": issue.grep_patterns,
                "repair_hints": [
                    {
                        "action": hint.action,
                        "message": hint.message,
                        "target_path": hint.target_path,
                        "example_patch": hint.example_patch,
                    }
                    for hint in issue.repair_hints
                ],
                "requires_retrieval": issue.requires_retrieval,
                "requires_human_confirmation": issue.requires_human_confirmation,
            }
        )
    return (
        "\n[Validation Issues]\n"
        "Use these stable codes and paths; do not guess missing physical facts.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def _retrieval_context_for_report(
    report: ValidationReport | None,
    policy: RetrievalPolicy | None = None,
) -> RetrievalContext:
    if report is None or not report.issues:
        return RetrievalContext()
    return gather_retrieval_context_for_issues(report.issues, policy=policy)


def _retrieval_state_updates(context: RetrievalContext) -> dict[str, Any]:
    graph_context = context.graph_context or GraphContext()
    return {
        "retrieval_context": context.model_dump(mode="json"),
        "retrieval_prompt": format_retrieval_context(context),
        "grep_evidence": [item.model_dump(mode="json") for item in context.grep_evidence],
        "graph_context": graph_context.model_dump(mode="json"),
        "rag_evidence": [item.model_dump(mode="json") for item in context.rag_evidence],
    }


def _coerce_grep_evidence(raw_items: list[Any]) -> list[RetrievedEvidence]:
    evidence: list[RetrievedEvidence] = []
    for raw in raw_items:
        if isinstance(raw, RetrievedEvidence):
            evidence.append(raw)
            continue
        if isinstance(raw, dict):
            try:
                evidence.append(RetrievedEvidence.model_validate(raw))
            except Exception:
                continue
    return evidence


def _coerce_rag_evidence(raw_items: list[Any]) -> list[RetrievedEvidence]:
    return _coerce_grep_evidence(raw_items)


def _coerce_graph_context(raw_item: Any) -> GraphContext:
    if isinstance(raw_item, GraphContext):
        return raw_item
    if isinstance(raw_item, dict):
        try:
            return GraphContext.model_validate(raw_item)
        except Exception:
            return GraphContext()
    return GraphContext()


def _repair_constraints_context() -> str:
    return (
        "\n[Repair Constraints]\n"
        "- Only change fields directly related to the validation issues.\n"
        "- Do not modify confirmed fields or expert feedback unless the issue targets them.\n"
        "- Do not invent material density, nuclide composition, benchmark facts, or cross section paths.\n"
        "- Treat grep evidence as locator context, not as final physics or nuclear-data truth.\n"
        "- Treat graph context as relationship metadata and retrieval routing hints, not as final physics facts.\n"
        "- Use RAG evidence only as documentation context for API usage, syntax, and explanations.\n"
        "- RAG evidence must not be used to invent nuclear data paths, material densities, compositions, or benchmark constants.\n"
        "- If an issue requires human confirmation, preserve that requirement even when RAG evidence explains the topic.\n"
    )


def _build_reflection_requirement(state: GraphState) -> str:
    tool_results = state.get("tool_results", [])
    expert_feedback = state.get("expert_feedback", [])
    repair_errors = state.get("capability_repair_errors", [])
    report = state.get("validation_report")
    issue_context = _structured_issue_context(report.issues if report else [])
    retrieval_prompt = state.get("retrieval_prompt") or format_retrieval_context(
        retrieval_context_from_raw(state.get("retrieval_context"))
    )
    if not retrieval_prompt:
        retrieval_prompt = format_retrieval_context(
            RetrievalContext(
                grep_evidence=_coerce_grep_evidence(state.get("grep_evidence", [])),
                graph_context=_coerce_graph_context(state.get("graph_context")),
                rag_evidence=_coerce_rag_evidence(state.get("rag_evidence", [])),
            )
        )
    structure_guidance = ""
    if repair_errors:
        structure_guidance = (
            "\nThe plan has STRUCTURAL defects the LLM must fix itself -- these are NOT "
            "questions for a human expert. Fix reference consistency and counts in the JSON:\n"
            "- Every universe.cell_ids / control-rod / reflector id reference must match an "
            "existing cell/material/region id. Rename the reference or add the missing object "
            "with that exact id.\n"
            "- A lattice pin-count mismatch means fill_universe + overrides positions do not "
            "sum to expected_counts; recompute the override (row, col) positions so each "
            "universe count matches expected_counts exactly.\n"
            "- Keep material values, geometry dimensions, and physical facts unchanged.\n"
            f"Structural errors to fix now: {repair_errors}\n"
        )
    pin_count_context = _pin_count_mismatch_context(state)
    return (
        f"{state.get('requirement', '')}\n\n"
        "The current SimulationPlan failed during OpenMC expert-style execution checks. "
        "Return a corrected SimulationPlan JSON object only. Do not modify Python code directly.\n"
        f"{structure_guidance}"
        f"{_hard_count_constraints_context(state)}"
        f"{pin_count_context}"
        f"{issue_context}"
        f"{retrieval_prompt}"
        f"{_repair_constraints_context()}"
        f"Validation and execution errors: {state.get('error', '')}\n"
        f"Tool results: {_compact_tool_results(tool_results)}\n"
        f"Human expert feedback: {expert_feedback}"
    )


def _build_format_repair_requirement(state: GraphState) -> str:
    raw_outputs = state.get("raw_llm_outputs", [])
    latest_raw = raw_outputs[-1] if raw_outputs else ""
    candidate_payload = state.get("candidate_payload")
    candidate_context = _candidate_payload_context(candidate_payload)
    schema_guidance = _format_repair_schema_guidance(
        state.get("error", ""),
        candidate_payload,
    )
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
        f"{schema_guidance}"
        f"{truncation_guidance}"
        f"Parse/validation error: {state.get('error', '')}\n"
        f"{candidate_context}"
        f"Previous raw response: {_truncate_text(latest_raw, 4000)}"
    )


def _candidate_payload_context(candidate_payload: Any) -> str:
    if not isinstance(candidate_payload, dict):
        return ""
    return (
        "Parsed candidate JSON is available. Repair this candidate locally instead of "
        "regenerating unrelated fields:\n"
        f"{_truncate_text(json.dumps(candidate_payload, ensure_ascii=False, indent=2), 4000)}\n"
    )


def _format_repair_schema_guidance(error: str, candidate_payload: Any) -> str:
    if not _is_missing_cell_fill_id_error(error):
        return ""

    paths = _missing_cell_fill_id_paths(candidate_payload)
    path_text = ", ".join(paths) if paths else "complex_model.cells[*].fill_id"
    return (
        "\n[Validation Issues]\n"
        "- code: cell.fill_id.missing\n"
        f"  schema_path: {path_text}\n"
        "  message: fill_id is required unless fill_type is void.\n"
        "  route_hint: reflect_plan\n\n"
        "[Repair Hints]\n"
        "- For every non-void cell, set fill_id to an already defined material, universe, "
        "or lattice id matching fill_type.\n"
        "- If and only if the cell is intentionally empty or outside the modeled domain, "
        "set fill_type='void' and leave fill_id null.\n"
        "- Do not invent material density, nuclide composition, benchmark facts, or cross "
        "section paths while repairing this schema error.\n"
        "- Preserve unrelated materials, dimensions, lattice maps, expert feedback, and "
        "confirmed reactor facts.\n\n"
    )


def _is_missing_cell_fill_id_error(error: str) -> bool:
    lowered = error.lower()
    return (
        "fill_id is required unless fill_type is void" in lowered
        or ("complex_model.cells" in lowered and "fill_id" in lowered)
    )


def _missing_cell_fill_id_paths(candidate_payload: Any) -> list[str]:
    if not isinstance(candidate_payload, dict):
        return []
    complex_model = candidate_payload.get("complex_model")
    if not isinstance(complex_model, dict):
        return []
    cells = complex_model.get("cells")
    if not isinstance(cells, list):
        return []

    paths: list[str] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        fill_type = cell.get("fill_type", "material")
        if fill_type != "void" and not cell.get("fill_id"):
            paths.append(f"complex_model.cells[{index}].fill_id")
    return paths


def _looks_like_truncated_json(raw: str, error: str) -> bool:
    lowered = error.lower()
    if "unterminated string" in lowered:
        return True
    if raw.count("{") > raw.count("}") or raw.count("[") > raw.count("]"):
        return True
    tail = raw.rstrip()
    return bool(tail) and tail[-1] not in {"}", "]", "`"}


# Nuclear-data path is an environment config, not a modeling fact. When
# OPENMC_CROSS_SECTIONS already points at a readable cross_sections.xml, the
# LLM-written "must be set by the user" confirmation is stale noise and must
# not be re-asked by ask_expert. Reading an existing environment value is not
# the same as inventing a path, so the human-confirmation safety boundary
# (no fabricated nuclear-data paths) still holds.
_CROSS_SECTIONS_CONFIRMATION_MARKERS = (
    "cross_section",
    "cross-section",
    "cross sections library",
    "openmc_cross_sections",
    "nuclear data path",
    "nuclear-data path",
    "nuclear data library",
    "nuclear-data library",
)


def _is_cross_sections_confirmation(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _CROSS_SECTIONS_CONFIRMATION_MARKERS)


def _cross_sections_env_available() -> bool:
    path = os.environ.get("OPENMC_CROSS_SECTIONS")
    return bool(path) and os.path.isfile(path)


def _cross_sections_question_resolved_by_env(text: str) -> bool:
    return _cross_sections_env_available() and _is_cross_sections_confirmation(text)


def _pending_expert_questions(state: GraphState) -> list[str]:
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    if plan is None:
        return []

    questions: list[str] = []
    capability = plan.capability_report
    for item in capability.required_human_confirmations:
        if is_structural_error_confirmation(item):
            # LLMs sometimes record structural defects (pin-count mismatch,
            # missing-universe refs) here; those are agent-fixable, not expert
            # questions, and re-asking them can trigger regenerate_plan.
            continue
        if _cross_sections_question_resolved_by_env(item):
            continue
        questions.append(f"Please provide or confirm: {item}")
    for item in plan.expert_assumptions:
        questions.append(f"Please confirm or correct this modeling assumption: {item}")

    report = state.get("validation_report")
    if report is not None:
        # Runtime issues (e.g. runtime.cross_sections_missing) come from a real
        # OpenMC failure, not an LLM guess: even when OPENMC_CROSS_SECTIONS is
        # set the run may still fail (stale path, subprocess env not inherited,
        # hdf5 mismatch), so these must stay routed to ask_expert. Do NOT
        # env-suppress them -- only capability-stage confirmations are suppressed.
        for issue in report.issues:
            if issue.route_hint == "ask_expert" or issue.requires_human_confirmation:
                questions.append(f"Please provide or confirm: [{issue.code}] {issue.message}")

    if capability.renderability in {"none", "skeleton"}:
        non_expert_routes = {"auto_repair", "reflect_plan", "retrieval", "capability_downgrade"}
        if not any(issue.route_hint in non_expert_routes for issue in capability.issues):
            for reason in capability.reasons:
                if is_structural_error_confirmation(reason):
                    continue
                questions.append(f"What expert information resolves this renderability gap: {reason}")
            for warning in capability.warnings:
                questions.append(f"Please review this modeling warning: {warning}")
        for issue in capability.issues:
            if issue.route_hint == "ask_expert" or issue.requires_human_confirmation:
                if _cross_sections_question_resolved_by_env(issue.message):
                    continue
                questions.append(f"Please provide or confirm: [{issue.code}] {issue.message}")

    questions = list(dict.fromkeys(q for q in questions if q.strip()))
    return _filter_already_resolved_questions(questions, state)[:8]


def _filter_already_resolved_questions(
    questions: list[str],
    state: GraphState,
) -> list[str]:
    events = state.setdefault("human_loop_events", [])
    resolved_items = _coerce_resolved_expert_items(state.get("resolved_expert_items", []))
    if not resolved_items:
        return questions

    kept: list[str] = []
    for question in questions:
        match = _resolved_match_for_question(question, resolved_items)
        if match is None:
            kept.append(question)
            continue
        event_name = (
            "expert_question_filtered_as_declined"
            if match.status == "declined"
            else "expert_question_filtered_as_resolved"
        )
        events.append(
            {
                "event": event_name,
                "round": state.get("expert_round_count", 0),
                "question": question,
                "answer": match.answer,
                "reason": match.reason or "matched prior expert feedback semantically",
                "semantic_keys": match.semantic_keys,
            }
        )
    return kept


def _coerce_resolved_expert_items(items: Any) -> list[ResolvedExpertItem]:
    resolved: list[ResolvedExpertItem] = []
    for item in items or []:
        try:
            resolved.append(
                item if isinstance(item, ResolvedExpertItem) else ResolvedExpertItem.model_validate(item)
            )
        except Exception:
            continue
    return resolved


def _update_resolved_expert_items_from_feedback(
    *,
    state: GraphState,
    questions: list[str],
    feedback_items: list[str],
    round_index: int,
) -> list[dict[str, Any]]:
    existing = _coerce_resolved_expert_items(state.get("resolved_expert_items", []))
    answer = "\n".join(feedback_items).strip()
    updated = list(existing)
    for question in questions:
        if not answer:
            status: Literal["resolved", "declined", "unknown"] = "declined"
            reason = "expert submitted empty feedback for this round"
        elif _question_answered_by_feedback(question, answer):
            status = "resolved"
            reason = "expert feedback overlaps the question by field keywords, values, or units"
        else:
            status = "unknown"
            reason = "feedback received, but this question was not clearly answered"
        updated.append(
            ResolvedExpertItem(
                question=question,
                answer=answer,
                kind=_expert_question_kind(question),
                status=status,
                source_round=round_index,
                semantic_keys=_semantic_keys(f"{question}\n{answer}"),
                reason=reason,
            )
        )
    return [item.model_dump(mode="json") for item in updated]


def _resolved_match_for_question(
    question: str,
    resolved_items: list[ResolvedExpertItem],
) -> ResolvedExpertItem | None:
    kind = _expert_question_kind(question)
    # Structural/capability questions (missing-cell references, pin-count
    # mismatches, unsupported subsystems) are plan-internal defects that a
    # material-level expert answer can never resolve. Only an identical-text
    # match may de-duplicate them; semantic matching against a prior material
    # answer would silently swallow the gap and leave the model stuck at
    # skeleton -- the over-correction of the earlier "re-ask" bug.
    structural_kind = kind in {"capability_reason", "capability_warning", "unknown"}
    for item in resolved_items:
        if item.status not in {"resolved", "declined"}:
            continue
        # Identical question text always wins (covers declined + exact re-asks).
        if _normalized_text(question) == _normalized_text(item.question):
            return item
        if item.status == "declined":
            continue
        if structural_kind:
            continue
        if _question_answered_by_feedback(question, item.answer):
            return item
        question_keys = set(_semantic_keys(question))
        item_keys = set(item.semantic_keys)
        # Empty question_keys cannot support a semantic claim. Guard against the
        # 0 >= 0 vacuous-truth trap that previously matched structural errors
        # (which extract no keywords) against unrelated material answers.
        if not question_keys:
            continue
        if question_keys.issubset(item_keys):
            return item
        if len(question_keys & item_keys) >= min(2, len(question_keys)):
            return item
    return None


def _expert_question_kind(question: str) -> str:
    lowered = question.lower()
    if "assumption" in lowered:
        return "assumption"
    if "renderability gap" in lowered:
        return "capability_reason"
    if "warning" in lowered:
        return "capability_warning"
    if "confirm" in lowered:
        return "confirmation"
    return "unknown"


def _question_answered_by_feedback(question: str, feedback: str) -> bool:
    question_norm = _normalized_text(question)
    feedback_norm = _normalized_text(feedback)
    if not question_norm or not feedback_norm:
        return False
    if question_norm in feedback_norm or feedback_norm in question_norm:
        return True

    question_keys = set(_semantic_keys(question))
    feedback_keys = set(_semantic_keys(feedback))
    if not question_keys or not feedback_keys:
        return False
    if question_keys & feedback_keys and (
        feedback_keys & _value_like_semantic_keys(feedback)
        or {"benchmark", "default", "acceptable", "confirmed"} & feedback_keys
    ):
        return True
    return len(question_keys & feedback_keys) >= min(2, len(question_keys))


def _semantic_keys(text: str) -> list[str]:
    lowered = text.lower()
    keys: list[str] = []
    patterns = {
        "fuel temperature": [
            r"fuel\s+temperature",
            r"temperature",
            r"temperature_k",
            r"燃料温度",
            r"温度",
        ],
        "boundary condition": [
            r"boundary\s+(condition|type)",
            r"boundary_type",
            r"boundary",
            r"边界条件",
            r"边界",
        ],
        "density": [r"density", r"density_value", r"密度"],
        "enrichment": [r"enrichment", r"u-?235", r"富集", r"富集度"],
        "composition": [r"composition", r"isotope", r"nuclide", r"同位素", r"组分"],
        "packing fraction": [r"packing\s+fraction", r"packing_fraction", r"填充"],
        "triso": [r"triso"],
        "pitch": [r"pitch", r"栅距"],
        "radius": [r"radius", r"半径"],
        "benchmark": [r"benchmark", r"基准"],
        "default": [r"default", r"默认"],
        "acceptable": [r"acceptable", r"可以", r"同意", r"确认"],
        "confirmed": [r"confirm", r"confirmed", r"按"],
        "reflective": [r"reflective", r"反射"],
        "vacuum": [r"vacuum", r"真空"],
        "periodic": [r"periodic", r"周期"],
    }
    for key, regexes in patterns.items():
        if any(re.search(pattern, lowered) for pattern in regexes):
            keys.append(key)
    for number in re.findall(r"\b\d+(?:\.\d+)?\s*(?:k|g/cm3|kg/m3|cm|%)?\b", lowered):
        keys.append(re.sub(r"\s+", " ", number.strip()))
    return list(dict.fromkeys(keys))


def _value_like_semantic_keys(text: str) -> set[str]:
    keys = set(_semantic_keys(text))
    return {
        key
        for key in keys
        if re.search(r"\d", key)
        or key in {"reflective", "vacuum", "periodic", "benchmark", "default"}
    }


def _normalized_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text.lower()).strip()


def _latest_expert_feedback(state: GraphState) -> str:
    feedback = state.get("expert_feedback", [])
    return feedback[-1].strip() if feedback else ""


def _classify_feedback_text(
    feedback: str,
    plan: SimulationPlan | None,
) -> tuple[str, str, Literal["high", "medium", "low"]]:
    lowered = feedback.lower()
    regenerate_patterns = [
        r"\brebuild\b",
        r"\bregenerate\b",
        r"\brestart\b",
        r"not\s+be\s+a",
        r"wrong\s+(requirement|task|model)",
        r"full[-\s]?core",
        r"whole\s+core",
        r"assembly",
        r"fixed\s+source",
        r"criticality",
        r"c5g7",
        r"triso",
        r"pebble",
        r"重新",
        r"重建",
        r"理解错",
        r"不是",
        r"全堆芯",
        r"组件",
        r"固定源",
        r"临界",
    ]
    if any(re.search(pattern, lowered) for pattern in regenerate_patterns):
        return (
            "regenerate_plan",
            "expert feedback appears to change the modeling intent, topology, benchmark, or physics task",
            "high",
        )
    if not _semantic_keys(feedback):
        return (
            "manual_review",
            "expert feedback has no safely mappable field, value, or confirmation keyword",
            "low",
        )
    if plan is None:
        return (
            "regenerate_plan",
            "no existing SimulationPlan is available for a local patch",
            "high",
        )
    return (
        "patch_plan",
        "expert feedback appears to provide or confirm local plan fields",
        "medium",
    )


def _build_plan_patches(
    plan: SimulationPlan,
    feedback: str,
    state: GraphState,
) -> tuple[list[dict[str, Any]], str, Literal["high", "medium", "low"]]:
    patches: list[dict[str, Any]] = []
    payload = plan.model_dump(mode="json")
    keys = set(_semantic_keys(feedback))

    if plan.model_spec is not None:
        spec_patches = _build_model_spec_patches(payload, feedback, keys)
        patches.extend(spec_patches)
    if plan.complex_model is not None:
        complex_patches = _build_complex_model_patches(payload, feedback, keys)
        patches.extend(complex_patches)

    patches.extend(_confirmation_removal_patches(payload, state))
    patches = _dedupe_patches(patches)
    if not patches:
        return [], "no safe local field path matched the expert feedback", "low"
    value_patches = [patch for patch in patches if patch.get("op") != "remove"]
    confidence: Literal["high", "medium", "low"] = "high" if value_patches else "medium"
    return patches, "generated minimal JSON Patch operations from expert feedback", confidence


def _build_model_spec_patches(
    payload: dict[str, Any],
    feedback: str,
    keys: set[str],
) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    model_spec = payload.get("model_spec") or {}
    fuel = (((model_spec.get("pin_cell") or {}).get("fuel")) or {})
    if "fuel temperature" in keys and fuel:
        temperature = _extract_temperature_k(feedback)
        if temperature is not None:
            patches.append(
                {
                    "op": "replace" if fuel.get("temperature_k") is not None else "add",
                    "path": "/model_spec/pin_cell/fuel/temperature_k",
                    "value": temperature,
                }
            )
    if "density" in keys and fuel:
        density = _extract_density(feedback)
        if density is not None:
            patches.append(
                {
                    "op": "replace",
                    "path": "/model_spec/pin_cell/fuel/density_value",
                    "value": density,
                }
            )
            unit = _extract_density_unit(feedback) or fuel.get("density_unit") or "g/cm3"
            patches.append(
                {
                    "op": "replace",
                    "path": "/model_spec/pin_cell/fuel/density_unit",
                    "value": unit,
                }
            )
    return patches


def _build_complex_model_patches(
    payload: dict[str, Any],
    feedback: str,
    keys: set[str],
) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    complex_model = payload.get("complex_model") or {}
    materials = complex_model.get("materials") or []
    fuel_index = _find_material_index(materials, feedback)
    if fuel_index is not None:
        material = materials[fuel_index]
        if "fuel temperature" in keys:
            temperature = _extract_temperature_k(feedback)
            if temperature is not None:
                patches.append(
                    {
                        "op": "replace" if material.get("temperature_k") is not None else "add",
                        "path": f"/complex_model/materials/{fuel_index}/temperature_k",
                        "value": temperature,
                    }
                )
        if "density" in keys:
            density = _extract_density(feedback)
            if density is not None:
                patches.append(
                    {
                        "op": "replace" if material.get("density_value") is not None else "add",
                        "path": f"/complex_model/materials/{fuel_index}/density_value",
                        "value": density,
                    }
                )
                unit = _extract_density_unit(feedback) or material.get("density_unit") or "g/cm3"
                patches.append(
                    {
                        "op": "replace" if material.get("density_unit") is not None else "add",
                        "path": f"/complex_model/materials/{fuel_index}/density_unit",
                        "value": unit,
                    }
                )

    boundary = _extract_boundary_type(feedback)
    if boundary is not None and "boundary condition" in keys:
        for index, surface in enumerate(complex_model.get("surfaces") or []):
            if surface.get("boundary_type") is not None:
                patches.append(
                    {
                        "op": "replace",
                        "path": f"/complex_model/surfaces/{index}/boundary_type",
                        "value": boundary,
                    }
                )
        for index, assembly in enumerate(complex_model.get("assemblies") or []):
            if assembly.get("boundary") is not None:
                patches.append(
                    {
                        "op": "replace",
                        "path": f"/complex_model/assemblies/{index}/boundary",
                        "value": boundary,
                    }
                )
    return patches


def _confirmation_removal_patches(
    payload: dict[str, Any],
    state: GraphState,
) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    resolved_items = [
        item
        for item in _coerce_resolved_expert_items(state.get("resolved_expert_items", []))
        if item.status == "resolved"
    ]
    if not resolved_items:
        return patches
    _append_remove_matches(
        patches,
        payload.get("capability_report", {}).get("required_human_confirmations", []),
        "/capability_report/required_human_confirmations",
        resolved_items,
    )
    _append_remove_matches(
        patches,
        payload.get("expert_assumptions", []),
        "/expert_assumptions",
        resolved_items,
    )
    complex_model = payload.get("complex_model") or {}
    _append_remove_matches(
        patches,
        complex_model.get("requires_human_confirmation", []),
        "/complex_model/requires_human_confirmation",
        resolved_items,
    )
    for index, material in enumerate(complex_model.get("materials") or []):
        _append_remove_matches(
            patches,
            material.get("requires_human_confirmation", []),
            f"/complex_model/materials/{index}/requires_human_confirmation",
            resolved_items,
        )
    for index, lattice in enumerate(complex_model.get("lattices") or []):
        _append_remove_matches(
            patches,
            lattice.get("requires_human_confirmation", []),
            f"/complex_model/lattices/{index}/requires_human_confirmation",
            resolved_items,
        )
    for index, triso in enumerate(complex_model.get("trisos") or []):
        _append_remove_matches(
            patches,
            triso.get("requires_human_confirmation", []),
            f"/complex_model/trisos/{index}/requires_human_confirmation",
            resolved_items,
        )
    for index, pebble in enumerate(complex_model.get("pebbles") or []):
        _append_remove_matches(
            patches,
            pebble.get("requires_human_confirmation", []),
            f"/complex_model/pebbles/{index}/requires_human_confirmation",
            resolved_items,
        )
    return patches


def _append_remove_matches(
    patches: list[dict[str, Any]],
    items: list[Any],
    base_path: str,
    resolved_items: list[ResolvedExpertItem],
) -> None:
    for index in range(len(items) - 1, -1, -1):
        item_text = str(items[index])
        question = f"Please provide or confirm: {item_text}"
        if _resolved_match_for_question(question, resolved_items) is not None:
            patches.append({"op": "remove", "path": f"{base_path}/{index}"})


def _find_material_index(materials: list[dict[str, Any]], feedback: str) -> int | None:
    if not materials:
        return None
    lowered = feedback.lower()
    if "fuel" in lowered or "燃料" in lowered:
        for index, material in enumerate(materials):
            material_text = f"{material.get('id', '')} {material.get('name', '')}".lower()
            if "fuel" in material_text or "uo2" in material_text:
                return index
    if len(materials) == 1:
        return 0
    return None


def _extract_temperature_k(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", text.lower())
    return float(match.group(1)) if match else None


def _extract_density(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:g\s*/\s*cm3|g/cm3|g/cc|kg\s*/\s*m3|kg/m3)", text.lower())
    return float(match.group(1)) if match else None


def _extract_density_unit(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"kg\s*/\s*m3|kg/m3", lowered):
        return "kg/m3"
    if re.search(r"g\s*/\s*cm3|g/cm3|g/cc", lowered):
        return "g/cm3"
    if "atom/b-cm" in lowered:
        return "atom/b-cm"
    return None


def _extract_boundary_type(text: str) -> str | None:
    lowered = text.lower()
    for boundary in ("reflective", "vacuum", "periodic", "transmission", "white"):
        if boundary in lowered:
            return boundary
    if "反射" in text:
        return "reflective"
    if "真空" in text:
        return "vacuum"
    if "周期" in text:
        return "periodic"
    return None


def _dedupe_patches(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for patch in patches:
        deduped[(patch.get("op", ""), patch.get("path", ""))] = patch
    return list(deduped.values())


def _apply_json_patches(payload: Any, patches: list[dict[str, Any]]) -> Any:
    updated = json.loads(json.dumps(payload))
    for patch in patches:
        op = patch.get("op")
        path = patch.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"invalid JSON Patch path: {path!r}")
        parent, key = _json_pointer_parent(updated, path)
        if op in {"add", "replace"}:
            value = patch.get("value")
            if isinstance(parent, list):
                index = int(key)
                if op == "add" and index == len(parent):
                    parent.append(value)
                else:
                    parent[index] = value
            else:
                parent[key] = value
        elif op == "remove":
            if isinstance(parent, list):
                del parent[int(key)]
            else:
                parent.pop(key, None)
        else:
            raise ValueError(f"unsupported JSON Patch op: {op!r}")
    return updated


def _normalize_capability_report_for_plan_validation(payload: dict[str, Any]) -> dict[str, Any]:
    """Reset locally assessed renderer capability before validating patched plans.

    ``assess_capability`` may write skeleton/assembly capability back onto a
    complex-only plan for sidecars and routing. The schema intentionally rejects
    that shape as an LLM-authored plan. A local expert patch should validate the
    structural plan first, then let ``assess_capability`` recompute capability.
    """
    if payload.get("model_spec") is not None or payload.get("complex_model") is None:
        return payload
    capability = dict(payload.get("capability_report") or {})
    if capability.get("is_executable") is False:
        capability["renderability"] = "none"
        capability["supported_renderer"] = "none"
        capability["executable_subsystems"] = []
        payload["capability_report"] = capability
    return payload


def _json_pointer_parent(payload: Any, path: str) -> tuple[Any, str]:
    parts = [_decode_json_pointer_part(part) for part in path.strip("/").split("/")]
    current = payload
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current, parts[-1]


def _decode_json_pointer_part(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


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


_SELF_REPAIRABLE_CAPABILITY_PATTERNS = (
    # Reference-consistency defects from renderer diagnostics: a universe/cell/
    # region/control-rod/reflector pointing at an id that does not exist.
    r"references missing (cells|universes|materials|surfaces|regions?)",
    # Pin-count / lattice-shape defects.
    r"pin counts? (do not match|mismatch)",
    r"expected_counts",
    r"shape .* does not match",
    r"(universe_pattern )?rows have unequal lengths",
    # Cylinder geometry defects.
    r"radius must be (positive|less than pitch)",
    r"non-numeric radius",
)


# Stable codes for plan defects the agent can fix itself (plan typos) vs. facts
# only an expert can supply. Code-based matching replaces the fragile regex over
# free-text renderer messages above; the regex stays as a legacy fallback for
# renderers that do not yet emit structured issues.
SELF_REPAIRABLE_CODES = frozenset({
    "lattice.universe_ref_missing",
    "lattice.shape_pattern_mismatch",
    "lattice.pattern_ragged_rows",
    "lattice.pin_count_mismatch",
    "cell.material_ref_missing",
    "cell.region_ref_missing",
    "cell.universe_ref_missing",
    "cell.lattice_ref_missing",
    "core.lattice_ref_missing",
    "universe.cell_ref_missing",
    "region.surface_ref_missing",
    "axial_layer.fill_ref_missing",
    "axial_layer.loading_ref_missing",
    "lattice_loading.base_ref_missing",
    "lattice_loading.override_universe_ref_missing",
    "surface.cylinder_radius_invalid",
    "material.mixed_percent_type",
})


def _capability_self_repair_errors(
    capability: RenderCapabilityReport,
) -> list[ValidationIssue]:
    """Issues the agent can fix itself (plan typos) vs. facts only an expert can supply.

    Prefers structured ``capability.issues`` filtered by :data:`SELF_REPAIRABLE_CODES`;
    falls back to the legacy regex over ``reasons`` / ``required_human_confirmations``
    for renderers that do not yet emit structured issues. A missing density or
    composition is a real gap the expert must fill, so those codes stay out of
    :data:`SELF_REPAIRABLE_CODES` and route to ask_expert.
    """
    structured = [
        issue
        for issue in capability.issues
        if issue.code in SELF_REPAIRABLE_CODES
    ]
    if structured:
        return list(structured)
    candidates: list[str] = []
    if capability.renderability in {"none", "skeleton"}:
        candidates.extend(capability.reasons)
    # Pin-count mismatches are also recorded as soft human confirmations on the
    # lattice spec; they are still count/override errors the agent can fix.
    candidates.extend(capability.required_human_confirmations)
    repaired_texts = [
        text
        for text in candidates
        if any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in _SELF_REPAIRABLE_CAPABILITY_PATTERNS
        )
    ]
    return [
        ValidationIssue(severity="error", code="legacy.self_repairable", message=text)
        for text in dict.fromkeys(repaired_texts)
    ]


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


# Regenerable render outputs. Removing them at the start of a render node lets a
# skeleton / non-exportable run overwrite a prior exportable run's model.py, XML
# and optimistic capability_report.json, so the on-disk state always matches the
# current run. Run records (simulation_plan.json, transcript.json,
# plan_artifacts/, checkpoints.sqlite, inspect_runs.jsonl) are NOT in this set.
_RENDER_ARTIFACT_NAMES: tuple[str, ...] = (
    "model.py",
    "smoke_model.py",
    "materials.xml",
    "geometry.xml",
    "settings.xml",
    "tallies.xml",
    "plots.xml",
    "capability_report.json",
    "TODO.md",
)


def _clean_stale_render_artifacts(output_dir: Path) -> None:
    """Remove regenerable render outputs left over from a prior run.

    Guarantees the output directory reflects the CURRENT run: when this run is
    non-exportable, no previous run's model.py / XML / exportable
    capability_report.json remains to masquerade as a successful result. Run
    records that are appended-to rather than regenerated are preserved.
    """
    import shutil

    for name in _RENDER_ARTIFACT_NAMES:
        path = output_dir / name
        if path.exists() or path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass
    # OpenMC run outputs and plot images.
    for pattern in ("statepoint.*.h5", "summary.*.h5"):
        for path in output_dir.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass
    plots_dir = output_dir / "plots"
    if plots_dir.exists():
        try:
            shutil.rmtree(plots_dir)
        except OSError:
            pass


def _plot_bounds_metadata(plan: SimulationPlan) -> list[dict]:
    """Project plan.plot_specs into the dict shape the bounds validator expects."""
    out: list[dict] = []
    for i, p in enumerate(plan.plot_specs):
        origin = p.origin or (0.0, 0.0, 0.0)
        width = p.width_cm or (0.0, 0.0)
        out.append({
            "id": f"plot_{i}",
            "basis": getattr(p, "basis", "xy"),
            "origin": {"x": float(origin[0]), "y": float(origin[1] if len(origin) > 1 else 0.0)},
            "width": {"x": float(width[0]), "y": float(width[1] if len(width) > 1 else width[0])},
        })
    return out


def _write_non_executable_marker(
    output_dir: Path,
    report: "ValidationReport | None",
    plan: "SimulationPlan | None",
    capability: RenderCapabilityReport | None = None,
) -> None:
    """Write an honest NOT_EXECUTABLE capability_report.json + TODO.md.

    Used when the render node is skipped (plan invalid / no renderer) so that a
    prior exportable run's optimistic sidecars cannot mask the current run's
    failure. ``capability`` may be supplied (e.g. renderer returned 'none');
    otherwise a minimal non-executable report is derived from the validation
    issues.
    """
    if capability is None:
        issues = list(report.issues) if report is not None and report.issues else []
        error_messages = [iss.message for iss in issues if iss.severity == "error"]
        capability = RenderCapabilityReport(
            renderability="none",
            is_executable=False,
            supported_renderer="none",
            executable_subsystems=[],
            reasons=error_messages or ["plan validation failed; render skipped"],
            issues=issues,
        )
    _write_capability_sidecar(output_dir, capability)

    lines = [
        "# TODO — OpenMC model not executable",
        "",
        f"Renderability: {capability.renderability}",
        "Status: NOT_EXECUTABLE",
        "",
        "## Blocking reasons",
    ]
    for reason in capability.reasons:
        lines.append(f"- {reason}")
    (output_dir / "TODO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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


_COUNT_CONSTRAINT_KEYWORDS = (
    "expected_counts",
    "pin count",
    "pin-count",
    "棒位",
    "总数",
    "共有",
    "检查",
    "燃料棒",
    "导向管",
    "裂变室",
    "MOX",
    "UO2",
    "mox",
    "uo2",
)


def _extract_hard_count_constraints(requirement: str, *, limit: int = 18) -> str:
    """Extract bounded source lines that look like hard count constraints."""
    lines: list[str] = []
    for raw_line in requirement.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        has_digit = bool(re.search(r"\d", line))
        has_count_unit = bool(
            re.search(r"(个|根|位置|count|counts|total|expected)", line, re.IGNORECASE)
        )
        has_keyword = any(keyword in line for keyword in _COUNT_CONSTRAINT_KEYWORDS)
        if has_digit and has_count_unit and has_keyword:
            lines.append(line)
        if len(lines) >= limit:
            break
    return "\n".join(dict.fromkeys(lines))


def _hard_count_constraints_context(state: GraphState) -> str:
    constraints = state.get("hard_count_constraints") or _extract_hard_count_constraints(
        state.get("requirement", "")
    )
    if not constraints:
        return ""
    return (
        "\n[Hard Count Constraints From Input]\n"
        "Treat these source-text counts as hard constraints. For any lattice that "
        "uses these counts, encode them in expected_counts and ensure the expanded "
        "universe_pattern matches them exactly. During repair, prefer fixing "
        "fill_universe/overrides/universe_pattern; change expected_counts only if "
        "the original input count was transcribed incorrectly.\n"
        f"{_truncate_text(constraints, 2500)}\n"
    )


def _core_lattice_naming_guidance() -> str:
    """Static rule injected into generation/reflection prompts.

    The C5G7 regression: the LLM wrote 'uo2_assembly_univ' in core_lattice but
    set assembly.id to 'uo2_assy'. The core renderer looks up assemblies by id,
    so any mismatch blocks export and loops reflect_plan. auto_repair now
    unifies the names deterministically; stating the rule up front keeps the
    LLM from introducing the mismatch in the first place.
    """
    return (
        "\n[Core Lattice Naming]\n"
        "Each assembly slot in a core lattice's universe_pattern MUST equal the\n"
        "id of an AssemblySpec -- the wrapper universe id is exactly assembly.id.\n"
        "Do NOT invent names like '<lattice_id>_univ' or '<assembly>_univ' that\n"
        "differ from assembly.id; the core renderer looks up assemblies by id and\n"
        "any mismatch blocks export.\n"
    )


def _pin_count_mismatch_context(state: GraphState) -> str:
    report = state.get("validation_report")
    if report is None:
        return ""
    mismatches = [
        issue
        for issue in report.issues
        if issue.code == "lattice.pin_count_mismatch"
    ]
    if not mismatches:
        return ""
    plan = _coerce_simulation_plan(state.get("simulation_plan"))
    requirement = state.get("requirement", "")
    lines: list[str] = []
    for issue in mismatches[:8]:
        lines.append(f"- {issue.schema_path or '<unknown path>'}: {issue.message}")
        location = _pin_count_mismatch_location(plan, issue, requirement)
        if location:
            lines.append(location)
    return (
        "\n[Pin Count Mismatch Evidence]\n"
        "The current IR expands to counts that disagree with expected_counts. "
        "Re-read the input rows/regions and correct the lattice map before any "
        "render/export step.\n"
        + "\n".join(lines)
        + "\n"
    )


def _lattice_id_from_schema_path(schema_path: str | None) -> str | None:
    """Extract the lattice id from ``complex_model.lattices.<id>.universe_pattern``."""
    if not schema_path:
        return None
    parts = schema_path.split(".")
    try:
        return parts[parts.index("lattices") + 1]
    except (ValueError, IndexError):
        return None


def _pin_count_mismatch_location(
    plan: SimulationPlan | None,
    issue: ValidationIssue,
    requirement: str,
) -> str:
    """Pinpoint the exact cells to rewrite using the requirement's canonical pin map.

    A count diff alone ('mox7 -2, mox87 +2') does not tell the LLM which of 289
    positions are wrong, so repeated reflections return a byte-identical wrong
    pattern. When the input document carries a canonical pin map for this
    lattice, compare it cell by cell and list the mis-positioned coordinates
    alongside the authoritative rows.
    """
    if plan is None or plan.complex_model is None or not requirement:
        return ""
    lattice_id = _lattice_id_from_schema_path(issue.schema_path)
    if not lattice_id:
        return ""
    lattice = next(
        (lat for lat in plan.complex_model.lattices if lat.id == lattice_id),
        None,
    )
    if lattice is None or not lattice.universe_pattern:
        return ""
    canonical = extract_canonical_pin_map(requirement, lattice_id)
    if canonical is None:
        return ""
    diffs = lattice_cell_mismatches(lattice.universe_pattern, canonical.rows)
    total = sum(len(row) for row in lattice.universe_pattern)
    parts: list[str] = []
    if diffs:
        if len(diffs) <= max(8, total // 4):
            cell_lines = [
                f"  R{row:02d}C{col:02d}: expected {expected!r}, got {actual!r}"
                for row, col, expected, actual in diffs[:64]
            ]
            parts.append(
                "Exact cells whose universe differs from the canonical pin map "
                "(row/col are 1-indexed; rewrite each to the expected universe):\n"
                + "\n".join(cell_lines)
            )
        else:
            parts.append(
                f"{len(diffs)}/{total} cells differ from the canonical map -- the "
                "pattern is broadly wrong; rebuild universe_pattern row by row "
                "from the canonical map below rather than patching individual cells."
            )
    parts.append(
        "Canonical pin map (transcribe row by row from R01; do NOT infer from "
        "symmetry or from the count diff):\n" + canonical.raw_text.strip()
    )
    return "\n".join(parts)


def _requirement_with_expert_feedback(state: GraphState) -> str:
    requirement = state["requirement"]
    feedback = state.get("expert_feedback", [])
    if not feedback:
        return requirement
    resolved_items = _coerce_resolved_expert_items(state.get("resolved_expert_items", []))
    resolved_context = ""
    if resolved_items:
        resolved_context = (
            "\n\nResolved expert feedback items:\n"
            + "\n".join(
                (
                    f"- Question: {item.question}\n"
                    f"  Expert answer: {item.answer}\n"
                    f"  Resolution: {item.status}; semantic_keys={item.semantic_keys}"
                )
                for item in resolved_items
                if item.status in {"resolved", "declined"}
            )
        )
    return (
        f"{requirement}\n\n"
        "Human expert feedback that should guide the structured SimulationPlan:\n"
        + "\n".join(f"- {item}" for item in feedback)
        + resolved_context
        + "\n\n"
        "Expert feedback consumption rules (IMPORTANT):\n"
        "- Treat expert feedback as authoritative unless it conflicts with the original requirement or validated OpenMC constraints.\n"
        "- If expert feedback answers a previous confirmation question, write the answer into the corresponding structured field.\n"
        "- Do not keep requires_human_confirmation entries for items already answered by expert feedback.\n"
        "- Do not keep expert_assumptions entries for assumptions already confirmed or corrected by expert feedback.\n"
        "- Do not ask the same expert question again in a later round.\n"
        "- If the feedback is in a different language from the question, infer the semantic match rather than relying on exact text.\n"
        "- If the feedback confirms use of benchmark/default/document values, represent that confirmation explicitly in the plan or remove the unresolved confirmation marker.\n"
        "- If a value remains genuinely unresolved after considering all expert feedback, then and only then keep requires_human_confirmation."
    )


def _augmented_plan_requirement(state: GraphState) -> str:
    base = _requirement_with_expert_feedback(state)
    docs = state.get("openmc_api_docs", [])
    few_shots = state.get("few_shot_examples", [])
    parts = [
        base,
        _hard_count_constraints_context(state),
        _core_lattice_naming_guidance(),
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


def _trace_event_update(
    state: GraphState,
    event_type: str,
    *,
    summary: str = "",
    report: ValidationReport | None = None,
    capability: RenderCapabilityReport | None = None,
    retrieval_context: RetrievalContext | None = None,
    plan: SimulationPlan | None = None,
    metadata: dict[str, Any] | None = None,
    round_index: int | None = None,
) -> dict[str, Any]:
    """Return a state update with one appended trace event.

    Trace failures are intentionally swallowed so observability never changes
    workflow behavior.
    """
    try:
        recorder = TraceRecorder(config=TraceConfig(), trace=state.get("trace"))
        if recorder.trace.user_request_preview is None and state.get("requirement"):
            recorder.trace.user_request_preview = _truncate_text(
                state.get("requirement", ""), recorder.config.max_preview_chars
            )
        active_report = report or state.get("validation_report")
        issues = list(active_report.issues) if active_report is not None else []
        active_plan = plan or _coerce_simulation_plan(state.get("simulation_plan"))
        active_capability = capability
        if active_capability is None and active_plan is not None:
            active_capability = active_plan.capability_report
        event_metadata: dict[str, Any] = dict(metadata or {})
        if report is not None:
            event_metadata.update(summarize_validation_report(report))
        if capability is not None:
            event_metadata.update(summarize_capability_report(capability))
        if retrieval_context is not None:
            event_metadata["retrieval"] = summarize_retrieval_context_for_trace(
                retrieval_context
            )
        if plan is not None and recorder.config.capture_plan_preview:
            event_metadata["plan_preview"] = preview_plan(
                plan, recorder.config.max_preview_chars
            )
        recorder.add_event(
            event_type,  # type: ignore[arg-type]
            round_index=(
                round_index
                if round_index is not None
                else state.get("retry_count", 0)
            ),
            summary=summary,
            issue_codes=[issue.code for issue in issues],
            route_hints=[issue.route_hint for issue in issues if issue.route_hint],
            renderability=(
                active_capability.renderability if active_capability is not None else None
            ),
            supported_renderer=(
                active_capability.supported_renderer
                if active_capability is not None
                else None
            ),
            metadata=event_metadata,
        )
        if event_type in {"workflow_completed", "workflow_failed"}:
            if event_type == "workflow_failed" or state.get("error"):
                recorder.trace.final_status = "failed"
            elif active_capability is not None and active_capability.renderability == "skeleton":
                recorder.trace.final_status = "skeleton"
            elif active_report is not None and active_report.is_valid:
                recorder.trace.final_status = "valid"
            else:
                recorder.trace.final_status = "invalid"
            if active_capability is not None:
                recorder.trace.final_renderability = active_capability.renderability
                recorder.trace.final_supported_renderer = active_capability.supported_renderer
        return {"trace": recorder.export_json()}
    except Exception:
        return {}


def _write_plan_generation_artifacts(
    state: GraphState,
    *,
    phase: str,
    result: StructuredOutputResult[SimulationPlan],
    retry_count: int,
) -> list[str]:
    output_dir = Path(state.get("output_dir", "data/runs"))
    artifacts_dir = output_dir / "plan_artifacts"
    stage_index = _next_plan_artifact_index(state)
    stage_dir = artifacts_dir / f"{stage_index:03d}_{phase}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    paths = list(state.get("plan_artifacts", []))
    stage_paths: list[str] = []
    if result.raw_response:
        raw_path = stage_dir / "raw_response.txt"
        raw_path.write_text(result.raw_response, encoding="utf-8")
        stage_paths.append(str(raw_path))
    if result.candidate_payload is not None:
        candidate_path = stage_dir / "candidate_plan.json"
        _write_json_file(candidate_path, result.candidate_payload)
        stage_paths.append(str(candidate_path))
    if result.value is not None:
        validated_path = stage_dir / "validated_plan.json"
        _write_json_file(validated_path, result.value.model_dump(mode="json"))
        stage_paths.append(str(validated_path))

    meta_path = stage_dir / "meta.json"
    _write_json_file(
        meta_path,
        {
            "phase": phase,
            "ok": result.ok,
            "error": result.error,
            "parse_notes": result.parse_notes or [],
            "retry_count": retry_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifacts": stage_paths,
        },
    )
    stage_paths.append(str(meta_path))
    paths.extend(stage_paths)
    return paths


def _write_final_simulation_plan(
    state: GraphState,
    plan: SimulationPlan,
    *,
    existing_paths: list[str] | None = None,
) -> list[str]:
    output_dir = Path(state.get("output_dir", "data/runs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "simulation_plan.json"
    _write_json_file(plan_path, plan.model_dump(mode="json"))
    paths = existing_paths if existing_paths is not None else state.get("plan_artifacts", [])
    return _append_plan_artifact_path(paths, plan_path)


def _next_plan_artifact_index(state: GraphState) -> int:
    count = 0
    for path in state.get("plan_artifacts", []):
        artifact_path = Path(path)
        if artifact_path.name == "meta.json" and artifact_path.parent.parent.name == "plan_artifacts":
            count += 1
    return count


def _append_plan_artifact_path(paths: list[str], path: Path) -> list[str]:
    text = str(path)
    updated = list(paths)
    if text not in updated:
        updated.append(text)
    return updated


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
