from openmc_agent.evaluation import EvaluationCase, evaluate_trace_against_case
from openmc_agent.workflow_trace import TraceRecorder


def trace_with(codes, mode="strict_evaluation"):
    r=TraceRecorder(); r.add_event("semantic_audit_completed", metadata={"finding_codes":codes,"finding_count":len(codes),"mode":mode}); r.add_event("workflow_completed") ; return r.trace


def test_strict_missing_expected_fails():
    case=EvaluationCase(case_id="c", user_request="", expected_audit_finding_codes=["audit.x"])
    ev=evaluate_trace_against_case(trace_with([]), case)
    assert not ev.passed


def test_warning_only_does_not_fail_on_missing_expected():
    case=EvaluationCase(case_id="c", user_request="", expected_audit_finding_codes=["audit.x"])
    ev=evaluate_trace_against_case(trace_with([], mode="warning_only"), case)
    assert ev.passed
