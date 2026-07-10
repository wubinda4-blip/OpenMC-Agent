"""Tests for run supervisor evaluation integration: metrics, fixtures, benchmark."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from openmc_agent.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    aggregate_evaluation_results,
    evaluate_trace_against_case,
)
from openmc_agent.workflow_trace import (
    TraceRecorder,
    WorkflowTrace,
)


def _make_trace_with_supervisor_event(
    action: str = "continue_to_render",
    *,
    accepted: bool = True,
    vetoed: bool = False,
    fallback_used: bool = False,
    target_patch_type: str | None = None,
    decision_count: int = 1,
) -> WorkflowTrace:
    recorder = TraceRecorder()
    meta = {
        "proposed_action": action,
        "final_action": action,
        "target_patch_type": target_patch_type,
        "accepted": accepted,
        "executed": accepted,
        "vetoed": vetoed,
        "fallback_used": fallback_used,
        "veto_reasons": ["supervisor.render_with_blocking_issue"] if vetoed else [],
        "decision_count": decision_count,
        "state_fingerprint": "test_fp",
        "mode": "advisory",
        "confidence": 0.85,
    }
    recorder.add_event("run_supervisor_started", metadata={"decision_id": "test"})
    event_type = "run_supervisor_decision_vetoed" if vetoed else "run_supervisor_decision_accepted"
    if fallback_used:
        event_type = "run_supervisor_fallback_used"
    recorder.add_event(event_type, metadata=meta)
    return recorder.trace


class TestEvaluationCaseFields:

    def test_supervisor_fields_default_none(self):
        case = EvaluationCase(case_id="test")
        assert case.expected_supervisor_action is None
        assert case.forbidden_supervisor_actions == []
        assert case.expected_supervisor_target_patch_type is None
        assert case.expected_supervisor_accepted is None
        assert case.expected_supervisor_loop_detected is None

    def test_supervisor_fields_from_dict(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_action="continue_to_render",
            forbidden_supervisor_actions=["stop"],
            expected_supervisor_target_patch_type="pin_map",
            expected_supervisor_accepted=True,
        )
        assert case.expected_supervisor_action == "continue_to_render"
        assert case.forbidden_supervisor_actions == ["stop"]
        assert case.expected_supervisor_target_patch_type == "pin_map"


class TestEvaluationMetricsFields:

    def test_supervisor_metrics_default_none(self):
        m = EvaluationMetrics(case_count=1, pass_count=1, fail_count=0, pass_rate=1.0)
        assert m.supervisor_completion_rate is None
        assert m.supervisor_action_accuracy is None
        assert m.supervisor_veto_rate is None
        assert m.supervisor_fallback_rate is None


class TestEvaluateSupervisorTrace:

    def test_clean_plan_supervisor_metrics(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_action="continue_to_render",
            expected_supervisor_accepted=True,
        )
        trace = _make_trace_with_supervisor_event("continue_to_render", accepted=True)
        result = evaluate_trace_against_case(trace, case)
        assert result.passed
        assert result.metrics["supervisor_enabled"] is True
        assert result.metrics["supervisor_final_action"] == "continue_to_render"
        assert result.metrics["supervisor_action_match"] is True

    def test_action_mismatch_fails(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_action="request_human_confirmation",
        )
        trace = _make_trace_with_supervisor_event("continue_to_render")
        result = evaluate_trace_against_case(trace, case)
        assert not result.passed
        assert any("supervisor action mismatch" in r for r in result.failure_reasons)

    def test_forbidden_action_fails(self):
        case = EvaluationCase(
            case_id="test",
            forbidden_supervisor_actions=["continue_to_render"],
        )
        trace = _make_trace_with_supervisor_event("continue_to_render")
        result = evaluate_trace_against_case(trace, case)
        assert not result.passed
        assert any("forbidden supervisor" in r for r in result.failure_reasons)

    def test_target_patch_match(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_action="retry_patch",
            expected_supervisor_target_patch_type="pin_map",
        )
        trace = _make_trace_with_supervisor_event(
            "retry_patch", target_patch_type="pin_map",
        )
        result = evaluate_trace_against_case(trace, case)
        assert result.passed
        assert result.metrics["supervisor_target_patch_match"] is True

    def test_target_patch_mismatch(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_target_patch_type="pin_map",
        )
        trace = _make_trace_with_supervisor_event(
            "retry_patch", target_patch_type="axial_layers",
        )
        result = evaluate_trace_against_case(trace, case)
        assert not result.passed

    def test_vetoed_supervisor(self):
        case = EvaluationCase(case_id="test")
        trace = _make_trace_with_supervisor_event("continue_to_render", vetoed=True)
        result = evaluate_trace_against_case(trace, case)
        assert result.metrics["supervisor_vetoed"] is True

    def test_fallback_supervisor(self):
        case = EvaluationCase(
            case_id="test",
            expected_supervisor_fallback_used=True,
        )
        trace = _make_trace_with_supervisor_event(
            "stop", fallback_used=True, accepted=False,
        )
        result = evaluate_trace_against_case(trace, case)
        assert result.metrics["supervisor_fallback_used"] is True


class TestAggregateSupervisorMetrics:

    def test_completion_rate(self):
        from openmc_agent.evaluation import EvaluationResult
        results = [
            EvaluationResult(
                case=EvaluationCase(case_id="c1"),
                case_id="c1",
                passed=True,
                metrics={"supervisor_enabled": True, "supervisor_completed": True},
            ),
            EvaluationResult(
                case=EvaluationCase(case_id="c2"),
                case_id="c2",
                passed=True,
                metrics={"supervisor_enabled": True, "supervisor_completed": True},
            ),
        ]
        m = aggregate_evaluation_results(results)
        assert m.supervisor_completion_rate == 1.0

    def test_action_accuracy(self):
        from openmc_agent.evaluation import EvaluationResult
        results = [
            EvaluationResult(
                case=EvaluationCase(case_id="c1", expected_supervisor_action="continue_to_render"),
                case_id="c1",
                passed=True,
                metrics={
                    "supervisor_enabled": True,
                    "supervisor_completed": True,
                    "supervisor_final_action": "continue_to_render",
                    "supervisor_action_match": True,
                },
            ),
        ]
        m = aggregate_evaluation_results(results)
        assert m.supervisor_action_accuracy == 1.0

    def test_no_supervisor_cases_metrics_none(self):
        from openmc_agent.evaluation import EvaluationResult
        results = [
            EvaluationResult(
                case=EvaluationCase(case_id="c1"),
                case_id="c1",
                passed=True,
                metrics={"supervisor_enabled": False},
            ),
        ]
        m = aggregate_evaluation_results(results)
        assert m.supervisor_completion_rate is None
        assert m.supervisor_action_accuracy is None


class TestRegressionCases:

    def test_supervisor_cases_loaded(self):
        cases_path = Path("tests/fixtures/evaluation_cases.json")
        cases = json.loads(cases_path.read_text())
        sup_cases = [c for c in cases if c.get("expected_supervisor_action")]
        assert len(sup_cases) >= 4

    def test_clean_exportable_case(self):
        cases_path = Path("tests/fixtures/evaluation_cases.json")
        cases = json.loads(cases_path.read_text())
        clean = next(c for c in cases if c["case_id"] == "supervisor-clean-exportable")
        assert clean["expected_supervisor_action"] == "continue_to_render"
        assert clean["expected_supervisor_accepted"] is True

    def test_fact_gap_case(self):
        cases_path = Path("tests/fixtures/evaluation_cases.json")
        cases = json.loads(cases_path.read_text())
        fact_gap = next(c for c in cases if c["case_id"] == "supervisor-fact-gap-human")
        assert fact_gap["expected_supervisor_action"] == "request_human_confirmation"

    def test_malicious_veto_case(self):
        cases_path = Path("tests/fixtures/evaluation_cases.json")
        cases = json.loads(cases_path.read_text())
        mal = next(c for c in cases if c["case_id"] == "supervisor-malicious-render-vetoed")
        assert "continue_to_render" in mal["forbidden_supervisor_actions"]
