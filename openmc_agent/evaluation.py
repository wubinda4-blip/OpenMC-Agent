from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel
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
    forbidden_issue_codes: list[str] = Field(default_factory=list)
    expected_renderability: str | None = None
    expected_supported_renderer: str | None = None
    should_require_human_confirmation: bool | None = None
    should_trigger_retrieval: bool | None = None
    expected_planning_mode: str | None = None
    expected_incremental_patch_types: list[str] = Field(default_factory=list)
    expected_artifact_keys: list[str] = Field(default_factory=list)
    expected_failed_stage: str | None = None
    expected_failed_patch_type: str | None = None
    expected_plan_schema_success: bool | None = None
    expected_incremental_patch_success: bool | None = None
    expected_artifact_complete: bool | None = None
    expected_audit_finding_codes: list[str] = Field(default_factory=list)
    forbidden_audit_finding_codes: list[str] = Field(default_factory=list)
    expected_audit_min_finding_count: int | None = None
    expected_audit_max_finding_count: int | None = None
    expected_audit_requires_human_confirmation: bool | None = None
    expected_semantic_audit_fallback_used: bool | None = None
    expected_repair_status: str | None = None
    expected_repair_source_issue_codes: list[str] = Field(default_factory=list)
    expected_repair_resolved_issue_codes: list[str] = Field(default_factory=list)
    expected_repair_allowed_paths: list[str] = Field(default_factory=list)
    forbidden_repair_paths: list[str] = Field(default_factory=list)
    expected_repair_applied_to_clone: bool | None = None
    expected_repair_applied_to_workflow_plan: bool | None = None
    expected_repair_requires_human_confirmation: bool | None = None
    expected_repair_fallback_used: bool | None = None
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
    plan_schema_success_rate: float | None = None
    incremental_patch_success_rate: float | None = None
    artifact_completeness_rate: float | None = None
    planning_mode_accuracy: float | None = None
    semantic_audit_completion_rate: float | None = None
    semantic_audit_fallback_rate: float | None = None
    semantic_audit_finding_precision: float | None = None
    semantic_audit_finding_recall: float | None = None
    semantic_audit_false_positive_rate: float | None = None
    semantic_audit_known_error_detection_rate: float | None = None
    llm_repair_completion_rate: float | None = None
    llm_repair_acceptance_rate: float | None = None
    llm_repair_rejection_rate: float | None = None
    llm_repair_unsafe_rate: float | None = None
    llm_repair_fallback_rate: float | None = None
    llm_repair_issue_resolution_rate: float | None = None
    llm_repair_new_issue_rate: float | None = None


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
    export_xml_tool: Callable | None = None,
    plot_tool: Callable | None = None,
    smoke_test_tool: Callable | None = None,
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
                export_xml_tool=export_xml_tool or export_xml,
                plot_tool=plot_tool or run_geometry_plots,
                smoke_test_tool=smoke_test_tool or run_smoke_test,
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
    triggered_retrieval = _trace_retrieval_triggered(workflow_trace)
    required_human_confirmation = _trace_requires_human_confirmation(workflow_trace)

    planning_mode = _trace_planning_mode(workflow_trace)
    failed_stage = _trace_failed_stage(workflow_trace)
    failed_patch_type = _trace_failed_patch_type(workflow_trace)
    plan_schema_success = _trace_plan_schema_success(workflow_trace)
    incremental_patch_success = _trace_incremental_patch_success(workflow_trace)
    observed_patch_types = _trace_incremental_patch_types(workflow_trace)
    artifact_keys = _trace_artifact_keys(workflow_trace)
    artifact_complete = _artifact_complete(case.expected_artifact_keys, artifact_keys)
    audit = _trace_semantic_audit(workflow_trace)
    repair = _trace_llm_repair(workflow_trace)

    failure_reasons: list[str] = []
    expected_codes = set(case.expected_issue_codes)
    observed_codes = set(observed_issue_codes)
    forbidden_codes = set(case.forbidden_issue_codes)

    if expected_codes and not expected_codes.issubset(observed_codes):
        missing = sorted(expected_codes - observed_codes)
        failure_reasons.append(f"missing expected issue codes: {', '.join(missing)}")
    forbidden_observed = sorted(forbidden_codes & observed_codes)
    if forbidden_observed:
        failure_reasons.append(
            f"forbidden issue codes observed: {', '.join(forbidden_observed)}"
        )
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
    planning_mode_match = None
    if case.expected_planning_mode is not None:
        planning_mode_match = planning_mode == case.expected_planning_mode
        if not planning_mode_match:
            failure_reasons.append(
                "planning mode mismatch: "
                f"expected={case.expected_planning_mode} observed={planning_mode}"
            )
    if case.expected_incremental_patch_types:
        missing_patches = sorted(set(case.expected_incremental_patch_types) - set(observed_patch_types))
        if missing_patches:
            failure_reasons.append(
                "missing expected incremental patch types: "
                f"{', '.join(missing_patches)}"
            )
    missing_artifacts: list[str] = []
    if case.expected_artifact_keys:
        missing_artifacts = sorted(set(case.expected_artifact_keys) - set(artifact_keys))
        if missing_artifacts:
            failure_reasons.append(
                f"missing expected artifacts: {', '.join(missing_artifacts)}"
            )
    if (
        case.expected_plan_schema_success is not None
        and plan_schema_success is not None
        and plan_schema_success != case.expected_plan_schema_success
    ):
        failure_reasons.append(
            "plan schema success mismatch: "
            f"expected={case.expected_plan_schema_success} observed={plan_schema_success}"
        )
    if case.expected_plan_schema_success is not None and plan_schema_success is None:
        failure_reasons.append("plan schema success unavailable")
    if (
        case.expected_incremental_patch_success is not None
        and incremental_patch_success is not None
        and incremental_patch_success != case.expected_incremental_patch_success
    ):
        failure_reasons.append(
            "incremental patch success mismatch: "
            f"expected={case.expected_incremental_patch_success} observed={incremental_patch_success}"
        )
    if case.expected_incremental_patch_success is not None and incremental_patch_success is None:
        failure_reasons.append("incremental patch success unavailable")
    if (
        case.expected_artifact_complete is not None
        and artifact_complete is not None
        and artifact_complete != case.expected_artifact_complete
    ):
        failure_reasons.append(
            "artifact completeness mismatch: "
            f"expected={case.expected_artifact_complete} observed={artifact_complete}"
        )
    if (
        case.expected_failed_stage is not None
        and failed_stage != case.expected_failed_stage
    ):
        failure_reasons.append(
            "failed stage mismatch: "
            f"expected={case.expected_failed_stage} observed={failed_stage}"
        )
    if (
        case.expected_failed_patch_type is not None
        and failed_patch_type != case.expected_failed_patch_type
    ):
        failure_reasons.append(
            "failed patch type mismatch: "
            f"expected={case.expected_failed_patch_type} observed={failed_patch_type}"
        )
    if case.expected_audit_finding_codes or case.forbidden_audit_finding_codes or case.expected_audit_min_finding_count is not None or case.expected_audit_max_finding_count is not None:
        if audit["mode"] == "strict_evaluation":
            missing_audit = sorted(set(case.expected_audit_finding_codes) - set(audit["finding_codes"]))
            forbidden_audit = sorted(set(case.forbidden_audit_finding_codes) & set(audit["finding_codes"]))
            if missing_audit:
                failure_reasons.append(f"missing expected audit finding codes: {', '.join(missing_audit)}")
            if forbidden_audit:
                failure_reasons.append(f"forbidden audit finding codes observed: {', '.join(forbidden_audit)}")
            if case.expected_audit_min_finding_count is not None and audit["finding_count"] < case.expected_audit_min_finding_count:
                failure_reasons.append("semantic audit finding count below minimum")
            if case.expected_audit_max_finding_count is not None and audit["finding_count"] > case.expected_audit_max_finding_count:
                failure_reasons.append("semantic audit finding count above maximum")
    if case.expected_audit_requires_human_confirmation is not None and audit["requires_human_confirmation"] != case.expected_audit_requires_human_confirmation and audit["mode"] == "strict_evaluation":
        failure_reasons.append("semantic audit human confirmation mismatch")
    if case.expected_semantic_audit_fallback_used is not None and audit["fallback_used"] != case.expected_semantic_audit_fallback_used and audit["mode"] == "strict_evaluation":
        failure_reasons.append("semantic audit fallback mismatch")

    if case.expected_repair_status is not None and repair["status"] != case.expected_repair_status:
        failure_reasons.append(
            f"repair status mismatch: expected={case.expected_repair_status} observed={repair['status']}"
        )
    if case.expected_repair_source_issue_codes:
        missing = sorted(set(case.expected_repair_source_issue_codes) - set(repair["source_issue_codes"]))
        if missing:
            failure_reasons.append(f"missing expected repair source issues: {', '.join(missing)}")
    if case.expected_repair_resolved_issue_codes:
        missing = sorted(set(case.expected_repair_resolved_issue_codes) - set(repair["resolved_issue_codes"]))
        if missing:
            failure_reasons.append(f"missing expected repair resolved issues: {', '.join(missing)}")
    if case.expected_repair_allowed_paths:
        missing = sorted(set(case.expected_repair_allowed_paths) - set(repair["allowed_paths"]))
        if missing:
            failure_reasons.append(f"missing expected repair allowed paths: {', '.join(missing)}")
    forbidden_paths = sorted(set(case.forbidden_repair_paths) & set(repair["operation_paths"]))
    if forbidden_paths:
        failure_reasons.append(f"forbidden repair paths observed: {', '.join(forbidden_paths)}")
    if case.expected_repair_applied_to_clone is not None and repair["applied_to_clone"] != case.expected_repair_applied_to_clone:
        failure_reasons.append("repair applied_to_clone mismatch")
    if case.expected_repair_applied_to_workflow_plan is not None and repair["applied_to_workflow_plan"] != case.expected_repair_applied_to_workflow_plan:
        failure_reasons.append("repair applied_to_workflow_plan mismatch")
    if case.expected_repair_requires_human_confirmation is not None and repair["requires_human_confirmation"] != case.expected_repair_requires_human_confirmation:
        failure_reasons.append("repair requires_human_confirmation mismatch")
    if case.expected_repair_fallback_used is not None and repair["fallback_used"] != case.expected_repair_fallback_used:
        failure_reasons.append("repair fallback mismatch")

    actual_failed = workflow_trace.final_status == "failed" or any(
        event.event_type == "workflow_failed" for event in workflow_trace.events
    )
    if actual_failed and not failed_stage:
        failure_reasons.append("missing failed_stage for failed case")

    precision = _precision(observed_codes, expected_codes)
    recall = _recall(observed_codes, expected_codes)
    audit_expected = set(case.expected_audit_finding_codes)
    audit_forbidden = set(case.forbidden_audit_finding_codes)
    audit_observed = set(audit["finding_codes"])
    audit_tp = len(audit_expected & audit_observed)
    audit_fp = len(audit_forbidden & audit_observed)
    audit_fn = len(audit_expected - audit_observed)
    audit_precision = audit_tp / (audit_tp + audit_fp) if (audit_expected or audit_forbidden) and (audit_tp + audit_fp) else (1.0 if audit_expected or audit_forbidden else None)
    audit_recall = audit_tp / len(audit_expected) if audit_expected else None
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
            "plan_schema_success": plan_schema_success,
            "incremental_patch_success": incremental_patch_success,
            "retrieval_triggered": triggered_retrieval,
            "artifact_complete": artifact_complete,
            "planning_mode": planning_mode,
            "planning_mode_match": planning_mode_match,
            "failed_stage": failed_stage,
            "failed_patch_type": failed_patch_type,
            "forbidden_issue_code_count": len(forbidden_observed),
            "expected_artifact_count": len(case.expected_artifact_keys),
            "observed_artifact_count": len(artifact_keys),
            "observed_incremental_patch_types": observed_patch_types,
            "observed_artifact_keys": artifact_keys,
            "missing_artifact_keys": missing_artifacts,
            "semantic_audit_enabled": audit["enabled"],
            "semantic_audit_completed": audit["completed"],
            "semantic_audit_fallback_used": audit["fallback_used"],
            "semantic_audit_finding_count": audit["finding_count"],
            "semantic_audit_finding_codes": audit["finding_codes"],
            "semantic_audit_true_positive_count": audit_tp,
            "semantic_audit_false_positive_count": audit_fp,
            "semantic_audit_false_negative_count": audit_fn,
            "semantic_audit_precision": audit_precision,
            "semantic_audit_recall": audit_recall,
            "semantic_audit_false_positive_rate": (audit_fp / len(audit_forbidden) if audit_forbidden else None),
            "semantic_audit_known_error_detected": (audit_tp > 0 if audit_expected else None),
            "llm_repair_enabled": repair["enabled"],
            "llm_repair_completed": repair["completed"],
            "llm_repair_status": repair["status"],
            "llm_repair_source_issue_codes": repair["source_issue_codes"],
            "llm_repair_proposal_count": 1 if repair["completed"] else 0,
            "llm_repair_operation_count": repair["operation_count"],
            "llm_repair_allowed_operation_count": repair["allowed_operation_count"],
            "llm_repair_rejected_operation_count": repair["rejected_operation_count"],
            "llm_repair_unsafe_operation_count": repair["unsafe_operation_count"],
            "llm_repair_accepted_count": 1 if repair["status"] == "accepted" else 0,
            "llm_repair_rejected_count": 1 if repair["status"] == "rejected" else 0,
            "llm_repair_unsafe_count": 1 if repair["status"] == "unsafe" else 0,
            "llm_repair_fallback_used": repair["fallback_used"],
            "llm_repair_resolved_issue_count": len(repair["resolved_issue_codes"]),
            "llm_repair_new_issue_count": len(repair["new_issue_codes"]),
            "llm_repair_applied_to_clone": repair["applied_to_clone"],
            "llm_repair_applied_to_workflow_plan": repair["applied_to_workflow_plan"],
        },
        failure_reasons=failure_reasons,
    )

