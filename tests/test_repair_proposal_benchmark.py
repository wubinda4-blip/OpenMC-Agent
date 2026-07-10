from openmc_agent.evaluation import EvaluationResult, aggregate_evaluation_results


def test_repair_aggregate_metrics():
    r1=EvaluationResult(case_id="a", passed=True, metrics={"llm_repair_enabled":True,"llm_repair_completed":True,"llm_repair_status":"accepted","llm_repair_fallback_used":False,"llm_repair_resolved_issue_count":1,"llm_repair_new_issue_count":0})
    r2=EvaluationResult(case_id="b", passed=True, metrics={"llm_repair_enabled":True,"llm_repair_completed":True,"llm_repair_status":"unsafe","llm_repair_fallback_used":True,"llm_repair_resolved_issue_count":0,"llm_repair_new_issue_count":1})
    m=aggregate_evaluation_results([r1,r2])
    assert m.llm_repair_completion_rate == 1.0
    assert m.llm_repair_acceptance_rate == 0.5
    assert m.llm_repair_unsafe_rate == 0.5
