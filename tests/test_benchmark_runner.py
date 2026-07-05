"""Tests for the benchmark / ablation runner (Step 7).

These tests exercise the runner mechanics using a fake, offline case runner
that never calls a real LLM or OpenMC. They cover case loading, the benchmark
runner, the ablation study runner, ablation -> RetrievalPolicy mapping, and the
markdown summaries.
"""

import json
from pathlib import Path

import pytest

from openmc_agent.benchmark_runner import (
    DEFAULT_ABLATIONS,
    AblationConfig,
    AblationStudyResult,
    BenchmarkCaseResult,
    BenchmarkConfig,
    BenchmarkRunResult,
    fake_case_runner,
    format_ablation_summary,
    format_benchmark_summary,
    load_evaluation_cases,
    retrieval_policy_from_ablation,
    run_ablation_study,
    run_benchmark,
)
from openmc_agent.evaluation import EvaluationCase, EvaluationResult
from openmc_agent.retrieval_orchestrator import RetrievalPolicy
from openmc_agent.workflow_trace import TraceRecorder, WorkflowTrace

FIXTURE_PATH = Path("tests/fixtures/evaluation_cases.json")


# ---------------------------------------------------------------------------
# Case loader
# ---------------------------------------------------------------------------


def test_load_evaluation_cases_reads_json_list(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {"case_id": "a", "category": "material", "user_request": "make uo2"},
                {"case_id": "b", "category": "pin_cell", "user_request": "make pin"},
            ]
        ),
        encoding="utf-8",
    )

    cases = load_evaluation_cases(path)

    assert [case.case_id for case in cases] == ["a", "b"]
    assert cases[0].category == "material"


def test_load_evaluation_cases_reads_cases_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps({"cases": [{"case_id": "a", "category": "material", "user_request": "x"}]}),
        encoding="utf-8",
    )

    cases = load_evaluation_cases(path)

    assert len(cases) == 1
    assert cases[0].case_id == "a"


def test_load_evaluation_cases_category_filter(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {"case_id": "a", "category": "material", "user_request": "x"},
                {"case_id": "b", "category": "pin_cell", "user_request": "y"},
                {"case_id": "c", "category": "material", "user_request": "z"},
            ]
        ),
        encoding="utf-8",
    )

    cases = load_evaluation_cases(path, categories=["material"])

    assert [case.case_id for case in cases] == ["a", "c"]


def test_load_evaluation_cases_max_cases(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {"case_id": "a", "category": "material", "user_request": "x"},
                {"case_id": "b", "category": "material", "user_request": "y"},
                {"case_id": "c", "category": "material", "user_request": "z"},
            ]
        ),
        encoding="utf-8",
    )

    cases = load_evaluation_cases(path, max_cases=2)

    assert [case.case_id for case in cases] == ["a", "b"]


def test_load_evaluation_cases_invalid_case_raises(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"category": "material"}]), encoding="utf-8")  # no case_id

    with pytest.raises(ValueError):
        load_evaluation_cases(path)


def test_load_evaluation_cases_reuses_existing_fixture() -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)

    assert len(cases) >= 5
    assert {case.case_id for case in cases} >= {
        "pin-cell-valid",
        "hex-lattice-unsupported",
        "runtime-geometry-overlap",
        "cross-sections-missing",
        "dangling-lattice-universe",
    }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _runnable_pin(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        category="pin_cell",
        user_request=f"build {case_id}",
        expected_renderability="runnable",
        expected_supported_renderer="pin_cell",
    )


def test_run_benchmark_produces_case_results_and_metrics(tmp_path: Path) -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)
    config = BenchmarkConfig(
        run_id="unit-run",
        name="unit_bench",
        output_dir=str(tmp_path),
    )

    result = run_benchmark(
        cases,
        fake_case_runner,
        ablation=AblationConfig(name="full_stack"),
        config=config,
    )

    assert isinstance(result, BenchmarkRunResult)
    assert result.run_id == "unit-run"
    assert result.benchmark_name == "unit_bench"
    assert result.ablation_name == "full_stack"
    assert len(result.case_results) == len(cases)
    for case_result in result.case_results:
        assert isinstance(case_result, BenchmarkCaseResult)
        assert isinstance(case_result.trace, WorkflowTrace)
        assert isinstance(case_result.evaluation, EvaluationResult)
        assert case_result.ablation_name == "full_stack"
    assert result.metrics is not None
    assert result.metrics.case_count == len(cases)


def test_run_benchmark_fake_runner_passes_all_fixture_cases() -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)

    result = run_benchmark(
        cases, fake_case_runner, ablation=AblationConfig(name="full_stack")
    )

    assert result.metrics is not None
    assert result.metrics.pass_count == len(cases)
    assert result.metrics.pass_rate == 1.0


