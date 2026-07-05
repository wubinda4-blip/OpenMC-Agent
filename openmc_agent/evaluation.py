from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.executor import build_openmc_material
from openmc_agent.graph import build_graph, build_plan_graph
from openmc_agent.llm import (
    DEFAULT_MODEL,
    StructuredOutputResult,
    generate_structured_output,
    repair_structured_output,
)
from openmc_agent.records import (
    DEFAULT_MATERIAL_RECORDS_PATH,
    DEFAULT_SIMULATION_RECORDS_PATH,
    append_material_record,
)
from openmc_agent.schemas import MaterialSpec, SimulationPlan, SimulationSpec, ValidationReport
from openmc_agent.tools import export_xml, run_geometry_plots, run_smoke_test
from openmc_agent.workflow_trace import WorkflowTrace, trace_from_raw


CaseKind = Literal["material", "pin_cell", "repair", "impossible"]
EvaluationCategory = Literal[
    "material",
    "pin_cell",
    "assembly",
    "core",
    "hex_lattice",
    "triso",
    "runtime_error",
    "export_xml_error",
    "expert_feedback",
    "repair",
    "impossible",
    "unknown",
]
GenerateMaterialFn = Callable[..., StructuredOutputResult[MaterialSpec]]
GenerateSimulationFn = Callable[..., StructuredOutputResult[SimulationSpec]]
RepairSimulationFn = Callable[..., StructuredOutputResult[SimulationSpec]]
GeneratePlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
RepairPlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]


class EvaluationCase(AgentBaseModel):
    """Evaluation case metadata.

    The class accepts the legacy positional shape
    ``EvaluationCase(case_id, kind, requirement)`` used by the smoke runner and
    the newer trace-evaluation fields ``category`` / ``user_request``.
    """

    case_id: str
    category: EvaluationCategory = "unknown"
    user_request: str = ""
    expected_issue_codes: list[str] = Field(default_factory=list)
    expected_renderability: str | None = None
    expected_supported_renderer: str | None = None
    should_require_human_confirmation: bool | None = None
    should_trigger_retrieval: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, *args: Any, **data: Any) -> None:
        if args:
            if len(args) != 3:
                raise TypeError("EvaluationCase positional form is (case_id, kind, requirement)")
            data.setdefault("case_id", args[0])
            data.setdefault("category", args[1])
            data.setdefault("user_request", args[2])
        if "kind" in data and "category" not in data:
            data["category"] = data.pop("kind")
        if "requirement" in data and "user_request" not in data:
            data["user_request"] = data.pop("requirement")
        super().__init__(**data)

    @property
    def kind(self) -> str:
        return self.category

    @property
    def requirement(self) -> str:
        return self.user_request


class EvaluationResult(AgentBaseModel):
    """Result for either a legacy smoke case or a trace-evaluation case."""

    case: EvaluationCase | None = None
    completed: bool | None = None
    output_path: str | None = None
    error: str = ""
    retry_count: int = 0
    case_id: str = ""
    passed: bool = False
    observed_issue_codes: list[str] = Field(default_factory=list)
    observed_renderability: str | None = None
    observed_supported_renderer: str | None = None
    triggered_retrieval: bool = False
    required_human_confirmation: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    failure_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_compatibility_fields(self) -> "EvaluationResult":
        if self.case is not None and not self.case_id:
            self.case_id = self.case.case_id
        if self.completed is None:
            self.completed = self.passed
        else:
            self.passed = bool(self.completed)
        if self.error and not self.failure_reasons and not self.passed:
            self.failure_reasons = [self.error]
        return self


class EvaluationMetrics(AgentBaseModel):
    case_count: int
    pass_count: int
    fail_count: int
    pass_rate: float
    issue_code_precision: float | None = None
    issue_code_recall: float | None = None
    retrieval_trigger_rate: float | None = None
    human_confirmation_rate: float | None = None


