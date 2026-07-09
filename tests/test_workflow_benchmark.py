import json
from pathlib import Path

from openmc_agent.evaluation import EvaluationCase
from openmc_agent.workflow_benchmark import WorkflowBenchmarkConfig, run_workflow_benchmark
from openmc_agent.workflow_trace import TraceRecorder


def _write_cases(path: Path, cases: list[dict]) -> Path:
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def _basic_cases() -> list[dict]:
    return [
        {
            "case_id": "pin-cell-basic-test",
            "category": "pin_cell",
            "user_request": "Build a simple pin cell.",
            "expected_issue_codes": [],
            "expected_renderability": "runnable",
            "expected_supported_renderer": "pin_cell",
            "should_trigger_retrieval": False,
            "should_require_human_confirmation": False,
            "expected_planning_mode": "monolithic",
            "expected_plan_schema_success": True,
            "expected_artifact_complete": True,
            "expected_artifact_keys": ["workflow_trace", "capability_report"],
        },
        {
            "case_id": "assembly-basic-test",
            "category": "assembly",
            "user_request": "Build a simple assembly.",
            "expected_issue_codes": [],
            "expected_renderability": "exportable",
            "expected_supported_renderer": "assembly",
            "should_trigger_retrieval": True,
            "should_require_human_confirmation": False,
            "expected_planning_mode": "monolithic",
            "expected_plan_schema_success": True,
            "expected_artifact_complete": True,
            "expected_artifact_keys": ["workflow_trace", "capability_report"],
        },
    ]


def test_run_workflow_benchmark_runs_fake_cases_and_writes_reports(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", _basic_cases())
    out = tmp_path / "out"

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(cases_path=cases_path, output_dir=out, model="fake")
    )

    assert result.case_count == 2
    assert result.metrics.pass_rate == 1.0
    assert Path(result.report_path).exists()
    assert Path(result.summary_path).exists()
    assert (out / "evaluation_report.json").exists()
    assert (out / "benchmark_summary.md").exists()


def test_workflow_benchmark_category_filter(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", _basic_cases())

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(
            cases_path=cases_path,
            output_dir=tmp_path / "out",
            model="fake",
            categories=["assembly"],
        )
    )

    assert result.case_count == 1
    assert result.cases[0].case_id == "assembly-basic-test"


def test_workflow_benchmark_max_cases(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", _basic_cases())

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(
            cases_path=cases_path,
            output_dir=tmp_path / "out",
            model="fake",
            max_cases=1,
        )
    )

    assert result.case_count == 1
    assert result.cases[0].case_id == "pin-cell-basic-test"


def test_failed_case_appears_in_summary(monkeypatch, tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", [_basic_cases()[0]])

    def fake_failed_runner(case: EvaluationCase, ablation):
        recorder = TraceRecorder()
        recorder.add_event(
            "workflow_failed",
            issue_codes=["planning.failed"],
            metadata={"failed_stage": "planning", "error": "boom"},
        )
        recorder.trace.final_status = "failed"
        return recorder.trace

    monkeypatch.setattr("openmc_agent.workflow_benchmark.fake_case_runner", fake_failed_runner)

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(cases_path=cases_path, output_dir=tmp_path / "out", model="fake")
    )

    assert result.case_count == 1
    assert result.cases[0].passed is False
    assert result.cases[0].failed_stage == "planning"
    assert result.cases[0].issue_codes == ["planning.failed"]
    summary = (tmp_path / "out" / "benchmark_summary.md").read_text(encoding="utf-8")
    assert "## Failed cases" in summary
    assert "pin-cell-basic-test" in summary
    report = json.loads((tmp_path / "out" / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["cases"][0]["failed_stage"] == "planning"
    assert report["cases"][0]["issue_codes"] == ["planning.failed"]


def test_trace_files_and_case_artifacts_are_written(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", [_basic_cases()[0]])
    out = tmp_path / "out"

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(cases_path=cases_path, output_dir=out, model="fake")
    )

    assert result.cases[0].trace_path == "traces/pin-cell-basic-test.json"
    assert (out / result.cases[0].trace_path).exists()
    assert result.cases[0].artifact_dir == "case_artifacts/pin-cell-basic-test"
    assert (out / result.cases[0].artifact_dir / "case_result.json").exists()


def test_artifact_completeness_metric_is_carried_through(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json", [_basic_cases()[0]])

    result = run_workflow_benchmark(
        WorkflowBenchmarkConfig(cases_path=cases_path, output_dir=tmp_path / "out", model="fake")
    )

    assert result.cases[0].artifact_complete is True
    assert result.metrics.artifact_completeness_rate == 1.0
