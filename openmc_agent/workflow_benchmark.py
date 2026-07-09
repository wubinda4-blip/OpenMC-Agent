"""Workflow benchmark runner for P0 evaluation traces.

This module wires the lightweight workflow case runner into a simple benchmark
entry point that writes machine-readable reports, markdown summaries, per-case
traces, and per-case artifact directories. It intentionally does not replace the
offline ``benchmark_runner`` harness and does not call real LLMs unless the
caller explicitly opts in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from openmc_agent.benchmark_runner import AblationConfig, fake_case_runner, load_evaluation_cases
from openmc_agent.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationResult,
    aggregate_evaluation_results,
    evaluate_trace_against_case,
)
from openmc_agent.schemas import AgentBaseModel
from openmc_agent.workflow_case_runner import WorkflowCaseRunnerConfig, run_workflow_case
from openmc_agent.workflow_trace import WorkflowTrace, save_trace_json


class WorkflowBenchmarkConfig(AgentBaseModel):
    cases_path: str | Path
    output_dir: str | Path = "data/evals/workflow"
    model: str = "fake"
    mode: Literal["plan_only", "render_only", "smoke_test"] = "plan_only"

    use_incremental_executor: bool = True
    reference_patch_policy: str = "off"

    enable_retrieval: bool = True
    enable_graph_retrieval: bool = True

    enable_render: bool = False
    enable_openmc_tools: bool = False

    write_traces: bool = True
    write_case_artifacts: bool = True

    max_cases: int | None = None
    categories: list[str] = Field(default_factory=list)

    allow_real_llm: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowBenchmarkCaseResult(AgentBaseModel):
    case_id: str
    passed: bool
    failed_stage: str | None = None
    failed_patch_type: str | None = None
    issue_codes: list[str] = Field(default_factory=list)
    planning_mode: str | None = None
    renderability: str | None = None
    supported_renderer: str | None = None
    artifact_complete: bool | None = None
    trace_path: str | None = None
    artifact_dir: str | None = None
    failure_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class WorkflowBenchmarkResult(AgentBaseModel):
    run_id: str
    model: str
    mode: str
    case_count: int
    metrics: EvaluationMetrics
    cases: list[WorkflowBenchmarkCaseResult]
    output_dir: str
    report_path: str | None = None
    summary_path: str | None = None


def run_workflow_benchmark(config: WorkflowBenchmarkConfig) -> WorkflowBenchmarkResult:
    """Run a workflow benchmark and write report artifacts."""
    if config.model != "fake" and not config.allow_real_llm:
        raise ValueError("Refusing to run real LLM benchmark without --allow-real-llm.")

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    traces_dir = output_dir / "traces"
    artifacts_root = output_dir / "case_artifacts"
    if config.write_traces:
        traces_dir.mkdir(parents=True, exist_ok=True)
    if config.write_case_artifacts:
        artifacts_root.mkdir(parents=True, exist_ok=True)

    cases = load_evaluation_cases(
        config.cases_path,
        categories=config.categories or None,
        max_cases=config.max_cases,
    )
    evaluations: list[EvaluationResult] = []
    case_results: list[WorkflowBenchmarkCaseResult] = []

    for case in cases:
        trace = _run_case(case, config)
        trace_path = _write_case_trace(trace, case, traces_dir, enabled=config.write_traces)
        artifact_dir = _prepare_case_artifact_dir(
            case,
            artifacts_root,
            enabled=config.write_case_artifacts,
        )
        evaluation = evaluate_trace_against_case(trace, case)
        evaluations.append(evaluation)
        case_result = _case_result_from_evaluation(
            case=case,
            evaluation=evaluation,
            trace_path=trace_path,
            artifact_dir=artifact_dir,
        )
        case_results.append(case_result)
        if artifact_dir is not None:
            _write_case_artifact_summary(case_result, artifacts_root / case.case_id)

    result = WorkflowBenchmarkResult(
        run_id=_new_run_id(),
        model=config.model,
        mode=config.mode,
        case_count=len(case_results),
        metrics=aggregate_evaluation_results(evaluations),
        cases=case_results,
        output_dir=str(output_dir),
    )
    report_path = write_workflow_evaluation_report(result, output_dir)
    summary_path = write_workflow_benchmark_summary(result, output_dir)
    return result.model_copy(
        update={"report_path": str(report_path), "summary_path": str(summary_path)}
    )


def write_workflow_evaluation_report(
    result: WorkflowBenchmarkResult,
    output_dir: str | Path,
) -> Path:
    """Write ``evaluation_report.json`` for a workflow benchmark result."""
    path = Path(output_dir) / "evaluation_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_workflow_benchmark_summary(
    result: WorkflowBenchmarkResult,
    output_dir: str | Path,
) -> Path:
    """Write a compact markdown summary for a workflow benchmark result."""
    path = Path(output_dir) / "benchmark_summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = result.metrics
    lines = [
        "# Workflow Benchmark Summary",
        "",
        f"- run_id: {result.run_id}",
        f"- model: {result.model}",
        f"- mode: {result.mode}",
        f"- cases: {result.case_count}",
        f"- pass rate: {_format_rate(metrics.pass_rate)}",
        f"- plan schema success rate: {_format_optional_rate(metrics.plan_schema_success_rate)}",
        f"- incremental patch success rate: {_format_optional_rate(metrics.incremental_patch_success_rate)}",
        f"- artifact completeness rate: {_format_optional_rate(metrics.artifact_completeness_rate)}",
        f"- planning mode accuracy: {_format_optional_rate(metrics.planning_mode_accuracy)}",
        f"- issue precision: {_format_optional_rate(metrics.issue_code_precision)}",
        f"- issue recall: {_format_optional_rate(metrics.issue_code_recall)}",
        "",
        "## Failed cases",
        "",
        "| case_id | failed_stage | failed_patch_type | issue_codes | failure_reasons |",
        "| --- | --- | --- | --- | --- |",
    ]
    failed_cases = [case for case in result.cases if not case.passed]
    if failed_cases:
        for case in failed_cases:
            lines.append(
                "| {case_id} | {stage} | {patch} | {issues} | {reasons} |".format(
                    case_id=case.case_id,
                    stage=case.failed_stage or "",
                    patch=case.failed_patch_type or "",
                    issues=", ".join(case.issue_codes),
                    reasons="; ".join(case.failure_reasons),
                )
            )
    else:
        lines.append("| _none_ |  |  |  |  |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_case(case: EvaluationCase, config: WorkflowBenchmarkConfig) -> WorkflowTrace:
    if config.model == "fake":
        return fake_case_runner(case, AblationConfig(name="workflow_fake"))
    runner_config = WorkflowCaseRunnerConfig(
        model=config.model,
        output_dir=str(Path(config.output_dir) / "case_artifacts"),
        mode=config.mode,
        use_incremental_executor=config.use_incremental_executor,
        reference_patch_policy=config.reference_patch_policy,
        enable_retrieval=config.enable_retrieval,
        enable_graph_retrieval=config.enable_graph_retrieval,
        enable_render=config.enable_render,
        enable_openmc_tools=config.enable_openmc_tools,
        allow_monolithic_fallback_for_incremental_failure=False,
        metadata=config.metadata,
    )
    return run_workflow_case(case, runner_config)


def _write_case_trace(
    trace: WorkflowTrace,
    case: EvaluationCase,
    traces_dir: Path,
    *,
    enabled: bool,
) -> str | None:
    if not enabled:
        return None
    path = traces_dir / f"{case.case_id}.json"
    try:
        save_trace_json(trace, path)
    except Exception:
        fallback = {
            "case_id": case.case_id,
            "events": [event.model_dump(mode="json") for event in trace.events],
            "metadata": {"trace_write_fallback": True},
        }
        path.write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
    return _relative_to(path, traces_dir.parent)


def _prepare_case_artifact_dir(
    case: EvaluationCase,
    artifacts_root: Path,
    *,
    enabled: bool,
) -> str | None:
    if not enabled:
        return None
    path = artifacts_root / case.case_id
    path.mkdir(parents=True, exist_ok=True)
    return _relative_to(path, artifacts_root.parent)


def _write_case_artifact_summary(
    case_result: WorkflowBenchmarkCaseResult,
    artifact_dir: Path,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "case_result.json").write_text(
        json.dumps(case_result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _case_result_from_evaluation(
    *,
    case: EvaluationCase,
    evaluation: EvaluationResult,
    trace_path: str | None,
    artifact_dir: str | None,
) -> WorkflowBenchmarkCaseResult:
    return WorkflowBenchmarkCaseResult(
        case_id=case.case_id,
        passed=evaluation.passed,
        failed_stage=_str_metric(evaluation.metrics.get("failed_stage")),
        failed_patch_type=_str_metric(evaluation.metrics.get("failed_patch_type")),
        issue_codes=list(evaluation.observed_issue_codes),
        planning_mode=_str_metric(evaluation.metrics.get("planning_mode")),
        renderability=evaluation.observed_renderability,
        supported_renderer=evaluation.observed_supported_renderer,
        artifact_complete=_bool_or_none(evaluation.metrics.get("artifact_complete")),
        trace_path=trace_path,
        artifact_dir=artifact_dir,
        failure_reasons=list(evaluation.failure_reasons),
        metrics=dict(evaluation.metrics),
    )


def _relative_to(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _str_metric(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _format_rate(value: float) -> str:
    return f"{value:.1%}"


def _format_optional_rate(value: float | None) -> str:
    return "n/a" if value is None else _format_rate(value)


def _new_run_id() -> str:
    return f"workflow_{uuid4().hex[:12]}"