def test_run_benchmark_case_runner_exception_does_not_break_run() -> None:
    cases = [_runnable_pin("ok"), _runnable_pin("boom")]

    def flaky_runner(case: EvaluationCase, ablation: AblationConfig) -> WorkflowTrace:
        if case.case_id == "boom":
            raise RuntimeError("simulated case crash")
        return fake_case_runner(case, ablation)

    result = run_benchmark(cases, flaky_runner, ablation=AblationConfig(name="full_stack"))

    assert len(result.case_results) == 2
    failed = next(cr for cr in result.case_results if cr.case.case_id == "boom")
    assert failed.evaluation.passed is False
    assert failed.warnings
    assert any("simulated case crash" in warning for warning in failed.warnings)
    assert result.metrics is not None
    assert result.metrics.pass_count == 1
    assert result.metrics.fail_count == 1


def test_run_benchmark_aggregate_metrics_are_correct() -> None:
    cases = [
        _runnable_pin("pass-a"),
        EvaluationCase(
            case_id="fail-b",
            category="pin_cell",
            user_request="expects runnable but trace says skeleton",
            expected_renderability="runnable",
            expected_supported_renderer="pin_cell",
        ),
    ]

    def mismatch_runner(case: EvaluationCase, ablation: AblationConfig) -> WorkflowTrace:
        if case.case_id == "fail-b":
            recorder = TraceRecorder()
            recorder.add_event(
                "workflow_completed", renderability="skeleton", supported_renderer="pin_cell"
            )
            recorder.trace.final_renderability = "skeleton"
            recorder.trace.final_supported_renderer = "pin_cell"
            return recorder.trace
        return fake_case_runner(case, ablation)

    result = run_benchmark(cases, mismatch_runner, ablation=AblationConfig(name="full_stack"))

    assert result.metrics is not None
    assert result.metrics.case_count == 2
    assert result.metrics.pass_count == 1
    assert result.metrics.pass_rate == 0.5


def test_run_benchmark_without_output_dir_writes_no_files(tmp_path: Path) -> None:
    cases = [_runnable_pin("a")]
    config = BenchmarkConfig(output_dir=None)

    run_benchmark(cases, fake_case_runner, ablation=AblationConfig(name="full_stack"), config=config)

    assert list(tmp_path.iterdir()) == []


def test_run_benchmark_with_output_dir_writes_artifacts(tmp_path: Path) -> None:
    cases = [_runnable_pin("a")]
    config = BenchmarkConfig(run_id="art-run", name="art_bench", output_dir=str(tmp_path))

    result = run_benchmark(
        cases,
        fake_case_runner,
        ablation=AblationConfig(name="full_stack"),
        config=config,
    )

    run_jsons = list(tmp_path.rglob("run_result.json"))
    summaries = list(tmp_path.rglob("summary.md"))
    case_jsonls = list(tmp_path.rglob("cases.jsonl"))
    trace_jsons = list(tmp_path.rglob("traces/*.json"))
    trace_jsonls = list(tmp_path.rglob("traces/*.jsonl"))

    assert len(run_jsons) == 1
    assert len(summaries) == 1
    assert len(case_jsonls) == 1
    assert len(trace_jsons) == 1
    assert len(trace_jsonls) == 1
    persisted = json.loads(run_jsons[0].read_text(encoding="utf-8"))
    assert persisted["run_id"] == result.run_id
    assert persisted["ablation_name"] == "full_stack"


# ---------------------------------------------------------------------------
# Ablation study runner
# ---------------------------------------------------------------------------


def test_default_ablations_contain_expected_set() -> None:
    names = [ablation.name for ablation in DEFAULT_ABLATIONS]

    assert {"full_stack", "no_grep", "no_graph", "no_rag", "no_retrieval", "no_auto_repair"} <= set(
        names
    )
    no_retrieval = next(a for a in DEFAULT_ABLATIONS if a.name == "no_retrieval")
    assert (
        no_retrieval.enable_grep is False
        and no_retrieval.enable_graph is False
        and no_retrieval.enable_rag is False
    )


def test_run_ablation_study_produces_result_per_ablation() -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)

    result = run_ablation_study(
        cases,
        fake_case_runner,
        ablations=DEFAULT_ABLATIONS,
        config=BenchmarkConfig(run_id="ab-run", name="ab_bench"),
    )

    assert isinstance(result, AblationStudyResult)
    assert result.run_id == "ab-run"
    assert len(result.ablation_results) == len(DEFAULT_ABLATIONS)
    for run_result in result.ablation_results:
        assert isinstance(run_result, BenchmarkRunResult)
        assert run_result.metrics is not None
        assert run_result.metrics.case_count == len(cases)
    assert "pass_rate_by_ablation" in result.comparison
    assert "retrieval_trigger_rate_by_ablation" in result.comparison
    assert "human_confirmation_rate_by_ablation" in result.comparison
    assert "issue_precision_by_ablation" in result.comparison
    assert "issue_recall_by_ablation" in result.comparison
    assert "case_count_by_ablation" in result.comparison
    assert "full_stack" in result.comparison["pass_rate_by_ablation"]


