"""Phase 3B: graph retry routing (execute_plan_retry, resume_plan_retry)."""

from __future__ import annotations

from openmc_agent.graph import _execute_plan_retry, _plan_generation_router, _plan_human_router, _resume_plan_retry


def test_router_returns_execute_plan_retry_when_pending_in_controlled_mode() -> None:
    state = {
        "plan_build_state": {
            "plan_loop_mode": "controlled",
            "plan_loop_stages": {},
            "plan_retry_pending_request_ids": ["retry_001"],
        },
    }
    assert _plan_generation_router(state) == "execute_plan_retry"


def test_router_returns_validate_when_no_pending_requests() -> None:
    state = {"plan_build_state": {"plan_loop_mode": "controlled", "plan_loop_stages": {}, "plan_retry_pending_request_ids": []}}
    assert _plan_generation_router(state) == "validate"


def test_router_returns_validate_in_advisory_mode_even_with_pending() -> None:
    state = {"plan_build_state": {"plan_loop_mode": "advisory", "plan_loop_stages": {}, "plan_retry_pending_request_ids": ["retry_001"]}}
    assert _plan_generation_router(state) == "validate"


def test_router_prefers_ask_plan_expert_over_retry() -> None:
    state = {
        "plan_build_state": {
            "plan_loop_mode": "controlled",
            "plan_loop_stages": {"plan_gate_facts": {"status": "awaiting_human"}},
            "plan_retry_pending_request_ids": ["retry_001"],
        },
    }
    assert _plan_generation_router(state) == "ask_plan_expert"


def test_human_router_distinguishes_retry_from_gate_resume() -> None:
    assert _plan_human_router({"plan_retry_human_resume": True}) == "resume_plan_retry"
    assert _plan_human_router({"plan_resume_requested": True}) == "resume"
    assert _plan_human_router({}) == "stop"


def test_resume_plan_retry_clears_resume_flag() -> None:
    state = {
        "plan_build_state": {
            "state_id": "s",
            "requirement_text": "r",
            "planning_mode": "incremental",
            "plan_retry_human_questions": {"q1": {"retry_request_id": "retry_001"}},
            "plan_retry_requests": {},
            "plan_retry_pending_request_ids": ["retry_001"],
        },
        "plan_human_answers": {"q1": {"question_id": "q1", "answered_by": "user"}},
        "plan_retry_human_resume": True,
    }
    result = _resume_plan_retry(state)
    assert result["plan_retry_human_resume"] is False
    assert result["plan_resume_requested"] is False
