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
    enable_semantic_audit: bool = False
    semantic_audit_mode: Literal["warning_only", "strict_evaluation"] = "warning_only"
    semantic_audit_model: str | None = None
    semantic_audit_allow_fallback: bool = True
    enable_llm_repair: bool = False
    llm_repair_mode: Literal["proposal_only", "validate_only", "apply_if_safe"] = "proposal_only"
    llm_repair_model: str | None = None
    llm_repair_allow_fallback: bool = True
    llm_repair_max_proposals: int = 1
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
            case_artifact_path = artifacts_root / case.case_id
            _write_case_artifact_summary(case_result, case_artifact_path)
            if config.enable_llm_repair:
                _write_repair_artifact_stub(case_result, case_artifact_path)

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
        "## Semantic Audit",
        "",
        f"- enabled: {any(c.metrics.get('semantic_audit_enabled') for c in result.cases)}",
        f"- completion rate: {_format_optional_rate(metrics.semantic_audit_completion_rate)}",
        f"- fallback rate: {_format_optional_rate(metrics.semantic_audit_fallback_rate)}",
        f"- finding precision: {_format_optional_rate(metrics.semantic_audit_finding_precision)}",
        f"- finding recall: {_format_optional_rate(metrics.semantic_audit_finding_recall)}",
        f"- false positive rate: {_format_optional_rate(metrics.semantic_audit_false_positive_rate)}",
        f"- known error detection rate: {_format_optional_rate(metrics.semantic_audit_known_error_detection_rate)}",
        "",
        "## LLM Repair Proposals",
        "",
        f"- completion rate: {_format_optional_rate(metrics.llm_repair_completion_rate)}",
        f"- acceptance rate: {_format_optional_rate(metrics.llm_repair_acceptance_rate)}",
        f"- rejection rate: {_format_optional_rate(metrics.llm_repair_rejection_rate)}",
        f"- unsafe rate: {_format_optional_rate(metrics.llm_repair_unsafe_rate)}",
        f"- fallback rate: {_format_optional_rate(metrics.llm_repair_fallback_rate)}",
        f"- issue resolution rate: {_format_optional_rate(metrics.llm_repair_issue_resolution_rate)}",
        f"- new issue rate: {_format_optional_rate(metrics.llm_repair_new_issue_rate)}",
        "",
        "| case_id | status | source_issues | operations | resolved | new_issues | applied |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        * _repair_rows(result),
        "",
        "| case_id | operation | path | rejection_code |",
        "| --- | --- | --- | --- |",
        * _repair_unsafe_rows(result),
        "",
        "| case_id | finding_code | severity | patch_target | confidence | human_confirmation |",
        "| --- | --- | --- | --- | --- | --- |",
        * _semantic_finding_rows(result),
        "",
        "| case_id | missing_finding_code |",
        "| --- | --- |",
        * _semantic_missing_rows(result),
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
        trace = fake_case_runner(case, AblationConfig(name="workflow_fake"))
        if config.enable_semantic_audit:
            trace = _augment_fake_trace_with_semantic_audit(trace, case, config)
        if config.enable_llm_repair:
            trace = _augment_fake_trace_with_llm_repair(trace, case, config)
        return trace
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
        enable_semantic_audit=config.enable_semantic_audit,
        semantic_audit_mode=config.semantic_audit_mode.replace("-", "_"),
        semantic_audit_model=config.semantic_audit_model,
        semantic_audit_allow_fallback=config.semantic_audit_allow_fallback,
        enable_llm_repair_proposer=config.enable_llm_repair,
        llm_repair_mode=config.llm_repair_mode.replace("-", "_"),
        llm_repair_model=config.llm_repair_model,
        llm_repair_allow_fallback=config.llm_repair_allow_fallback,
        llm_repair_max_proposals=config.llm_repair_max_proposals,
    )
    return run_workflow_case(case, runner_config)



