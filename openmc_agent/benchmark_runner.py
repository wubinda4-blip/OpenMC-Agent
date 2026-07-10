"""Benchmark and ablation runner for OpenMC Agent workflows (Step 7).

This module is a thin, offline, reproducible harness. It does not call real
LLMs or OpenMC and does not hardcode the production workflow. Instead it accepts
an injectable ``case_runner`` callable that produces a :class:`WorkflowTrace`
for one :class:`EvaluationCase` under one :class:`AblationConfig`.

Trace is the source of truth: every case is scored by
:func:`openmc_agent.evaluation.evaluate_trace_against_case`, aggregated by
:func:`openmc_agent.evaluation.aggregate_evaluation_results`, and optionally
persisted as JSON / JSONL / Markdown artifacts. Ablations are expressed as
policy configuration (see :func:`retrieval_policy_from_ablation`), not as
scattered code branches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import Field

from openmc_agent.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationResult,
    aggregate_evaluation_results,
    evaluate_trace_against_case,
)
from openmc_agent.retrieval_orchestrator import RetrievalPolicy
from openmc_agent.schemas import AgentBaseModel
from openmc_agent.workflow_trace import (
    TraceRecorder,
    WorkflowTrace,
    save_trace_json,
    save_trace_jsonl,
)


CaseRunner = Callable[[EvaluationCase, "AblationConfig"], WorkflowTrace]


# ---------------------------------------------------------------------------
# Configuration and result models
# ---------------------------------------------------------------------------


class BenchmarkConfig(AgentBaseModel):
    """Run-level configuration for a benchmark or ablation study."""

    run_id: str | None = None
    name: str = "openmc_agent_benchmark"
    output_dir: str | None = None
    save_traces: bool = True
    save_jsonl: bool = True
    save_markdown: bool = True
    max_cases: int | None = None
    categories: list[str] = Field(default_factory=list)


class AblationConfig(AgentBaseModel):
    """Which optional capabilities are enabled for one ablation arm.

    ``enable_grep`` / ``enable_graph`` / ``enable_rag`` map directly onto a
    :class:`RetrievalPolicy`. ``enable_auto_repair`` / ``enable_reflect_plan``
    / ``enable_ask_expert`` are passed through to the case runner as metadata;
    they are not yet wired into the production workflow.
    """

    name: str
    enable_grep: bool = True
    enable_graph: bool = True
    enable_rag: bool = True
    enable_auto_repair: bool = True
    enable_reflect_plan: bool = True
    enable_ask_expert: bool = True
    notes: str = ""


class BenchmarkCaseResult(AgentBaseModel):
    case: EvaluationCase
    trace: WorkflowTrace
    evaluation: EvaluationResult
    ablation_name: str
    warnings: list[str] = Field(default_factory=list)


class BenchmarkRunResult(AgentBaseModel):
    run_id: str
    benchmark_name: str
    ablation_name: str
    case_results: list[BenchmarkCaseResult] = Field(default_factory=list)
    metrics: EvaluationMetrics | None = None
    warnings: list[str] = Field(default_factory=list)


class AblationStudyResult(AgentBaseModel):
    run_id: str
    benchmark_name: str
    ablation_results: list[BenchmarkRunResult] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


DEFAULT_ABLATIONS: list[AblationConfig] = [
    AblationConfig(name="full_stack"),
    AblationConfig(name="no_grep", enable_grep=False),
    AblationConfig(name="no_graph", enable_graph=False),
    AblationConfig(name="no_rag", enable_rag=False),
    AblationConfig(
        name="no_retrieval", enable_grep=False, enable_graph=False, enable_rag=False
    ),
    AblationConfig(name="no_auto_repair", enable_auto_repair=False),
]


# ---------------------------------------------------------------------------
# Evaluation case loader
# ---------------------------------------------------------------------------


def load_evaluation_cases(
    path: str | Path,
    *,
    categories: list[str] | None = None,
    max_cases: int | None = None,
) -> list[EvaluationCase]:
    """Load evaluation cases from a JSON file.

    Accepts either a JSON list of case objects or a single object of the form
    ``{"cases": [...]}``. Each entry is validated with :class:`EvaluationCase`;
    validation errors are raised as :class:`ValueError` with the offending
    index so callers can fix the manifest quickly.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("cases")
        if not isinstance(items, list):
            raise ValueError(
                "evaluation cases JSON object must contain a 'cases' list"
            )
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("evaluation cases JSON must be a list or {'cases': [...]}")

    cases: list[EvaluationCase] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"evaluation case #{index} is not an object")
        try:
            cases.append(EvaluationCase(**item))
        except Exception as exc:  # pydantic ValidationError or TypeError
            raise ValueError(f"invalid evaluation case #{index}: {exc}") from exc

    if categories:
        wanted = set(categories)
        cases = [case for case in cases if case.category in wanted]
    if max_cases is not None:
        cases = cases[: max(0, max_cases)]
    return cases