def aggregate_evaluation_results(results: list[EvaluationResult]) -> EvaluationMetrics:
    """Aggregate trace-evaluation results into small benchmark metrics."""
    case_count = len(results)
    pass_count = sum(result.passed for result in results)
    fail_count = case_count - pass_count
    precisions = _metric_values(results, "issue_code_precision")
    recalls = _metric_values(results, "issue_code_recall")
    plan_schema_values = _bool_metric_values(results, "plan_schema_success")
    incremental_values = _bool_metric_values(results, "incremental_patch_success")
    artifact_values = _bool_metric_values(results, "artifact_complete")
    planning_mode_values = _bool_metric_values(results, "planning_mode_match")
    audit_completed_values = _bool_metric_values(results, "semantic_audit_completed")
    audit_fallback_values = _bool_metric_values(results, "semantic_audit_fallback_used")
    audit_precisions = _metric_values(results, "semantic_audit_precision")
    audit_recalls = _metric_values(results, "semantic_audit_recall")
    audit_fprs = _metric_values(results, "semantic_audit_false_positive_rate")
    audit_detected = _bool_metric_values(results, "semantic_audit_known_error_detected")
    repair_enabled = [r for r in results if r.metrics.get("llm_repair_enabled") is True]
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
        plan_schema_success_rate=(
            sum(plan_schema_values) / len(plan_schema_values)
            if plan_schema_values
            else None
        ),
        incremental_patch_success_rate=(
            sum(incremental_values) / len(incremental_values)
            if incremental_values
            else None
        ),
        artifact_completeness_rate=(
            sum(artifact_values) / len(artifact_values)
            if artifact_values
            else None
        ),
        planning_mode_accuracy=(
            sum(planning_mode_values) / len(planning_mode_values)
            if planning_mode_values
            else None
        ),
        semantic_audit_completion_rate=(sum(audit_completed_values) / len(audit_completed_values) if audit_completed_values else None),
        semantic_audit_fallback_rate=(sum(audit_fallback_values) / len(audit_fallback_values) if audit_fallback_values else None),
        semantic_audit_finding_precision=(sum(audit_precisions) / len(audit_precisions) if audit_precisions else None),
        semantic_audit_finding_recall=(sum(audit_recalls) / len(audit_recalls) if audit_recalls else None),
        semantic_audit_false_positive_rate=(sum(audit_fprs) / len(audit_fprs) if audit_fprs else None),
        semantic_audit_known_error_detection_rate=(sum(audit_detected) / len(audit_detected) if audit_detected else None),
        llm_repair_completion_rate=_repair_rate(repair_enabled, "llm_repair_completed"),
        llm_repair_acceptance_rate=_repair_status_rate(repair_enabled, "accepted"),
        llm_repair_rejection_rate=_repair_status_rate(repair_enabled, "rejected"),
        llm_repair_unsafe_rate=_repair_status_rate(repair_enabled, "unsafe"),
        llm_repair_fallback_rate=_repair_rate(repair_enabled, "llm_repair_fallback_used"),
        llm_repair_issue_resolution_rate=_repair_positive_rate(repair_enabled, "llm_repair_resolved_issue_count"),
        llm_repair_new_issue_rate=_repair_positive_rate(repair_enabled, "llm_repair_new_issue_count"),
    )


