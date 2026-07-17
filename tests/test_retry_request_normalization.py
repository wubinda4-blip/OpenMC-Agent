from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.state import PlanBuildState


def _state() -> PlanBuildState:
    return PlanBuildState(state_id="retry-state", requirement_text="reactor-neutral requirement")


def test_legacy_placement_request_routes_owner_in_python() -> None:
    state = _state()
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "facts", "required_ids": ["insert_u"], "reason": "untrusted text"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["universes"]
    assert request.targets[0].required_ids == ["insert_u"]


def test_unknown_issue_is_rejected_without_guessing_owner() -> None:
    state = _state()
    assert normalize_retry_request({"issue_codes": ["invented.error"]}, state=state) is None
    assert not state.plan_retry_requests
