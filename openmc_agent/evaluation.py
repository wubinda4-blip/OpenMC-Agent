from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

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


CaseKind = Literal["material", "pin_cell", "repair", "impossible"]
GenerateMaterialFn = Callable[..., StructuredOutputResult[MaterialSpec]]
GenerateSimulationFn = Callable[..., StructuredOutputResult[SimulationSpec]]
RepairSimulationFn = Callable[..., StructuredOutputResult[SimulationSpec]]
GeneratePlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]
RepairPlanFn = Callable[..., StructuredOutputResult[SimulationPlan]]


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    kind: CaseKind
    requirement: str


@dataclass(frozen=True)
class EvaluationResult:
    case: EvaluationCase
    completed: bool
    output_path: str | None = None
    error: str = ""
    retry_count: int = 0


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