def _metric_values(results: list[EvaluationResult], key: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result.metrics.get(key)
        if value is not None:
            values.append(float(value))
    return values


def _bool_metric_values(results: list[EvaluationResult], key: str) -> list[bool]:
    values: list[bool] = []
    for result in results:
        value = result.metrics.get(key)
        if value is not None:
            values.append(bool(value))
    return values



def _trace_metadata_values(trace: WorkflowTrace, key: str) -> list[Any]:
    values: list[Any] = []
    for event in trace.events:
        if key in event.metadata:
            values.append(event.metadata[key])
        retrieval = event.metadata.get("retrieval")
        if isinstance(retrieval, dict) and key in retrieval:
            values.append(retrieval[key])
    return values


def _latest_metadata_value(trace: WorkflowTrace, key: str) -> Any:
    values = _trace_metadata_values(trace, key)
    return values[-1] if values else None


def _trace_retrieval_triggered(trace: WorkflowTrace) -> bool:
    if any(event.event_type in {"retrieval_started", "retrieval_completed"} for event in trace.events):
        return True
    if _latest_metadata_value(trace, "retrieval_triggered") is not None:
        return bool(_latest_metadata_value(trace, "retrieval_triggered"))
    for event in trace.events:
        retrieval = event.metadata.get("retrieval")
        if isinstance(retrieval, dict):
            if any(int(retrieval.get(key, 0) or 0) > 0 for key in (
                "grep_request_count",
                "grep_evidence_count",
                "graph_node_count",
                "graphrag_evidence_count",
                "rag_evidence_count",
                "ranked_evidence_count",
            )):
                return True
    return False


def _trace_planning_mode(trace: WorkflowTrace) -> str | None:
    for key in ("planning_mode", "mode"):
        value = _latest_metadata_value(trace, key)
        if isinstance(value, str) and value:
            return value
    decision = _latest_metadata_value(trace, "planning_mode_decision")
    if isinstance(decision, dict):
        mode = decision.get("mode")
        if isinstance(mode, str):
            return mode
    return None


def _trace_failed_stage(trace: WorkflowTrace) -> str | None:
    value = _latest_metadata_value(trace, "failed_stage")
    if isinstance(value, str) and value:
        return value
    for event in reversed(trace.events):
        if event.event_type == "workflow_failed":
            stage = event.metadata.get("stage") or event.metadata.get("node")
            if isinstance(stage, str) and stage:
                return stage
    return None


def _trace_failed_patch_type(trace: WorkflowTrace) -> str | None:
    value = _latest_metadata_value(trace, "failed_patch_type")
    if isinstance(value, str) and value:
        return value
    inc_result = _latest_metadata_value(trace, "incremental_execution_result")
    if isinstance(inc_result, dict):
        summary = inc_result.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("failed_patch_type"), str):
            return summary["failed_patch_type"]
        if isinstance(inc_result.get("failed_patch_type"), str):
            return inc_result["failed_patch_type"]
    return None