@dataclass(frozen=True)
class EvaluationSummary:
    total: int
    completed: int
    success_rate: float
    meets_threshold: bool
    results: list[EvaluationResult] = field(default_factory=list)


DEFAULT_TEST_CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase("material-uo2", "material", "创建 UO2 燃料材料"),
    EvaluationCase("material-water", "material", "创建轻水慢化剂材料"),
    EvaluationCase("material-zircaloy", "material", "创建锆合金包壳材料"),
    EvaluationCase("pin-cell-basic", "pin_cell", "建立一个 UO2 pin-cell 临界计算"),
    EvaluationCase("pin-cell-clad", "pin_cell", "建立带锆包壳的 UO2 pin-cell 临界计算"),
    EvaluationCase("pin-cell-pitch", "pin_cell", "建立 1.26 cm pitch 的 UO2 pin-cell 临界计算"),
    EvaluationCase("pin-cell-low-enriched", "pin_cell", "建立低富集 UO2 pin-cell 临界计算"),
    EvaluationCase("repair-large-radius", "repair", "异常：燃料半径 10cm 的 UO2 pin-cell，修正到合理尺寸"),
    EvaluationCase("repair-bad-clad", "repair", "异常：包壳外半径小于内半径的 UO2 pin-cell，修正尺寸"),
    EvaluationCase(
        "impossible-missing-boundary",
        "impossible",
        "无法完成：建立无限复杂堆芯且缺少材料尺寸边界条件",
    ),
)


def run_test_set(
    *,
    cases: tuple[EvaluationCase, ...] = DEFAULT_TEST_CASES,
    model: str = DEFAULT_MODEL,
    output_dir: str | Path = "data/runs/evaluation",
    material_records_path: str | Path = DEFAULT_MATERIAL_RECORDS_PATH,
    simulation_records_path: str | Path = DEFAULT_SIMULATION_RECORDS_PATH,
    generate_material: GenerateMaterialFn = generate_structured_output,
    generate_simulation: GenerateSimulationFn = generate_structured_output,
    repair_simulation: RepairSimulationFn = repair_structured_output,
    use_plan: bool = False,
    enable_plots: bool = False,
    enable_smoke_test: bool = False,
    generate_plan: GeneratePlanFn = generate_structured_output,
    repair_plan: RepairPlanFn = repair_structured_output,
    export_xml_tool: Callable = export_xml,
    plot_tool: Callable = run_geometry_plots,
    smoke_test_tool: Callable = run_smoke_test,
    success_threshold: float = 0.8,
) -> EvaluationSummary:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[EvaluationResult] = []

    for case in cases:
        if case.kind == "material":
            results.append(
                _run_material_case(
                    case=case,
                    model=model,
                    records_path=material_records_path,
                    generate_material=generate_material,
                )
            )
            continue

        results.append(
            _run_simulation_case(
                case=case,
                model=model,
                output_dir=output_root / case.case_id,
                records_path=simulation_records_path,
                generate_simulation=generate_simulation,
                repair_simulation=repair_simulation,
                use_plan=use_plan,
                enable_plots=enable_plots,
                enable_smoke_test=enable_smoke_test,
                generate_plan=generate_plan,
                repair_plan=repair_plan,
                export_xml_tool=export_xml_tool,
                plot_tool=plot_tool,
                smoke_test_tool=smoke_test_tool,
            )
        )

    completed = sum(result.completed for result in results)
    total = len(results)
    success_rate = completed / total if total else 0.0
    return EvaluationSummary(
        total=total,
        completed=completed,
        success_rate=success_rate,
        meets_threshold=success_rate >= success_threshold,
        results=results,
    )


def format_summary(summary: EvaluationSummary) -> str:
    lines = [
        (
            f"completed={summary.completed}/{summary.total} "
            f"success_rate={summary.success_rate:.1%} "
            f"threshold_met={summary.meets_threshold}"
        )
    ]
    for result in summary.results:
        status = "PASS" if result.completed else "FAIL"
        detail = result.output_path or result.error
        lines.append(f"{status} {result.case.case_id}: {detail}")
    return "\n".join(lines)


