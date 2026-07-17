"""Phase 3B: Facts owner integration — Facts revision + regeneration path."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.retry_request_builders import build_retry_request_from_facts_issue
from openmc_agent.plan_builder.state import PlanBuildState


def test_facts_owner_request_from_critic_finding_preserves_evidence() -> None:
    state = PlanBuildState(state_id="facts-int", requirement_text="r")
    request = build_retry_request_from_facts_issue(
        issue_code="facts.model_scope_conflicts_with_planning_features",
        affected_json_paths=["/model_scope"],
        finding_ids=["finding_abc"],
        state=state,
        expected_value="multi_assembly_core",
        current_value="single_assembly",
        evidence_refs=["evidence_hash_1"],
    )
    assert request is not None
    assert request.owner_patch_types == ["facts"]
    assert request.gate_id is not None
    assert request.gate_id.value == "facts"
    assert request.targets[0].source_finding_ids == ["finding_abc"]
    assert request.targets[0].affected_json_paths == ["/model_scope"]
    assert "facts_schema" in request.targets[0].required_properties


def test_facts_owner_request_registered_in_state() -> None:
    state = PlanBuildState(state_id="facts-int", requirement_text="r")
    request = build_retry_request_from_facts_issue(
        issue_code="facts.localized_insert_contract_missing",
        affected_json_paths=["/localized_insert_requirements"],
        finding_ids=[],
        state=state,
    )
    assert request is not None
    assert request.request_id in state.plan_retry_requests
    assert request.request_id in state.plan_retry_pending_request_ids
