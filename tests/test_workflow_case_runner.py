from pathlib import Path

from openmc_agent.benchmark_runner import AblationConfig
from openmc_agent.evaluation import EvaluationCase
from openmc_agent.workflow_case_runner import (
    WorkflowCaseRunnerConfig,
    make_workflow_case_runner,
    run_workflow_case,
)
from openmc_agent.workflow_trace import TraceRecorder, WorkflowTrace


class FakeGraph:
    def __init__(self, state):
        self.state = state

    def invoke(self, inputs):
        if callable(self.state):
            return self.state(inputs)
        return self.state


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="runner-case",
        category="pin_cell",
        user_request="Build a pin-cell.",
    )


def test_workflow_case_runner_returns_trace_on_graph_success(monkeypatch, tmp_path: Path) -> None:
    def fake_build_plan_graph(**kwargs):
        recorder = TraceRecorder()
        recorder.add_event("plan_generated", metadata={"planning_mode": "monolithic"})
        recorder.add_event(
            "capability_assessed",
            renderability="runnable",
            supported_renderer="pin_cell",
        )
        return FakeGraph(
            {
                "trace": recorder.export_json(),
                "simulation_plan": {"capability_report": {"renderability": "runnable", "supported_renderer": "pin_cell"}},
                "validation_report": {"is_valid": True, "issue_codes": []},
                "plan_artifacts": {"capability_report": "capability_report.json"},
                "planning_mode_decision": {"mode": "monolithic"},
                "error": "",
            }
        )

    monkeypatch.setattr("openmc_agent.workflow_case_runner.build_plan_graph", fake_build_plan_graph)

    trace = run_workflow_case(
        _case(),
        WorkflowCaseRunnerConfig(output_dir=str(tmp_path)),
    )

    assert isinstance(trace, WorkflowTrace)
    assert any(event.event_type == "workflow_completed" for event in trace.events)
    assert trace.final_renderability == "runnable"
    summary_event = trace.events[-1]
    assert summary_event.metadata["planning_mode"] == "monolithic"
    assert summary_event.metadata["capability_report"]["renderability"] == "runnable"


def test_workflow_case_runner_returns_trace_on_graph_exception(monkeypatch, tmp_path: Path) -> None:
    def fake_build_plan_graph(**kwargs):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr("openmc_agent.workflow_case_runner.build_plan_graph", fake_build_plan_graph)

    trace = run_workflow_case(
        _case(),
        WorkflowCaseRunnerConfig(output_dir=str(tmp_path)),
    )

    assert trace.final_status == "failed"
    assert any(event.event_type == "workflow_failed" for event in trace.events)
    failed = trace.events[-1]
    assert failed.metadata["failed_stage"] == "workflow_case_runner"
    assert "graph exploded" in failed.metadata["error"]
    assert failed.metadata["artifact_dir"]


def test_make_workflow_case_runner_is_benchmark_runner_compatible(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_build_plan_graph(**kwargs):
        captured.update(kwargs)
        recorder = TraceRecorder()
        recorder.add_event("workflow_completed", metadata={"planning_mode": "monolithic"})
        return FakeGraph(
            {
                "trace": recorder.export_json(),
                "validation_report": {"is_valid": True},
                "planning_mode_decision": {"mode": "monolithic"},
                "error": "",
            }
        )

    monkeypatch.setattr("openmc_agent.workflow_case_runner.build_plan_graph", fake_build_plan_graph)
    runner = make_workflow_case_runner(WorkflowCaseRunnerConfig(output_dir=str(tmp_path)))

    trace = runner(
        _case(),
        AblationConfig(
            name="no_graph",
            enable_grep=True,
            enable_graph=False,
            enable_rag=True,
            enable_auto_repair=False,
        ),
    )

    assert isinstance(trace, WorkflowTrace)
    assert captured["retrieval_policy"].enable_graph is False
    assert any(
        event.metadata.get("requested_ablation", {}).get("name") == "no_graph"
        for event in trace.events
    )