def evaluate_trace_against_case(
    trace: WorkflowTrace | dict[str, Any],
    case: EvaluationCase,
) -> EvaluationResult:
    """Evaluate one trace against a lightweight expected-case contract."""
    workflow_trace = trace_from_raw(trace)
    observed_issue_codes = _trace_issue_codes(workflow_trace)
    observed_renderability = workflow_trace.final_renderability or _latest_event_field(
        workflow_trace, "renderability"
    )
    observed_supported_renderer = (
        workflow_trace.final_supported_renderer
        or _latest_event_field(workflow_trace, "supported_renderer")
    )
    triggered_retrieval = any(
        event.event_type in {"retrieval_started", "retrieval_completed"}
        for event in workflow_trace.events
    )
    required_human_confirmation = _trace_requires_human_confirmation(workflow_trace)

    failure_reasons: list[str] = []
    expected_codes = set(case.expected_issue_codes)
    observed_codes = set(observed_issue_codes)
    if expected_codes and not expected_codes.issubset(observed_codes):
        missing = sorted(expected_codes - observed_codes)
        failure_reasons.append(f"missing expected issue codes: {', '.join(missing)}")
    if (
        case.expected_renderability is not None
        and observed_renderability != case.expected_renderability
    ):
        failure_reasons.append(
            "renderability mismatch: "
            f"expected={case.expected_renderability} observed={observed_renderability}"
        )
    if (
        case.expected_supported_renderer is not None
        and observed_supported_renderer != case.expected_supported_renderer
    ):
        failure_reasons.append(
            "supported_renderer mismatch: "
            f"expected={case.expected_supported_renderer} observed={observed_supported_renderer}"
        )
    if (
        case.should_trigger_retrieval is not None
        and triggered_retrieval != case.should_trigger_retrieval
    ):
        failure_reasons.append(
            "retrieval trigger mismatch: "
            f"expected={case.should_trigger_retrieval} observed={triggered_retrieval}"
        )
    if (
        case.should_require_human_confirmation is not None
        and required_human_confirmation != case.should_require_human_confirmation
    ):
        failure_reasons.append(
            "human confirmation mismatch: "
            f"expected={case.should_require_human_confirmation} "
            f"observed={required_human_confirmation}"
        )

    precision = _precision(observed_codes, expected_codes)
    recall = _recall(observed_codes, expected_codes)
    return EvaluationResult(
        case=case,
        case_id=case.case_id,
        passed=not failure_reasons,
        observed_issue_codes=observed_issue_codes,
        observed_renderability=observed_renderability,
        observed_supported_renderer=observed_supported_renderer,
        triggered_retrieval=triggered_retrieval,
        required_human_confirmation=required_human_confirmation,
        metrics={
            "issue_code_precision": precision,
            "issue_code_recall": recall,
            "event_count": len(workflow_trace.events),
        },
        failure_reasons=failure_reasons,
    )


def aggregate_evaluation_results(results: list[EvaluationResult]) -> EvaluationMetrics:
    """Aggregate trace-evaluation results into small benchmark metrics."""
    case_count = len(results)
    pass_count = sum(result.passed for result in results)
    fail_count = case_count - pass_count
    precisions = [
        value
        for result in results
        if (value := result.metrics.get("issue_code_precision")) is not None
    ]
    recalls = [
        value
        for result in results
        if (value := result.metrics.get("issue_code_recall")) is not None
    ]
    return EvaluationMetrics(
        case_count=case_count,
        pass_count=pass_count,
        fail_count=fail_count,
        pass_rate=pass_count / case_count if case_count else 0.0,
        issue_code_precision=sum(precisions) / len(precisions) if precisions else None,
        issue_code_recall=sum(recalls) / len(recalls) if recalls else None,
        retrieval_trigger_rate=(
            sum(result.triggered_retrieval for result in results) / case_count
            if case_count
            else None
        ),
        human_confirmation_rate=(
            sum(result.required_human_confirmation for result in results) / case_count
            if case_count
            else None
        ),
    )


