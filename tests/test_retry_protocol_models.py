from openmc_agent.plan_builder.closed_loop.retry_models import (
    ExecutablePlanRetryRequest,
    PlanRetryAction,
    RetryTargetSpec,
    RetryTriggerOrigin,
)


def _request(*, message: str = "ignored", owner_hash: str = "a") -> ExecutablePlanRetryRequest:
    return ExecutablePlanRetryRequest(
        request_id="r1", origin=RetryTriggerOrigin.PLACEMENT_GATE,
        action=PlanRetryAction.REGENERATE_OWNER_PATCH, owner_patch_types=["universes"],
        targets=[RetryTargetSpec(patch_type="universes", current_patch_hash=owner_hash, required_ids=["insert_u"], source_issue_codes=["localized_insert.required_universe_missing"])],
        source_issue_codes=["localized_insert.required_universe_missing"], reason_code="localized_insert.required_universe_missing",
        metadata={"message": message},
    )


def test_retry_fingerprint_ignores_message_and_tracks_owner_hash() -> None:
    assert _request(message="first").request_fingerprint == _request(message="second").request_fingerprint
    assert _request(owner_hash="a").request_fingerprint != _request(owner_hash="b").request_fingerprint


def test_retry_round_trip() -> None:
    request = _request()
    assert ExecutablePlanRetryRequest.model_validate(request.model_dump(mode="json")).request_fingerprint == request.request_fingerprint