# ---------------------------------------------------------------------------
# Ablation -> RetrievalPolicy
# ---------------------------------------------------------------------------


def retrieval_policy_from_ablation(ablation: AblationConfig) -> RetrievalPolicy:
    """Map ablation retrieval toggles onto a :class:`RetrievalPolicy`.

    Other ablation toggles (auto-repair, reflect, ask-expert) are not part of
    retrieval policy and are intentionally ignored here; the case runner reads
    them directly from the ablation config.
    """
    return RetrievalPolicy(
        enable_grep=ablation.enable_grep,
        enable_graph=ablation.enable_graph,
        enable_rag=ablation.enable_rag,
    )


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(
    cases: list[EvaluationCase],
    case_runner: CaseRunner,
    ablation: AblationConfig | None = None,
    config: BenchmarkConfig | None = None,
) -> BenchmarkRunResult:
    """Run one ablation arm across a set of cases using an injectable runner.

    Each case is scored against its produced trace. A single case failure
    (exception or contract mismatch) is captured as a warning and a failing
    case result; it never aborts the overall run.
    """
    active_config = config or BenchmarkConfig()
    active_ablation = ablation or AblationConfig(name="default")
    run_id = active_config.run_id or _generate_run_id()

    filtered = _apply_case_filters(cases, active_config)
    case_results: list[BenchmarkCaseResult] = []
    run_warnings: list[str] = []

    for case in filtered:
        case_result = _run_single_case(case, case_runner, active_ablation)
        case_results.append(case_result)
        run_warnings.extend(
            f"{case.case_id}: {warning}" for warning in case_result.warnings
        )

    evaluations = [cr.evaluation for cr in case_results]
    metrics = aggregate_evaluation_results(evaluations)
    result = BenchmarkRunResult(
        run_id=run_id,
        benchmark_name=active_config.name,
        ablation_name=active_ablation.name,
        case_results=case_results,
        metrics=metrics,
        warnings=run_warnings,
    )

    if active_config.output_dir is not None:
        _write_run_artifacts(result, active_config)
    return result


def run_ablation_study(
    cases: list[EvaluationCase],
    case_runner: CaseRunner,
    ablations: list[AblationConfig] | None = None,
    config: BenchmarkConfig | None = None,
) -> AblationStudyResult:
    """Run several ablation arms over the same case set and compare them."""
    active_config = config or BenchmarkConfig()
    arms = list(ablations or DEFAULT_ABLATIONS)
    run_id = active_config.run_id or _generate_run_id()
    study_config = active_config.model_copy(update={"run_id": run_id})

    ablation_results: list[BenchmarkRunResult] = []
    study_warnings: list[str] = []
    for arm in arms:
        arm_result = run_benchmark(cases, case_runner, ablation=arm, config=study_config)
        ablation_results.append(arm_result)
        study_warnings.extend(
            f"{arm.name}: {warning}" for warning in arm_result.warnings
        )

    comparison = _build_ablation_comparison(ablation_results)
    study_result = AblationStudyResult(
        run_id=run_id,
        benchmark_name=active_config.name,
        ablation_results=ablation_results,
        comparison=comparison,
        warnings=study_warnings,
    )

    if active_config.output_dir is not None:
        _write_ablation_artifacts(study_result, study_config)
    return study_result


