"""Phase 4: regression tests — Phase 1B/1C/2/3B still pass."""

from __future__ import annotations


def test_phase3b_owner_policy_scope_unchanged() -> None:
    from openmc_agent.plan_builder.closed_loop.retry_owner_policy import retry_owner_policy
    # Placement owner policy must still be scope-aware.
    p = retry_owner_policy("localized_insert.required_placement_missing", {"owner_patch_type": ""}, canonical_scope="single_assembly")
    assert p is not None
    assert p.owner_patch_types == ["pin_map"]


def test_phase3b_retry_request_normalization_unchanged() -> None:
    from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
    from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
    from openmc_agent.plan_builder.state import PlanBuildState
    state = PlanBuildState(state_id="regress", requirement_text="r")
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["u1"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None
    assert request.owner_patch_types == ["universes"]


def test_phase2_placement_preflight_unchanged() -> None:
    from openmc_agent.plan_builder.closed_loop.placement_issue_policy import placement_owner_patch_types
    owners = placement_owner_patch_types("localized_insert.required_placement_missing", canonical_scope="multi_assembly")
    assert owners == ["assembly_catalog"]


def test_contract_version_0_9() -> None:
    """Phase 8A Step 6B: contract bumped 0.8 → 0.9 for RETRIEVE_EVIDENCE."""

    from openmc_agent.plan_builder.closed_loop.models import PLAN_CLOSED_LOOP_CONTRACT_VERSION
    assert PLAN_CLOSED_LOOP_CONTRACT_VERSION == "0.9"


def test_legacy_0_5_checkpoint_loads_without_clearing() -> None:
    """A 0.5 checkpoint with a skipped material-universe stage migrates to pending."""
    from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state
    from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanStageState, PlanStageStatus
    from openmc_agent.plan_builder.state import PlanBuildState
    state = PlanBuildState(state_id="migrate", requirement_text="r")
    state.plan_loop_contract_version = "0.5"
    state.plan_loop_stages["plan_gate_material_universe"] = PlanStageState(stage_id="plan_gate_material_universe", gate_id=PlanGateId.MATERIAL_UNIVERSE, status=PlanStageStatus.SKIPPED, metadata={"review_not_implemented": True})
    policy = PlanClosedLoopPolicy(mode="advisory", gate_enabled={PlanGateId.MATERIAL_UNIVERSE: True})
    initialize_plan_loop_state(state, policy, ["materials", "universes"])
    assert state.plan_loop_contract_version == "0.9"
    assert state.plan_loop_stages["plan_gate_material_universe"].status is PlanStageStatus.PENDING
    assert any(e.event_type == "planning.material_universe_gate_migrated_to_0_6" for e in state.build_log)
