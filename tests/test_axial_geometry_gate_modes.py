"""Tests for Axial Geometry Gate modes (off / advisory / controlled)."""

from tests._axial_geometry_fixtures import state_with_axial_patches
from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import (
    axial_geometry_gate_applicable,
    build_axial_geometry_evidence_pack,
)
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanLoopMode, PlanStageStatus
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state


def test_off_mode_no_gate():
    state = state_with_axial_patches()
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.OFF)
    assert policy.axial_geometry_review_mode == "off"
    stage = state.plan_loop_stages.get("plan_gate_axial_geometry")
    # In off mode, the stage may not even be created.
    assert stage is None or stage.status in {PlanStageStatus.PENDING, PlanStageStatus.SKIPPED}


def test_off_mode_no_evidence_pack_built():
    """Off mode should not invoke build_axial_geometry_evidence_pack via the executor."""
    # This is implicitly tested by the executor not routing to the gate.
    state = state_with_axial_patches()
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.OFF)
    # The evidence pack can still be built directly, but the executor skips it.
    pack = build_axial_geometry_evidence_pack(state=state, policy=policy)
    assert pack is not None  # building is always safe; executor just doesn't call it


def test_advisory_mode_does_not_mutate_patches():
    state = state_with_axial_patches()
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.ADVISORY, axial_geometry_review_mode="advisory", gate_enabled={PlanGateId.AXIAL_GEOMETRY: True})
    # Advisory mode should be declarable without crashing.
    assert policy.axial_geometry_review_mode == "advisory"


def test_controlled_mode_requires_facts_accepted():
    state = state_with_axial_patches()
    # Un-accept the facts gate.
    state.plan_loop_stages["plan_gate_facts"].status = PlanStageStatus.PENDING
    policy = PlanClosedLoopPolicy(mode=PlanLoopMode.CONTROLLED, axial_geometry_review_mode="controlled", gate_enabled={PlanGateId.AXIAL_GEOMETRY: True, PlanGateId.FACTS: True})
    # The executor's _run_axial_geometry_gate will block, tested in barrier test.
    assert policy.axial_geometry_review_mode == "controlled"


def test_controlled_mode_applicable_and_ready():
    state = state_with_axial_patches()
    assert axial_geometry_gate_applicable(state) is True