# ---------------------------------------------------------------------------
# Markdown summaries
# ---------------------------------------------------------------------------


def format_benchmark_summary(result: BenchmarkRunResult) -> str:
    """Render a short markdown summary for a single benchmark run."""
    metrics = result.metrics
    case_count = len(result.case_results)
    pass_rate = metrics.pass_rate if metrics else 0.0
    retrieval_rate = _safe_rate(metrics.retrieval_trigger_rate if metrics else None)
    human_rate = _safe_rate(metrics.human_confirmation_rate if metrics else None)
    precision = _safe_rate(metrics.issue_code_precision if metrics else None)
    recall = _safe_rate(metrics.issue_code_recall if metrics else None)

    lines = [
        f"# Benchmark: {result.benchmark_name} / {result.ablation_name}",
        "",
        f"- Cases: {case_count}",
        f"- Pass rate: {pass_rate:.1%}",
        f"- Retrieval trigger rate: {retrieval_rate:.1%}",
        f"- Human confirmation rate: {human_rate:.1%}",
        f"- Issue precision: {precision:.1%}",
        f"- Issue recall: {recall:.1%}",
    ]
    if result.warnings:
        lines.append(f"- Warnings: {len(result.warnings)}")

    failed = [cr for cr in result.case_results if not cr.evaluation.passed]
    if failed:
        lines.append("")
        lines.append("## Failed cases")
        lines.append("")
        lines.append("| case_id | category | reasons |")
        lines.append("| --- | --- | --- |")
        for case_result in failed:
            reasons = "; ".join(case_result.evaluation.failure_reasons) or "unknown"
            lines.append(
                f"| {case_result.case.case_id} | {case_result.case.category} | {reasons} |"
            )
    return "\n".join(lines) + "\n"