def _trace_plan_schema_success(trace: WorkflowTrace) -> bool | None:
    for key in ("plan_schema_success", "simulation_plan_present"):
        value = _latest_metadata_value(trace, key)
        if isinstance(value, bool):
            return value
    validation = _latest_metadata_value(trace, "validation_report")
    if isinstance(validation, dict) and isinstance(validation.get("is_valid"), bool):
        return validation["is_valid"]
    value = _latest_metadata_value(trace, "is_valid")
    if isinstance(value, bool):
        return value
    return None


def _trace_incremental_patch_success(trace: WorkflowTrace) -> bool | None:
    value = _latest_metadata_value(trace, "incremental_patch_success")
    if isinstance(value, bool):
        return value
    inc_result = _latest_metadata_value(trace, "incremental_execution_result")
    if isinstance(inc_result, dict) and isinstance(inc_result.get("ok"), bool):
        return inc_result["ok"]
    patch_status = _latest_metadata_value(trace, "patch_status")
    if isinstance(patch_status, dict):
        statuses = [str(v) for v in patch_status.values()]
        if statuses:
            return all(status in {"valid", "ok", "success", "completed"} for status in statuses)
    return None


def _trace_incremental_patch_types(trace: WorkflowTrace) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ordered.append(value)

    for key in ("valid_patch_types", "expected_incremental_patch_types", "patch_order"):
        value = _latest_metadata_value(trace, key)
        if isinstance(value, list):
            for item in value:
                add(item)
    patch_status = _latest_metadata_value(trace, "patch_status")
    if isinstance(patch_status, dict):
        for patch_type, status in patch_status.items():
            if str(status) in {"valid", "ok", "success", "completed"}:
                add(patch_type)
    plan_build = _latest_metadata_value(trace, "plan_build_state_summary") or _latest_metadata_value(trace, "plan_build_state")
    if isinstance(plan_build, dict):
        for key in ("valid_patch_types", "patch_order"):
            value = plan_build.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
        patches = plan_build.get("patches")
        if isinstance(patches, dict):
            for patch_type, patch_info in patches.items():
                status = patch_info.get("status") if isinstance(patch_info, dict) else None
                if status in {"valid", "ok", "success", "completed"}:
                    add(patch_type)
    return ordered