def _write_repair_artifact_stub(
    case_result: WorkflowBenchmarkCaseResult,
    artifact_dir: Path,
) -> None:
    if not case_result.metrics.get("llm_repair_enabled"):
        return
    proposal_id = f"repair_{case_result.case_id}"
    root = artifact_dir / "repair_proposals" / proposal_id
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "proposal_id": proposal_id,
        "status": case_result.metrics.get("llm_repair_status"),
        "source_issue_codes": case_result.metrics.get("llm_repair_source_issue_codes") or [],
        "operation_count": case_result.metrics.get("llm_repair_operation_count") or 0,
    }
    for name in (
        "input.json",
        "proposal.json",
        "operation_evaluations.json",
        "validation_before.json",
        "validation_after.json",
        "result.json",
    ):
        (root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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


def _augment_fake_trace_with_semantic_audit(trace: WorkflowTrace, case: EvaluationCase, config: WorkflowBenchmarkConfig) -> WorkflowTrace:
    from openmc_agent.workflow_trace import TraceRecorder
    recorder = TraceRecorder(trace=trace)
    codes = list(case.expected_audit_finding_codes)
    findings = [
        {
            "finding_code": code,
            "severity": "error" if "conflict" in code or "partial_insert" in code else "warning",
            "suggested_patch_target": "pin_map" if "axial" in code else "none",
            "confidence": 0.9,
            "requires_human_confirmation": bool(case.expected_audit_requires_human_confirmation),
        }
        for code in codes
    ]
    meta = {
        "audit_id": f"fake_{case.case_id}",
        "auditor": "FakeSemanticAuditClient",
        "model": config.semantic_audit_model or config.model,
        "finding_count": len(codes),
        "finding_codes": codes,
        "findings": findings,
        "severity_counts": {"error": sum(1 for f in findings if f["severity"] == "error"), "warning": sum(1 for f in findings if f["severity"] == "warning")},
        "fallback_used": bool(config.semantic_audit_allow_fallback and case.expected_semantic_audit_fallback_used),
        "duration_ms": 0.0,
        "mode": config.semantic_audit_mode.replace("-", "_"),
    }
    recorder.add_event("semantic_audit_started", summary="fake semantic audit started", metadata={"audit_id": meta["audit_id"]})
    recorder.add_event("semantic_audit_completed", summary="fake semantic audit completed", metadata=meta)
    if meta["fallback_used"]:
        recorder.add_event("semantic_audit_fallback_used", summary="fake semantic audit fallback", metadata=meta)
    return recorder.trace

def _semantic_finding_rows(result: WorkflowBenchmarkResult) -> list[str]:
    rows: list[str] = []
    for case in result.cases:
        codes = case.metrics.get("semantic_audit_finding_codes") or []
        for code in codes:
            rows.append(f"| {case.case_id} | {code} |  |  |  |  |")
    return rows or ["| _none_ |  |  |  |  |"]

def _semantic_missing_rows(result: WorkflowBenchmarkResult) -> list[str]:
    rows: list[str] = []
    for case in result.cases:
        observed = set(case.metrics.get("semantic_audit_finding_codes") or [])
        # expected codes are not stored on case result; failures carry the missing detail.
        for reason in case.failure_reasons:
            if reason.startswith("missing expected audit finding codes:"):
                for code in reason.split(":", 1)[1].split(","):
                    rows.append(f"| {case.case_id} | {code.strip()} |")
    return rows or ["| _none_ |  |"]


def _augment_fake_trace_with_llm_repair(trace: WorkflowTrace, case: EvaluationCase, config: WorkflowBenchmarkConfig) -> WorkflowTrace:
    from openmc_agent.workflow_trace import TraceRecorder
    recorder = TraceRecorder(trace=trace)
    status = case.expected_repair_status or "proposed"
    source = list(case.expected_repair_source_issue_codes or case.expected_audit_finding_codes or case.expected_issue_codes)
    resolved = list(case.expected_repair_resolved_issue_codes or ([] if status != "accepted" else source[:1]))
    applied_clone = bool(case.expected_repair_applied_to_clone) if case.expected_repair_applied_to_clone is not None else status == "accepted" and config.llm_repair_mode in {"validate_only", "apply_if_safe"}
    applied_workflow = bool(case.expected_repair_applied_to_workflow_plan) if case.expected_repair_applied_to_workflow_plan is not None else status == "accepted" and config.llm_repair_mode == "apply_if_safe"
    operation_count = 0 if case.expected_repair_requires_human_confirmation else (1 if source else 0)
    evaluations = []
    if operation_count:
        path = (case.forbidden_repair_paths or case.expected_repair_allowed_paths or ["/metadata/repair_requests/0"])[0]
        evaluations.append({"index": 0, "op": "replace", "path": path, "allowed": status != "unsafe", "risk_level": "forbidden" if status == "unsafe" else "low", "rejection_codes": [] if status != "unsafe" else ["repair.protected_path"]})
    meta = {
        "proposal_id": f"fake_repair_{case.case_id}",
        "mode": config.llm_repair_mode.replace("-", "_"),
        "model": config.llm_repair_model or config.model,
        "source_issue_codes": source,
        "source_audit_finding_codes": list(case.expected_audit_finding_codes),
        "operation_count": operation_count,
        "allowed_operation_count": sum(1 for ev in evaluations if ev["allowed"]),
        "rejected_operation_count": sum(1 for ev in evaluations if not ev["allowed"]),
        "unsafe_operation_count": 1 if status == "unsafe" else 0,
        "status": status,
        "resolved_issue_codes": resolved,
        "remaining_issue_codes": [],
        "new_issue_codes": [],
        "applied_to_clone": applied_clone,
        "applied_to_workflow_plan": applied_workflow,
        "duration_ms": 0.0,
        "fallback_used": bool(case.expected_repair_fallback_used),
        "requires_human_confirmation": bool(case.expected_repair_requires_human_confirmation),
        "operation_evaluations": evaluations,
    }
    recorder.add_event("llm_repair_proposal_started", summary="fake repair started", metadata={"proposal_id": meta["proposal_id"], "mode": meta["mode"]})
    recorder.add_event("llm_repair_proposal_generated", summary="fake repair generated", metadata=meta)
    if status in {"accepted", "rejected", "unsafe", "failed"}:
        recorder.add_event(f"llm_repair_proposal_{status}", summary=f"fake repair {status}", metadata=meta)
    if meta["fallback_used"]:
        recorder.add_event("llm_repair_fallback_used", summary="fake repair fallback", metadata=meta)
    return recorder.trace

def _repair_rows(result: WorkflowBenchmarkResult) -> list[str]:
    rows: list[str] = []
    for case in result.cases:
        if not case.metrics.get("llm_repair_enabled"):
            continue
        rows.append("| {case_id} | {status} | {source} | {ops} | {resolved} | {new} | {applied} |".format(
            case_id=case.case_id,
            status=case.metrics.get("llm_repair_status") or "",
            source=", ".join(case.metrics.get("llm_repair_source_issue_codes") or []),
            ops=case.metrics.get("llm_repair_operation_count") or 0,
            resolved=case.metrics.get("llm_repair_resolved_issue_count") or 0,
            new=case.metrics.get("llm_repair_new_issue_count") or 0,
            applied=case.metrics.get("llm_repair_applied_to_workflow_plan"),
        ))
    return rows or ["| _none_ |  |  |  |  |  |  |"]

def _repair_unsafe_rows(result: WorkflowBenchmarkResult) -> list[str]:
    rows: list[str] = []
    for case in result.cases:
        if (case.metrics.get("llm_repair_unsafe_operation_count") or 0) > 0:
            rows.append(f"| {case.case_id} | 0 |  | repair.protected_path |")
    return rows or ["| _none_ |  |  |  |"]