def test_run_ablation_study_no_retrieval_lowers_retrieval_trigger_rate() -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)

    result = run_ablation_study(
        cases,
        fake_case_runner,
        ablations=[AblationConfig(name="full_stack"), AblationConfig(
            name="no_retrieval", enable_grep=False, enable_graph=False, enable_rag=False
        )],
        config=BenchmarkConfig(run_id="cmp-run", name="cmp"),
    )

    full = result.comparison["retrieval_trigger_rate_by_ablation"]["full_stack"]
    none = result.comparison["retrieval_trigger_rate_by_ablation"]["no_retrieval"]
    assert full > 0.0
    assert none == 0.0


# ---------------------------------------------------------------------------
# AblationConfig -> RetrievalPolicy mapping
# ---------------------------------------------------------------------------


def test_retrieval_policy_full_stack_enables_all() -> None:
    policy = retrieval_policy_from_ablation(AblationConfig(name="full_stack"))

    assert isinstance(policy, RetrievalPolicy)
    assert policy.enable_grep is True
    assert policy.enable_graph is True
    assert policy.enable_rag is True


def test_retrieval_policy_no_grep_disables_only_grep() -> None:
    policy = retrieval_policy_from_ablation(AblationConfig(name="no_grep", enable_grep=False))

    assert policy.enable_grep is False
    assert policy.enable_graph is True
    assert policy.enable_rag is True


def test_retrieval_policy_no_graph_disables_only_graph() -> None:
    policy = retrieval_policy_from_ablation(AblationConfig(name="no_graph", enable_graph=False))

    assert policy.enable_graph is False
    assert policy.enable_grep is True
    assert policy.enable_rag is True


def test_retrieval_policy_no_rag_disables_only_rag() -> None:
    policy = retrieval_policy_from_ablation(AblationConfig(name="no_rag", enable_rag=False))

    assert policy.enable_rag is False
    assert policy.enable_grep is True
    assert policy.enable_graph is True


def test_retrieval_policy_no_retrieval_disables_all() -> None:
    policy = retrieval_policy_from_ablation(
        AblationConfig(
            name="no_retrieval",
            enable_grep=False,
            enable_graph=False,
            enable_rag=False,
        )
    )

    assert (policy.enable_grep, policy.enable_graph, policy.enable_rag) == (False, False, False)


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------


def test_format_benchmark_summary_contains_case_count_and_pass_rate() -> None:
    cases = [_runnable_pin("a")]
    result = run_benchmark(cases, fake_case_runner, ablation=AblationConfig(name="full_stack"))

    markdown = format_benchmark_summary(result)

    assert "# Benchmark:" in markdown
    assert "Cases:" in markdown
    assert "Pass rate:" in markdown
    assert "Retrieval trigger rate:" in markdown
    assert "full_stack" in markdown


def test_format_benchmark_summary_lists_failed_cases() -> None:
    cases = [_runnable_pin("ok"), _runnable_pin("bad")]

    def fail_bad(case: EvaluationCase, ablation: AblationConfig) -> WorkflowTrace:
        if case.case_id == "bad":
            recorder = TraceRecorder()
            recorder.add_event(
                "workflow_completed", renderability="skeleton", supported_renderer="pin_cell"
            )
            recorder.trace.final_renderability = "skeleton"
            return recorder.trace
        return fake_case_runner(case, ablation)

    result = run_benchmark(cases, fail_bad, ablation=AblationConfig(name="full_stack"))

    markdown = format_benchmark_summary(result)

    assert "Failed cases" in markdown
    assert "| bad" in markdown


def test_format_benchmark_summary_does_not_dump_full_trace() -> None:
    cases = [_runnable_pin("a")]
    result = run_benchmark(cases, fake_case_runner, ablation=AblationConfig(name="full_stack"))

    markdown = format_benchmark_summary(result)

    assert "trace_id" not in markdown
    assert "event_id" not in markdown
    assert "timestamp" not in markdown


def test_format_ablation_summary_contains_comparison_table() -> None:
    cases = [_runnable_pin("a")]
    result = run_ablation_study(
        cases,
        fake_case_runner,
        ablations=DEFAULT_ABLATIONS[:3],
        config=BenchmarkConfig(run_id="md-run", name="md_bench"),
    )

    markdown = format_ablation_summary(result)

    assert "# Ablation Study:" in markdown
    assert "| ablation" in markdown
    assert "full_stack" in markdown
    assert "pass rate" in markdown


def test_format_ablation_summary_does_not_dump_full_trace() -> None:
    cases = [_runnable_pin("a")]
    result = run_ablation_study(
        cases,
        fake_case_runner,
        ablations=DEFAULT_ABLATIONS[:2],
        config=BenchmarkConfig(run_id="md2-run", name="md_bench"),
    )

    markdown = format_ablation_summary(result)

    assert "event_id" not in markdown
    assert "trace_id" not in markdown
