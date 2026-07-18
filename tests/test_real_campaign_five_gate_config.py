"""Five-gate policy configuration tests."""

from openmc_agent.plan_builder.closed_loop.models import (
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanLoopMode,
)
from openmc_agent.real_campaign_harness import (
    make_five_gate_controlled_policy,
    policy_hash,
)


def test_policy_enables_all_five_gates():
    policy = make_five_gate_controlled_policy()
    assert policy.mode == PlanLoopMode.CONTROLLED
    expected = {
        PlanGateId.FACTS,
        PlanGateId.MATERIAL_UNIVERSE,
        PlanGateId.PLACEMENT,
        PlanGateId.AXIAL_GEOMETRY,
        PlanGateId.ASSEMBLED_PLAN,
    }
    assert set(policy.plan_gates) == expected
    for gate in expected:
        assert policy.gate_enabled[gate] is True


def test_all_review_modes_are_controlled():
    policy = make_five_gate_controlled_policy()
    assert policy.placement_review_mode == "controlled"
    assert policy.material_universe_review_mode == "controlled"
    assert policy.axial_geometry_review_mode == "controlled"
    assert policy.assembled_plan_review_mode == "controlled"


def test_no_review_mode_is_advisory_or_off():
    policy = make_five_gate_controlled_policy()
    for mode in (
        policy.placement_review_mode,
        policy.material_universe_review_mode,
        policy.axial_geometry_review_mode,
        policy.assembled_plan_review_mode,
    ):
        assert mode == "controlled"


def test_policy_hash_is_stable():
    p1 = make_five_gate_controlled_policy()
    p2 = make_five_gate_controlled_policy()
    assert policy_hash(p1) == policy_hash(p2)


def test_policy_hash_changes_when_max_review_rounds_change():
    p1 = make_five_gate_controlled_policy(max_review_rounds_per_gate=2)
    p2 = make_five_gate_controlled_policy(max_review_rounds_per_gate=4)
    assert policy_hash(p1) != policy_hash(p2)


def test_contract_version_is_unchanged_by_phase7a():
    policy = make_five_gate_controlled_policy()
    assert policy.contract_version == "0.8"
