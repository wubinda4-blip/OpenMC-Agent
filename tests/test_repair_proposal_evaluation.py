from openmc_agent.evaluation import EvaluationCase, evaluate_trace_against_case
from openmc_agent.workflow_trace import TraceRecorder


def test_repair_evaluation_metrics_and_expectations():
    r=TraceRecorder(); meta={"status":"accepted", "source_issue_codes":["x"], "operation_count":1, "allowed_operation_count":1, "rejected_operation_count":0, "unsafe_operation_count":0, "resolved_issue_codes":["x"], "new_issue_codes":[], "applied_to_clone":True, "applied_to_workflow_plan":False, "operation_evaluations":[{"path":"/metadata/x", "allowed":True}]}
    r.add_event("llm_repair_proposal_accepted", metadata=meta)
    case=EvaluationCase(case_id="c", user_request="", expected_repair_status="accepted", expected_repair_source_issue_codes=["x"], expected_repair_resolved_issue_codes=["x"], expected_repair_applied_to_clone=True, expected_repair_applied_to_workflow_plan=False)
    ev=evaluate_trace_against_case(r.trace, case)
    assert ev.passed
    assert ev.metrics["llm_repair_accepted_count"] == 1
