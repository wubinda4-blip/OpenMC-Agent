"""Phase 3B: post-replay root-cause reclassification."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.reclassification import reclassify_retry_outcome
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState


def _request(state: PlanBuildState, code: str = "materials.execution_density_required"):
    return normalize_retry_request(
        {"issue_codes": [code], "required_ids": ["zircaloy4"], "required_property": "density_g_cm3", "reason": "x"},
        state=state, origin=RetryTriggerOrigin.MATERIAL_READINESS,
    )


def test_resolved_when_reason_code_disappears() -> None:
    state = PlanBuildState(state_id="reclass", requirement_text="r")
    request = _request(state)
    assert request is not None
    before = [{"code": "materials.execution_density_required", "severity": "error"}]
    after: list[dict] = []
    result = reclassify_retry_outcome(request=request, before_issues=before, after_issues=after)
    assert result.classification == "resolved"
    assert "materials.execution_density_required" in result.resolved_issue_codes


def test_no_progress_when_reason_code_persists() -> None:
    state = PlanBuildState(state_id="reclass", requirement_text="r")
    request = _request(state)
    before = [{"code": "materials.execution_density_required", "severity": "error"}]
    after = [{"code": "materials.execution_density_required", "severity": "error"}]
    result = reclassify_retry_outcome(request=request, before_issues=before, after_issues=after)
    assert result.classification == "no_progress"


def test_next_request_required_when_new_error_appears() -> None:
    state = PlanBuildState(state_id="reclass", requirement_text="r")
    request = _request(state)
    before = [{"code": "materials.execution_density_required", "severity": "error"}]
    after = [{"code": "localized_insert.required_universe_missing", "severity": "error"}]
    result = reclassify_retry_outcome(request=request, before_issues=before, after_issues=after)
    assert result.classification == "next_request_required"
    assert "localized_insert.required_universe_missing" in result.new_issue_codes


def test_partially_resolved_when_reason_gone_but_old_errors_remain() -> None:
    state = PlanBuildState(state_id="reclass", requirement_text="r")
    request = _request(state)
    before = [{"code": "materials.execution_density_required", "severity": "error"}, {"code": "other.error", "severity": "error"}]
    after = [{"code": "other.error", "severity": "error"}]
    result = reclassify_retry_outcome(request=request, before_issues=before, after_issues=after)
    assert result.classification == "partially_resolved"


def test_awaiting_human_when_new_human_error_appears() -> None:
    state = PlanBuildState(state_id="reclass", requirement_text="r")
    request = _request(state)
    before = [{"code": "materials.execution_density_required", "severity": "error"}]
    after = [{"code": "some.human_issue", "severity": "error", "requires_human": True}]
    result = reclassify_retry_outcome(request=request, before_issues=before, after_issues=after)
    assert result.classification == "awaiting_human"