def format_ablation_summary(result: AblationStudyResult) -> str:
    """Render a short markdown comparison table for an ablation study."""
    lines = [
        f"# Ablation Study: {result.benchmark_name}",
        "",
        "| ablation | cases | pass rate | retrieval trigger rate | human confirmation rate | issue precision | issue recall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run_result in result.ablation_results:
        metrics = run_result.metrics
        case_count = metrics.case_count if metrics else len(run_result.case_results)
        lines.append(
            "| {name} | {cases} | {pass_rate} | {retrieval} | {human} | {precision} | {recall} |".format(
                name=run_result.ablation_name,
                cases=case_count,
                pass_rate=_format_rate(metrics.pass_rate if metrics else 0.0),
                retrieval=_format_rate(
                    metrics.retrieval_trigger_rate if metrics else None
                ),
                human=_format_rate(
                    metrics.human_confirmation_rate if metrics else None
                ),
                precision=_format_rate(metrics.issue_code_precision if metrics else None),
                recall=_format_rate(metrics.issue_code_recall if metrics else None),
            )
        )
    if result.warnings:
        lines.append("")
        lines.append(f"_Warnings: {len(result.warnings)}_")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fake / demo case runner (offline, no LLM, no OpenMC)
# ---------------------------------------------------------------------------


def fake_case_runner(case: EvaluationCase, ablation: AblationConfig) -> WorkflowTrace:
    """Generate a trace from a case's expected contract.

    Demo / test only. Does not call a real LLM or OpenMC. The trace mirrors
    what the case expects so the full-stack arm passes, while retrieval /
    ask-expert events are gated by the ablation toggles so that disabled arms
    produce visibly different traces.
    """
    recorder = TraceRecorder()
    recorder.trace.user_request_preview = (case.user_request or "")[:200]

    if case.expected_issue_codes:
        recorder.add_event(
            "validation_completed",
            issue_codes=list(case.expected_issue_codes),
            summary=f"fake validation for {case.case_id}",
        )

    retrieval_enabled = ablation.enable_grep or ablation.enable_graph or ablation.enable_rag
    if retrieval_enabled and case.should_trigger_retrieval:
        recorder.add_event(
            "retrieval_started",
            summary="fake retrieval",
        )
        recorder.add_event(
            "retrieval_completed",
            summary="fake retrieval completed",
        )

    if ablation.enable_ask_expert and case.should_require_human_confirmation:
        recorder.add_event(
            "ask_expert_started",
            summary="fake ask expert",
            metadata={"requires_human_confirmation_count": 1},
        )

    p0_metadata = {
        "planning_mode": case.expected_planning_mode,
        "plan_schema_success": case.expected_plan_schema_success,
        "incremental_patch_success": case.expected_incremental_patch_success,
        "artifact_keys": list(case.expected_artifact_keys),
        "patch_status": {
            patch_type: "valid" for patch_type in case.expected_incremental_patch_types
        },
        "valid_patch_types": list(case.expected_incremental_patch_types),
        "failed_stage": case.expected_failed_stage,
        "failed_patch_type": case.expected_failed_patch_type,
    }
    p0_metadata = {k: v for k, v in p0_metadata.items() if v not in (None, [], {})}

    if case.expected_repair_status:
        repair_meta = {
            "proposal_id": f"fake_repair_{case.case_id}",
            "status": case.expected_repair_status,
            "source_issue_codes": list(case.expected_repair_source_issue_codes),
            "source_audit_finding_codes": list(case.expected_audit_finding_codes),
            "operation_count": 1 if case.expected_repair_status != "proposed" else 0,
            "allowed_operation_count": 1 if case.expected_repair_status == "accepted" else 0,
            "rejected_operation_count": 1 if case.expected_repair_status in {"rejected", "unsafe"} else 0,
            "unsafe_operation_count": 1 if case.expected_repair_status == "unsafe" else 0,
            "resolved_issue_codes": list(case.expected_repair_resolved_issue_codes),
            "remaining_issue_codes": [],
            "new_issue_codes": [],
            "applied_to_clone": bool(case.expected_repair_applied_to_clone),
            "applied_to_workflow_plan": bool(case.expected_repair_applied_to_workflow_plan),
            "fallback_used": bool(case.expected_repair_fallback_used),
            "requires_human_confirmation": bool(case.expected_repair_requires_human_confirmation),
            "operation_evaluations": [
                {
                    "path": (case.expected_repair_allowed_paths or ["/metadata/repair_requests/0"])[0],
                    "allowed": case.expected_repair_status == "accepted",
                    "rejection_codes": [] if case.expected_repair_status == "accepted" else ["repair.protected_path"],
                }
            ],
        }
        recorder.add_event("llm_repair_proposal_generated", metadata=repair_meta)
        if case.expected_repair_status in {"accepted", "rejected", "unsafe", "failed"}:
            recorder.add_event(f"llm_repair_proposal_{case.expected_repair_status}", metadata=repair_meta)

    if case.expected_renderability:
        recorder.add_event(
            "capability_assessed",
            renderability=case.expected_renderability,
            supported_renderer=case.expected_supported_renderer,
            metadata=p0_metadata,
        )
        recorder.add_event(
            "workflow_completed",
            renderability=case.expected_renderability,
            supported_renderer=case.expected_supported_renderer,
            metadata=p0_metadata,
        )
        recorder.trace.final_renderability = case.expected_renderability
        recorder.trace.final_supported_renderer = case.expected_supported_renderer
        recorder.trace.final_status = (
            "valid" if case.expected_renderability == "runnable" else "skeleton"
        )
    else:
        recorder.add_event("workflow_completed", summary="fake workflow completed", metadata=p0_metadata)

    return recorder.trace


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_single_case(
    case: EvaluationCase,
    case_runner: CaseRunner,
    ablation: AblationConfig,
) -> BenchmarkCaseResult:
    warnings: list[str] = []
    try:
        trace = case_runner(case, ablation)
    except Exception as exc:  # case runners must never break the whole run
        warning = f"case_runner raised {type(exc).__name__}: {exc}"
        warnings.append(warning)
        recorder = TraceRecorder()
        recorder.trace.user_request_preview = (case.user_request or "")[:200]
        recorder.add_event(
            "workflow_failed",
            summary=f"case_runner exception for {case.case_id}",
            metadata={"error": str(exc)},
        )
        recorder.trace.final_status = "failed"
        trace = recorder.trace

    evaluation = evaluate_trace_against_case(trace, case)
    if warnings:
        evaluation.failure_reasons = list(evaluation.failure_reasons) + list(warnings)
    return BenchmarkCaseResult(
        case=case,
        trace=trace,
        evaluation=evaluation,
        ablation_name=ablation.name,
        warnings=warnings,
    )


def _apply_case_filters(
    cases: list[EvaluationCase], config: BenchmarkConfig
) -> list[EvaluationCase]:
    filtered = list(cases)
    if config.categories:
        wanted = set(config.categories)
        filtered = [case for case in filtered if case.category in wanted]
    if config.max_cases is not None:
        filtered = filtered[: max(0, config.max_cases)]
    return filtered


def _build_ablation_comparison(
    ablation_results: list[BenchmarkRunResult],
) -> dict[str, Any]:
    pass_rates: dict[str, float] = {}
    retrieval_rates: dict[str, float] = {}
    human_rates: dict[str, float] = {}
    precision_rates: dict[str, float | None] = {}
    recall_rates: dict[str, float | None] = {}
    case_counts: dict[str, int] = {}
    for run_result in ablation_results:
        metrics = run_result.metrics
        name = run_result.ablation_name
        pass_rates[name] = metrics.pass_rate if metrics else 0.0
        retrieval_rates[name] = _safe_rate(
            metrics.retrieval_trigger_rate if metrics else None
        )
        human_rates[name] = _safe_rate(
            metrics.human_confirmation_rate if metrics else None
        )
        precision_rates[name] = (
            metrics.issue_code_precision if metrics else None
        )
        recall_rates[name] = metrics.issue_code_recall if metrics else None
        case_counts[name] = metrics.case_count if metrics else len(run_result.case_results)
    return {
        "pass_rate_by_ablation": pass_rates,
        "retrieval_trigger_rate_by_ablation": retrieval_rates,
        "human_confirmation_rate_by_ablation": human_rates,
        "issue_precision_by_ablation": precision_rates,
        "issue_recall_by_ablation": recall_rates,
        "case_count_by_ablation": case_counts,
    }


def _write_run_artifacts(result: BenchmarkRunResult, config: BenchmarkConfig) -> None:
    base = Path(config.output_dir) / result.run_id / result.ablation_name  # type: ignore[arg-type]
    base.mkdir(parents=True, exist_ok=True)
    (base / "run_result.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if config.save_markdown:
        (base / "summary.md").write_text(
            format_benchmark_summary(result), encoding="utf-8"
        )
    if config.save_jsonl:
        lines = [
            json.dumps(cr.model_dump(mode="json"), ensure_ascii=False)
            for cr in result.case_results
        ]
        (base / "cases.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
    if config.save_traces:
        traces_dir = base / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        for case_result in result.case_results:
            case_id = case_result.case.case_id
            save_trace_json(case_result.trace, traces_dir / f"{case_id}.json")
            if config.save_jsonl:
                save_trace_jsonl(case_result.trace, traces_dir / f"{case_id}.jsonl")


def _write_ablation_artifacts(
    result: AblationStudyResult, config: BenchmarkConfig
) -> None:
    base = Path(config.output_dir) / result.run_id  # type: ignore[arg-type]
    base.mkdir(parents=True, exist_ok=True)
    (base / "ablation_result.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if config.save_markdown:
        (base / "ablation_summary.md").write_text(
            format_ablation_summary(result), encoding="utf-8"
        )


def _generate_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


def _safe_rate(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"