def _trace_issue_codes(trace: WorkflowTrace) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for event in trace.events:
        for code in event.issue_codes:
            if code not in seen:
                seen.add(code)
                codes.append(code)
        metadata_codes = event.metadata.get("issue_codes")
        if isinstance(metadata_codes, list):
            for code in metadata_codes:
                if isinstance(code, str) and code not in seen:
                    seen.add(code)
                    codes.append(code)
    return codes


def _latest_event_field(trace: WorkflowTrace, field_name: str) -> str | None:
    for event in reversed(trace.events):
        value = getattr(event, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _trace_requires_human_confirmation(trace: WorkflowTrace) -> bool:
    for event in trace.events:
        if event.metadata.get("requires_human_confirmation_count", 0):
            return True
        if event.metadata.get("required_human_confirmations"):
            return True
        if event.event_type.startswith("ask_expert"):
            return True
    return False


def _precision(observed: set[str], expected: set[str]) -> float | None:
    if not observed and not expected:
        return None
    if not observed:
        return 0.0
    return len(observed & expected) / len(observed)


def _recall(observed: set[str], expected: set[str]) -> float | None:
    if not expected:
        return None
    return len(observed & expected) / len(expected)


def main() -> int:
    summary = run_test_set()
    print(format_summary(summary))
    return 0 if summary.meets_threshold else 1


def _run_material_case(
    *,
    case: EvaluationCase,
    model: str,
    records_path: str | Path,
    generate_material: GenerateMaterialFn,
) -> EvaluationResult:
    result = generate_material(
        requirement=case.requirement,
        schema=MaterialSpec,
        model=model,
    )
    if not result.ok or result.value is None:
        return EvaluationResult(case=case, completed=False, error=result.error)

    try:
        material = build_openmc_material(result.value)
    except Exception as exc:
        return EvaluationResult(case=case, completed=False, error=str(exc))

    append_material_record(
        requirement=case.requirement,
        model=model,
        material_spec=result.value,
        validation_report=ValidationReport(is_valid=True),
        path=records_path,
    )
    return EvaluationResult(case=case, completed=True, output_path=material.name)


def _run_simulation_case(
    *,
    case: EvaluationCase,
    model: str,
    output_dir: Path,
    records_path: str | Path,
    generate_simulation: GenerateSimulationFn,
    repair_simulation: RepairSimulationFn,
    use_plan: bool,
    enable_plots: bool,
    enable_smoke_test: bool,
    generate_plan: GeneratePlanFn,
    repair_plan: RepairPlanFn,
    export_xml_tool: Callable,
    plot_tool: Callable,
    smoke_test_tool: Callable,
) -> EvaluationResult:
    if use_plan:
        graph = build_plan_graph(
            generate_plan=generate_plan,
            repair_plan=repair_plan,
            export_xml_tool=export_xml_tool,
            plot_tool=plot_tool,
            smoke_test_tool=smoke_test_tool,
            enable_plots=enable_plots,
            enable_smoke_test=enable_smoke_test,
            max_retries=3,
        )
    else:
        graph = build_graph(
            generate_spec=generate_simulation,
            repair_spec=repair_simulation,
            max_retries=3,
        )
    state = graph.invoke(
        {
            "requirement": case.requirement,
            "model": model,
            "output_dir": str(output_dir),
            "records_path": str(records_path),
        }
    )
    report = state.get("validation_report")
    if use_plan:
        plan = state.get("simulation_plan")
        completed = bool(
            report
            and report.is_valid
            and plan is not None
            and (state.get("model_path") or not plan.capability_report.is_executable)
        )
    else:
        completed = bool(report and report.is_valid and state.get("model_path"))
    return EvaluationResult(
        case=case,
        completed=completed,
        output_path=state.get("model_path"),
        error=state.get("error", ""),
        retry_count=state.get("retry_count", 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
