from openmc_agent.runtime_supervisor import (
    RuntimeLoopBudget,
    RuntimeSupervisorAction,
    RuntimeSupervisorDecision,
    RuntimeSupervisorInput,
    run_runtime_supervisor_decision,
    RuntimeIterationState,
    write_runtime_iteration_manifest,
)
from openmc_agent.runtime_supervisor_policy import (
    build_runtime_supervisor_input,
    compute_allowed_runtime_supervisor_actions,
    compute_runtime_supervisor_state_fingerprint,
    validate_runtime_supervisor_decision,
)


def _value(*, classification="plan_fixable", success=False, **extra):
    value = RuntimeSupervisorInput(
        decision_id="rts_test",
        current_primary_failure={"primary_issue_code": "runtime.test"},
        current_failure_fingerprint="rt_test",
        current_failure_classification=classification,
        execution_succeeded=success,
        deterministic_repair_available=extra.pop("deterministic", False),
        llm_diagnosis_available=extra.pop("llm", False),
        budget_remaining=extra.pop("budget", {"iterations": 4, "deterministic": 3, "llm_diagnoses": 2, "transient": 1}),
        **extra,
    )
    value.allowed_actions = compute_allowed_runtime_supervisor_actions(value)
    value.state_fingerprint = compute_runtime_supervisor_state_fingerprint(value)
    return value


def test_success_allows_only_finish_or_stop():
    value = _value(success=True)
    assert value.allowed_actions == [RuntimeSupervisorAction.FINISH_SUCCESS, RuntimeSupervisorAction.STOP]


def test_source_fixable_prefers_deterministic():
    value = _value(deterministic=True, llm=True)
    assert value.allowed_actions[0] == RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR


def test_llm_allowed_after_deterministic_unavailable():
    value = _value(deterministic=False, llm=True)
    assert value.allowed_actions[0] == RuntimeSupervisorAction.ATTEMPT_LLM_REPAIR


def test_environment_only_human_or_stop():
    value = _value(classification="environment")
    assert value.allowed_actions == [RuntimeSupervisorAction.REQUEST_HUMAN_CONFIRMATION, RuntimeSupervisorAction.STOP]


def test_human_fact_cannot_auto_repair():
    value = _value(classification="human_fact", deterministic=True, llm=True)
    assert RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR not in value.allowed_actions
    assert RuntimeSupervisorAction.ATTEMPT_LLM_REPAIR not in value.allowed_actions


def test_timeout_allows_one_retry():
    value = _value(classification="transient")
    assert value.allowed_actions[0] == RuntimeSupervisorAction.RETRY_SAME_PLAN
    exhausted = _value(classification="transient", budget={"iterations": 4, "transient": 0})
    assert exhausted.allowed_actions == [RuntimeSupervisorAction.STOP]


def test_no_progress_stops():
    value = _value(no_progress_count=2, deterministic=True)
    assert value.allowed_actions == [RuntimeSupervisorAction.STOP]


def test_veto_disallows_environment_repair():
    value = _value(classification="environment")
    decision = RuntimeSupervisorDecision(
        decision_id="rts_test", action=RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR,
        rationale="bad", confidence=1.0,
    )
    vetoes = validate_runtime_supervisor_decision(decision, value)
    assert "runtime_supervisor.action_not_allowed" in vetoes
    assert "runtime_supervisor.environment_repair_forbidden" in vetoes


def test_client_none_is_deterministic_not_fake():
    value = _value(deterministic=True)
    result = run_runtime_supervisor_decision(value, client=None)
    assert result.supervisor == "deterministic"
    assert result.final_action == RuntimeSupervisorAction.ATTEMPT_DETERMINISTIC_REPAIR


def test_fingerprint_ignores_history_paths_and_times():
    a = _value(deterministic=True)
    b = _value(deterministic=True)
    a.recent_actions = [{"action": "x", "path": "/tmp/a", "time": "2025-01-01"}]
    b.recent_actions = [{"action": "x", "path": "/home/b", "time": "2030-02-02"}]
    assert compute_runtime_supervisor_state_fingerprint(a) == compute_runtime_supervisor_state_fingerprint(b)


def test_build_input_uses_independent_runtime_counters():
    state = {
        "runtime_primary_failure": {
            "primary_issue_code": "runtime.source_not_in_active_fuel_region",
            "error_fingerprint": "rt_1", "classification": "plan_fixable",
        },
        "runtime_policy_summary": {"deterministic_repair_supported": True},
        "runtime_repair_count": 1,
        "retry_count": 99,
    }
    value = build_runtime_supervisor_input(state, budget=RuntimeLoopBudget(max_deterministic_attempts=3))
    assert value.deterministic_attempt_count == 1
    assert value.budget_remaining["deterministic"] == 2


def test_same_fingerprint_after_commit_stops_via_no_progress():
    value = _value(no_progress_count=2, deterministic=True)
    assert value.allowed_actions == [RuntimeSupervisorAction.STOP]


def test_iteration_manifest_is_compact_and_resumable(tmp_path):
    value = _value(deterministic=True)
    result = run_runtime_supervisor_decision(value)
    paths = write_runtime_iteration_manifest(
        tmp_path,
        RuntimeIterationState(iteration=0, plan_hash_before_execution="p", build_state_hash="b"),
        value,
        result,
        final_disposition="attempt_deterministic_repair",
    )
    assert (tmp_path / "runtime_loop" / "iteration_000" / "iteration_manifest.json").exists()
    assert (tmp_path / "runtime_loop_manifest.json").exists()
    assert all("statepoint" not in path for path in paths)


def test_runtime_supervisor_graph_builds_with_feature_flag():
    from openmc_agent.graph import build_plan_graph
    graph = build_plan_graph(enable_runtime_supervisor=True)
    assert graph is not None


def test_accepted_fixture_bypasses_patch_llm_generation(tmp_path):
    from openmc_agent.graph import build_plan_graph
    from openmc_agent.runtime_faults import load_vera3b_accepted_state

    state = load_vera3b_accepted_state()
    graph = build_plan_graph(
        enable_plots=False,
        enable_smoke_test=False,
        enable_runtime_supervisor=True,
    )
    result = graph.invoke({
        "requirement": state.requirement_text,
        "model": "fake",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "records.jsonl"),
        "accepted_plan_build_state": state.model_dump(mode="json"),
    })
    assert "patch_client_unavailable" not in result.get("error", "")
    assert result.get("simulation_plan") is not None


def test_vera3b_runtime_loop_harness_marks_missing_real_key(tmp_path, monkeypatch):
    from scripts.evaluate_vera3b_runtime_loop import main
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["harness", "--client", "real", "--output-dir", str(tmp_path)],
    )
    assert main() == 0
    import json
    report = json.loads((tmp_path / "runtime_loop_harness_report.json").read_text())
    assert report["status"] == "REAL_LLM_SKIPPED_ENV"
