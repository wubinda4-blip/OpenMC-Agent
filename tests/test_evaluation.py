import pytest
from pathlib import Path

from openmc_agent.evaluation import DEFAULT_TEST_CASES, format_summary, run_test_set
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.records import load_jsonl_records
from openmc_agent.schemas import (
    ExecutionCheckSpec,
    GeometrySpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
)
from openmc_agent.tools import ToolResult


def material(name: str) -> MaterialSpec:
    return MaterialSpec(
        name=name,
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )


def simulation() -> SimulationSpec:
    return SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=MaterialSpec(
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=4.95),
                    NuclideSpec(name="U238", percent=95.05),
                    NuclideSpec(name="O16", percent=200.0),
                ],
            ),
            moderator=material("Water moderator"),
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def simulation_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=simulation(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(200, 200),
                filename="pin_cell_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100)
        ),
    )


def invalid_simulation() -> SimulationSpec:
    spec = simulation()
    spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )
    return spec


def test_default_test_cases_cover_step_11_categories() -> None:
    assert len(DEFAULT_TEST_CASES) == 10
    assert sum(case.kind == "material" for case in DEFAULT_TEST_CASES) == 3
    assert sum(case.kind == "pin_cell" for case in DEFAULT_TEST_CASES) == 4
    assert sum(case.kind == "repair" for case in DEFAULT_TEST_CASES) == 2
    assert sum(case.kind == "impossible" for case in DEFAULT_TEST_CASES) == 1


@pytest.mark.openmc
def test_run_test_set_records_success_rate(tmp_path: Path) -> None:
    pytest.importorskip("openmc", reason="OpenMC is required for this integration test")
    def fake_generate_material(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=material(requirement))

    def fake_generate_simulation(*, requirement: str, schema, model: str):
        if "异常" in requirement:
            return StructuredOutputResult(ok=True, value=invalid_simulation())
        if "无法完成" in requirement:
            return StructuredOutputResult(ok=False, error="unsupported boundary case")
        return StructuredOutputResult(ok=True, value=simulation())

    def fake_repair_simulation(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=simulation())

    summary = run_test_set(
        output_dir=tmp_path / "outputs",
        material_records_path=tmp_path / "materials.jsonl",
        simulation_records_path=tmp_path / "simulations.jsonl",
        generate_material=fake_generate_material,
        generate_simulation=fake_generate_simulation,
        repair_simulation=fake_repair_simulation,
    )

    assert summary.total == 10
    assert summary.completed == 9
    assert summary.success_rate == 0.9
    assert summary.meets_threshold is True
    assert len(load_jsonl_records(tmp_path / "materials.jsonl")) == 3
    assert len(load_jsonl_records(tmp_path / "simulations.jsonl")) == 7
    text = format_summary(summary)
    assert "completed=9/10" in text
    assert "success_rate=90.0%" in text
    assert "threshold_met=True" in text


@pytest.mark.openmc
def test_run_test_set_can_use_plan_workflow_with_tools(tmp_path: Path) -> None:
    pytest.importorskip("openmc", reason="OpenMC is required for this integration test")
    calls = {"export": 0, "plot": 0, "smoke": 0}

    def fake_generate_material(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=material(requirement))

    def fake_generate_plan(*, requirement: str, schema, model: str):
        if "无法完成" in requirement:
            return StructuredOutputResult(ok=False, error="unsupported boundary case")
        return StructuredOutputResult(ok=True, value=simulation_plan())

    def fake_repair_plan(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationPlan,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=simulation_plan())

    def fake_export(model_path):
        calls["export"] += 1
        return ToolResult(name="export_xml", ok=True, returncode=0)

    def fake_plot(run_dir):
        calls["plot"] += 1
        return ToolResult(name="run_geometry_plots", ok=True, returncode=0)

    def fake_smoke(run_dir, plan):
        calls["smoke"] += 1
        return ToolResult(name="run_smoke_test", ok=True, returncode=0, stdout="ok")

    summary = run_test_set(
        output_dir=tmp_path / "outputs",
        material_records_path=tmp_path / "materials.jsonl",
        simulation_records_path=tmp_path / "simulations.jsonl",
        generate_material=fake_generate_material,
        use_plan=True,
        enable_plots=True,
        enable_smoke_test=True,
        generate_plan=fake_generate_plan,
        repair_plan=fake_repair_plan,
        export_xml_tool=fake_export,
        plot_tool=fake_plot,
        smoke_test_tool=fake_smoke,
    )

    assert summary.completed == 9
    assert calls == {"export": 6, "plot": 6, "smoke": 6}

# ---------------------------------------------------------------------------
# P0 evaluation backbone schema / trace contract tests
# ---------------------------------------------------------------------------