def _trace_artifact_keys(trace: WorkflowTrace) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ordered.append(value)

    for key in ("artifact_keys", "expected_artifact_keys"):
        value = _latest_metadata_value(trace, key)
        if isinstance(value, list):
            for item in value:
                add(item)
    plan_artifacts = _latest_metadata_value(trace, "plan_artifacts")
    if isinstance(plan_artifacts, dict):
        for key in plan_artifacts:
            add(key)
    elif isinstance(plan_artifacts, list):
        for item in plan_artifacts:
            if isinstance(item, str):
                name = Path(item).name
                stem = Path(item).stem
                add(stem or name)
            elif isinstance(item, dict):
                add(item.get("key") or item.get("name") or item.get("type"))
    for event in trace.events:
        if event.event_type in {"workflow_completed", "workflow_failed"}:
            add("workflow_trace")
        if event.renderability is not None or event.supported_renderer is not None:
            add("capability_report")
    return ordered


def _artifact_complete(expected_keys: list[str], observed_keys: list[str]) -> bool | None:
    if not expected_keys:
        return None
    return set(expected_keys).issubset(set(observed_keys))

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
        from openmc_agent.executor import build_openmc_material

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
        from openmc_agent.graph import build_plan_graph

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
        from openmc_agent.graph import build_graph

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


