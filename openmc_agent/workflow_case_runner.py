"""Lightweight real-workflow case runner for evaluation backbones.

The runner is intentionally plan-first and safe by default: it can invoke the
production plan graph, but it disables plots / smoke tests and substitutes a
non-executing export tool unless explicitly configured otherwise. It returns a
WorkflowTrace for both success and failure so benchmark harnesses can diagnose
stage, patch, artifact, and capability regressions without crashing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from openmc_agent.benchmark_runner import AblationConfig
from openmc_agent.evaluation import EvaluationCase
from openmc_agent.retrieval_orchestrator import RetrievalPolicy
from openmc_agent.schemas import AgentBaseModel
from openmc_agent.tools import ToolResult
from openmc_agent.workflow_trace import TraceRecorder, WorkflowTrace, trace_from_raw


def build_plan_graph(*args: Any, **kwargs: Any) -> Any:
    from openmc_agent.graph import build_plan_graph as _build_plan_graph

    return _build_plan_graph(*args, **kwargs)


class WorkflowCaseRunnerConfig(AgentBaseModel):
    model: str = "fake"
    output_dir: str = "data/evals/workflow"
    mode: Literal["plan_only", "render_only", "smoke_test"] = "plan_only"

    use_incremental_executor: bool = True
    reference_patch_policy: str = "off"

    enable_retrieval: bool = True
    enable_graph_retrieval: bool = True

    enable_render: bool = False
    enable_openmc_tools: bool = False

    patch_llm_client: Any | None = None
    allow_monolithic_fallback_for_incremental_failure: bool = False

    timeout_s: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def run_workflow_case(
    case: EvaluationCase,
    config: WorkflowCaseRunnerConfig,
) -> WorkflowTrace:
    """Run one evaluation case through a lightweight plan workflow.

    No exception escapes this function. The returned trace contains either a
    ``workflow_completed`` event from the production graph or a synthetic
    ``workflow_failed`` event with stage / error metadata.
    """
    output_dir = Path(config.output_dir) / case.case_id
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        retrieval_policy = _retrieval_policy_from_config(config)
        graph_kwargs: dict[str, Any] = {}
        if not config.enable_openmc_tools:
            graph_kwargs["export_xml_tool"] = _noop_export_xml
        graph = build_plan_graph(
            **graph_kwargs,
            enable_plots=config.mode == "smoke_test" and config.enable_openmc_tools,
            enable_smoke_test=config.mode == "smoke_test" and config.enable_openmc_tools,
            retrieval_policy=retrieval_policy,
            patch_llm_client=config.patch_llm_client,
            use_incremental_executor=config.use_incremental_executor,
            allow_monolithic_fallback_for_incremental_failure=(
                config.allow_monolithic_fallback_for_incremental_failure
            ),
            reference_patch_policy=config.reference_patch_policy,
        )
        state = graph.invoke(
            {
                "requirement": case.user_request,
                "model": config.model,
                "output_dir": str(output_dir),
                "records_path": str(output_dir / "simulation_runs.jsonl"),
                "use_incremental_executor": config.use_incremental_executor,
                "allow_monolithic_fallback_for_incremental_failure": (
                    config.allow_monolithic_fallback_for_incremental_failure
                ),
            }
        )
        return _trace_from_state(case, config, state, artifact_dir=output_dir)
    except Exception as exc:  # graph failures are evaluation data, not harness failures
        recorder = TraceRecorder()
        recorder.trace.user_request_preview = (case.user_request or "")[:200]
        recorder.add_event(
            "workflow_failed",
            summary=f"workflow case runner failed for {case.case_id}",
            metadata={
                "error": str(exc),
                "failed_stage": "workflow_case_runner",
                "artifact_dir": str(output_dir),
                "case_id": case.case_id,
                "runner_config": _safe_model_dump(config),
            },
        )
        recorder.trace.final_status = "failed"
        return recorder.trace


def make_workflow_case_runner(
    config: WorkflowCaseRunnerConfig,
) -> Callable[[EvaluationCase, AblationConfig], WorkflowTrace]:
    """Build a benchmark-runner compatible real workflow adapter."""

    def _runner(case: EvaluationCase, ablation: AblationConfig) -> WorkflowTrace:
        ablation_metadata = {
            "name": ablation.name,
            "enable_grep": ablation.enable_grep,
            "enable_graph": ablation.enable_graph,
            "enable_rag": ablation.enable_rag,
            "enable_auto_repair": ablation.enable_auto_repair,
            "enable_reflect_plan": ablation.enable_reflect_plan,
            "enable_ask_expert": ablation.enable_ask_expert,
        }
        merged = config.model_copy(
            update={
                "enable_retrieval": any(
                    [ablation.enable_grep, ablation.enable_graph, ablation.enable_rag]
                ),
                "enable_graph_retrieval": ablation.enable_graph,
                "metadata": {
                    **config.metadata,
                    "requested_ablation": ablation_metadata,
                },
            }
        )
        trace = run_workflow_case(case, merged)
        _append_ablation_metadata(trace, ablation_metadata)
        return trace

    return _runner


def _retrieval_policy_from_config(config: WorkflowCaseRunnerConfig) -> RetrievalPolicy:
    if not config.enable_retrieval:
        return RetrievalPolicy(enable_grep=False, enable_graph=False, enable_rag=False)
    return RetrievalPolicy(
        enable_grep=True,
        enable_graph=config.enable_graph_retrieval,
        enable_rag=True,
    )


def _noop_export_xml(model_path: Path) -> ToolResult:
    return ToolResult(
        name="export_xml",
        ok=True,
        returncode=0,
        stdout="skipped by workflow_case_runner plan-only configuration",
        artifacts=[],
    )


def _trace_from_state(
    case: EvaluationCase,
    config: WorkflowCaseRunnerConfig,
    state: dict[str, Any],
    *,
    artifact_dir: Path,
) -> WorkflowTrace:
    trace = trace_from_raw(state.get("trace"))
    recorder = TraceRecorder(trace=trace)
    metadata = _state_trace_metadata(state, config=config, artifact_dir=artifact_dir)
    event_type = "workflow_failed" if state.get("error") else "workflow_completed"
    recorder.add_event(
        event_type,
        summary=f"workflow case runner summary for {case.case_id}",
        renderability=metadata.get("renderability"),
        supported_renderer=metadata.get("supported_renderer"),
        metadata=metadata,
    )
    if state.get("error"):
        recorder.trace.final_status = "failed"
    elif metadata.get("renderability") == "skeleton":
        recorder.trace.final_status = "skeleton"
    elif metadata.get("plan_schema_success"):
        recorder.trace.final_status = "valid"
    recorder.trace.final_renderability = metadata.get("renderability")
    recorder.trace.final_supported_renderer = metadata.get("supported_renderer")
    if recorder.trace.user_request_preview is None:
        recorder.trace.user_request_preview = (case.user_request or "")[:200]
    return recorder.trace


def _state_trace_metadata(
    state: dict[str, Any],
    *,
    config: WorkflowCaseRunnerConfig,
    artifact_dir: Path,
) -> dict[str, Any]:
    validation_report = _safe_model_dump(state.get("validation_report"))
    capability_report = _capability_report_from_state(state)
    planning_mode_decision = state.get("planning_mode_decision") or {}
    planning_mode = _planning_mode_from_state(state)
    plan_build_state_summary = _summarize_plan_build_state(state.get("plan_build_state"))
    incremental_result = state.get("incremental_execution_result")
    plan_artifacts = _plan_artifacts(state)
    issue_codes = _issue_codes_from_state(state, validation_report, incremental_result)
    renderability = capability_report.get("renderability") if capability_report else None
    supported_renderer = capability_report.get("supported_renderer") if capability_report else None
    failed_stage = _failed_stage_from_state(state)
    failed_patch_type = _failed_patch_type_from_state(state, incremental_result)
    metadata = {
        "planning_mode_decision": planning_mode_decision,
        "planning_mode": planning_mode,
        "incremental_execution_result": incremental_result,
        "plan_build_state_summary": plan_build_state_summary,
        "capability_report": capability_report,
        "validation_report": validation_report,
        "plan_artifacts": plan_artifacts,
        "artifact_keys": _artifact_keys(plan_artifacts),
        "artifact_dir": str(artifact_dir),
        "failed_stage": failed_stage,
        "failed_patch_type": failed_patch_type,
        "issue_codes": issue_codes,
        "renderer": supported_renderer,
        "renderability": renderability,
        "supported_renderer": supported_renderer,
        "retrieval_triggered": bool(state.get("retrieval_context") or state.get("grep_evidence") or state.get("rag_evidence")),
        "plan_schema_success": _plan_schema_success(state, validation_report),
        "simulation_plan_present": state.get("simulation_plan") is not None,
        "incremental_patch_success": _incremental_success(incremental_result),
        "mode": config.mode,
        "runner_config": _safe_model_dump(config),
        "error": state.get("error", ""),
    }
    return metadata


def _capability_report_from_state(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("simulation_plan")
    if hasattr(plan, "capability_report"):
        return _safe_model_dump(plan.capability_report)
    if isinstance(plan, dict):
        report = plan.get("capability_report")
        if isinstance(report, dict):
            return report
    return {}


def _planning_mode_from_state(state: dict[str, Any]) -> str | None:
    inc = state.get("incremental_execution_result")
    if isinstance(inc, dict) and isinstance(inc.get("planning_mode"), str):
        return inc["planning_mode"]
    decision = state.get("planning_mode_decision")
    if isinstance(decision, dict) and isinstance(decision.get("mode"), str):
        return decision["mode"]
    return None


def _summarize_plan_build_state(plan_build_state: Any) -> dict[str, Any]:
    if not isinstance(plan_build_state, dict):
        return {}
    patches = plan_build_state.get("patches") or {}
    patch_status: dict[str, str] = {}
    valid_patch_types: list[str] = []
    if isinstance(patches, dict):
        for patch_type, patch_info in patches.items():
            status = patch_info.get("status") if isinstance(patch_info, dict) else None
            if isinstance(status, str):
                patch_status[str(patch_type)] = status
                if status == "valid":
                    valid_patch_types.append(str(patch_type))
    component_tasks = plan_build_state.get("component_tasks") or []
    patch_order = [
        task.get("patch_type")
        for task in component_tasks
        if isinstance(task, dict) and isinstance(task.get("patch_type"), str)
    ]
    return {
        "state_id": plan_build_state.get("state_id"),
        "patch_status": patch_status,
        "valid_patch_types": valid_patch_types,
        "patch_order": patch_order,
        "event_count": len(plan_build_state.get("events") or []),
    }


def _plan_artifacts(state: dict[str, Any]) -> list[str] | dict[str, Any]:
    artifacts = state.get("plan_artifacts")
    if isinstance(artifacts, (list, dict)):
        return artifacts
    return []


def _artifact_keys(artifacts: list[str] | dict[str, Any]) -> list[str]:
    keys = {"workflow_trace"}
    if isinstance(artifacts, dict):
        keys.update(str(key) for key in artifacts)
    else:
        for artifact in artifacts:
            path = Path(str(artifact))
            keys.add(path.stem or path.name)
    keys.add("capability_report")
    return sorted(keys)


def _issue_codes_from_state(
    state: dict[str, Any],
    validation_report: dict[str, Any],
    incremental_result: Any,
) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []

    def add(code: Any) -> None:
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            codes.append(code)

    for code in validation_report.get("issue_codes") or []:
        add(code)
    if isinstance(incremental_result, dict):
        for issue in incremental_result.get("issues") or []:
            if isinstance(issue, dict):
                add(issue.get("code"))
    error = state.get("error")
    if isinstance(error, str) and error:
        add(error.split(":", 1)[0])
    return codes


def _failed_stage_from_state(state: dict[str, Any]) -> str | None:
    error = state.get("error")
    if not error:
        return None
    text = str(error)
    if text.startswith("incremental."):
        return "generate_plan"
    if state.get("validation_report") is not None:
        return "validate_plan"
    return "workflow"


def _failed_patch_type_from_state(state: dict[str, Any], incremental_result: Any) -> str | None:
    if isinstance(incremental_result, dict):
        summary = incremental_result.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("failed_patch_type"), str):
            return summary["failed_patch_type"]
        if isinstance(incremental_result.get("failed_patch_type"), str):
            return incremental_result["failed_patch_type"]
    return None


def _plan_schema_success(state: dict[str, Any], validation_report: dict[str, Any]) -> bool | None:
    if state.get("simulation_plan") is not None:
        if isinstance(validation_report.get("is_valid"), bool):
            return validation_report["is_valid"]
        return True
    if state.get("error"):
        return False
    return None


def _incremental_success(incremental_result: Any) -> bool | None:
    if isinstance(incremental_result, dict) and isinstance(incremental_result.get("ok"), bool):
        return incremental_result["ok"]
    return None


def _append_ablation_metadata(trace: WorkflowTrace, metadata: dict[str, Any]) -> None:
    recorder = TraceRecorder(trace=trace)
    recorder.add_event(
        "workflow_completed" if trace.final_status != "failed" else "workflow_failed",
        summary="benchmark ablation metadata",
        metadata={"requested_ablation": metadata},
    )


def _safe_model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"repr": repr(value)}