from openmc_agent.benchmark_runner import load_evaluation_cases
from openmc_agent.evaluation import (
    EvaluationCase,
    EvaluationResult,
    aggregate_evaluation_results,
    evaluate_trace_against_case,
)
from openmc_agent.workflow_trace import TraceRecorder


def test_evaluation_case_p0_fields_are_defaulted_and_legacy_compatible() -> None:
    old = EvaluationCase("legacy-pin", "pin_cell", "build a pin")

    assert old.case_id == "legacy-pin"
    assert old.kind == "pin_cell"
    assert old.requirement == "build a pin"
    assert old.forbidden_issue_codes == []
    assert old.expected_incremental_patch_types == []
    assert old.expected_artifact_keys == []
    assert old.expected_planning_mode is None


def test_evaluate_trace_fails_on_forbidden_issue_code() -> None:
    recorder = TraceRecorder()
    recorder.add_event("validation_completed", issue_codes=["bad.issue"])

    result = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="forbidden",
            category="pin_cell",
            user_request="x",
            forbidden_issue_codes=["bad.issue"],
        ),
    )

    assert result.passed is False
    assert any("forbidden issue codes observed" in r for r in result.failure_reasons)
    assert result.metrics["forbidden_issue_code_count"] == 1


def test_evaluate_trace_expected_planning_mode_match_and_mismatch() -> None:
    recorder = TraceRecorder()
    recorder.add_event("plan_generated", metadata={"planning_mode": "incremental"})

    matched = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="inc",
            category="assembly",
            user_request="x",
            expected_planning_mode="incremental",
        ),
    )
    mismatched = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="mono",
            category="assembly",
            user_request="x",
            expected_planning_mode="monolithic",
        ),
    )

    assert matched.passed is True
    assert matched.metrics["planning_mode_match"] is True
    assert mismatched.passed is False
    assert any("planning mode mismatch" in r for r in mismatched.failure_reasons)


def test_evaluate_trace_expected_incremental_patch_types_reports_missing() -> None:
    recorder = TraceRecorder()
    recorder.add_event(
        "plan_generated",
        metadata={"patch_status": {"facts": "valid", "materials": "valid"}},
    )

    result = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="patches",
            category="assembly",
            user_request="x",
            expected_incremental_patch_types=["facts", "materials", "axial_overlays"],
        ),
    )

    assert result.passed is False
    assert any("missing expected incremental patch types" in r for r in result.failure_reasons)
    assert "axial_overlays" in result.failure_reasons[0]


def test_evaluate_trace_artifact_completeness_present_and_missing() -> None:
    recorder = TraceRecorder()
    recorder.add_event(
        "workflow_completed",
        metadata={"artifact_keys": ["workflow_trace", "capability_report"]},
    )

    present = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="art-ok",
            category="pin_cell",
            user_request="x",
            expected_artifact_keys=["workflow_trace", "capability_report"],
            expected_artifact_complete=True,
        ),
    )
    missing = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="art-missing",
            category="pin_cell",
            user_request="x",
            expected_artifact_keys=["workflow_trace", "capability_report", "model_py"],
        ),
    )

    assert present.passed is True
    assert present.metrics["artifact_complete"] is True
    assert missing.passed is False
    assert any("missing expected artifacts" in r for r in missing.failure_reasons)


def test_aggregate_evaluation_results_p0_metrics() -> None:
    results = [
        EvaluationResult(
            case_id="a",
            passed=True,
            metrics={
                "plan_schema_success": True,
                "incremental_patch_success": True,
                "artifact_complete": True,
                "planning_mode_match": True,
            },
        ),
        EvaluationResult(
            case_id="b",
            passed=False,
            metrics={
                "plan_schema_success": False,
                "incremental_patch_success": False,
                "artifact_complete": True,
                "planning_mode_match": False,
            },
        ),
        EvaluationResult(
            case_id="c",
            passed=True,
            metrics={"plan_schema_success": True},
        ),
    ]

    metrics = aggregate_evaluation_results(results)

    assert metrics.plan_schema_success_rate == 2 / 3
    assert metrics.incremental_patch_success_rate == 0.5
    assert metrics.artifact_completeness_rate == 1.0
    assert metrics.planning_mode_accuracy == 0.5


def test_evaluation_cases_fixture_loads_and_contains_p0_cases() -> None:
    cases = load_evaluation_cases("tests/fixtures/evaluation_cases.json")
    case_ids = {case.case_id for case in cases}

    assert {
        "pin-cell-basic",
        "assembly-2d-basic",
        "assembly-3d-overlays",
        "quarter-core-or-quarter-assembly",
        "fact-gap-case",
        "unsupported-hex-or-depletion",
    } <= case_ids
    assert all(isinstance(case.forbidden_issue_codes, list) for case in cases)