def _trace_semantic_audit(trace: WorkflowTrace) -> dict[str, Any]:
    completed = [event for event in trace.events if event.event_type == "semantic_audit_completed"]
    latest = completed[-1].metadata if completed else {}
    findings = latest.get("findings") or []
    codes = latest.get("finding_codes") or []
    if not codes and isinstance(findings, list):
        codes = [f.get("finding_code") for f in findings if isinstance(f, dict) and f.get("finding_code")]
    requires_human = False
    if isinstance(findings, list):
        requires_human = any(bool(f.get("requires_human_confirmation")) for f in findings if isinstance(f, dict))
    mode = latest.get("mode") or _latest_metadata_value(trace, "semantic_audit_mode") or "warning_only"
    return {
        "enabled": bool(completed),
        "completed": bool(completed),
        "fallback_used": bool(latest.get("fallback_used")),
        "finding_count": int(latest.get("finding_count") or len(codes or [])),
        "finding_codes": [str(c) for c in (codes or []) if c],
        "requires_human_confirmation": requires_human,
        "mode": mode,
    }


def _trace_llm_repair(trace: WorkflowTrace) -> dict[str, Any]:
    events = [event for event in trace.events if event.event_type in {
        "llm_repair_proposal_generated",
        "llm_repair_proposal_accepted",
        "llm_repair_proposal_rejected",
        "llm_repair_proposal_unsafe",
        "llm_repair_proposal_failed",
    }]
    latest = events[-1].metadata if events else {}
    evaluations = latest.get("operation_evaluations") or []
    paths = [ev.get("path") for ev in evaluations if isinstance(ev, dict) and ev.get("path")]
    allowed_paths = [ev.get("path") for ev in evaluations if isinstance(ev, dict) and ev.get("allowed") and ev.get("path")]
    return {
        "enabled": bool(events),
        "completed": bool(events),
        "status": latest.get("status"),
        "source_issue_codes": list(latest.get("source_issue_codes") or []),
        "source_audit_finding_codes": list(latest.get("source_audit_finding_codes") or []),
        "operation_count": int(latest.get("operation_count") or 0),
        "allowed_operation_count": int(latest.get("allowed_operation_count") or 0),
        "rejected_operation_count": int(latest.get("rejected_operation_count") or 0),
        "unsafe_operation_count": int(latest.get("unsafe_operation_count") or 0),
        "fallback_used": bool(latest.get("fallback_used")),
        "resolved_issue_codes": list(latest.get("resolved_issue_codes") or []),
        "remaining_issue_codes": list(latest.get("remaining_issue_codes") or []),
        "new_issue_codes": list(latest.get("new_issue_codes") or []),
        "applied_to_clone": bool(latest.get("applied_to_clone")),
        "applied_to_workflow_plan": bool(latest.get("applied_to_workflow_plan")),
        "requires_human_confirmation": bool(latest.get("requires_human_confirmation")),
        "operation_paths": paths,
        "allowed_paths": allowed_paths,
    }


def _repair_rate(results: list[EvaluationResult], key: str) -> float | None:
    if not results:
        return None
    values = [r.metrics.get(key) for r in results if r.metrics.get(key) is not None]
    return sum(bool(v) for v in values) / len(values) if values else None


def _repair_status_rate(results: list[EvaluationResult], status: str) -> float | None:
    if not results:
        return None
    values = [r.metrics.get("llm_repair_status") for r in results if r.metrics.get("llm_repair_status") is not None]
    return sum(v == status for v in values) / len(values) if values else None


def _repair_positive_rate(results: list[EvaluationResult], key: str) -> float | None:
    if not results:
        return None
    values = [r.metrics.get(key) for r in results if r.metrics.get(key) is not None]
    return sum(int(v) > 0 for v in values) / len(values) if values else None
