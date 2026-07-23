"""Phase 8A Step 6A — cumulative stop_after_gate prefix tests (P0-6).

Verifies that ``--stop-after-gate`` is a cumulative prefix, not an
exact set.  Stopping at ``material_universe`` must still enable the
Facts Gate as a barrier; stopping at ``placement`` enables facts +
material_universe + placement; etc.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.policy import (
    canonical_gate_order,
    enabled_gates_through,
)
from openmc_agent.plan_builder.closed_loop.models import PlanGateId
from openmc_agent.real_campaign_harness import (
    CanaryCampaignConfig,
    CanaryRunConfig,
    RealCampaignCaseSpec,
    make_five_gate_controlled_policy,
)


def test_facts_stop_enables_only_facts() -> None:
    gates = enabled_gates_through("facts")
    assert [g.value for g in gates] == ["facts"]


def test_material_universe_stop_enables_facts_and_material_universe() -> None:
    """P0-6 regression: previously this enabled ONLY material_universe,
    breaking the Facts→MU ordering invariant."""

    gates = enabled_gates_through("material_universe")
    assert [g.value for g in gates] == ["facts", "material_universe"]


def test_placement_stop_enables_three_gates() -> None:
    gates = enabled_gates_through("placement")
    assert [g.value for g in gates] == [
        "facts", "material_universe", "placement",
    ]


def test_axial_geometry_stop_enables_four_gates() -> None:
    gates = enabled_gates_through("axial_geometry")
    assert [g.value for g in gates] == [
        "facts", "material_universe", "placement", "axial_geometry",
    ]


def test_assembled_plan_stop_enables_all_five_gates() -> None:
    gates = enabled_gates_through("assembled_plan")
    assert [g.value for g in gates] == [
        "facts", "material_universe", "placement",
        "axial_geometry", "assembled_plan",
    ]


def test_stop_after_gate_accepts_plan_gate_id_enum() -> None:
    """The helper accepts both string and PlanGateId enum values."""

    gates_str = enabled_gates_through("placement")
    gates_enum = enabled_gates_through(PlanGateId.PLACEMENT)
    assert gates_str == gates_enum


def test_unknown_target_enables_all_gates() -> None:
    """Unknown target name is defensive — enable everything."""

    gates = enabled_gates_through("totally_unknown_gate")
    assert len(gates) == len(canonical_gate_order())


def test_harness_make_policy_with_material_universe_stop_enables_facts() -> None:
    """The harness factory uses enabled_gates_through so the cumulative
    prefix is reflected in the produced policy."""

    cumulative = enabled_gates_through("material_universe")
    policy = make_five_gate_controlled_policy(
        enabled_gate_ids=tuple(g.value for g in cumulative),
    )
    # Both facts and material_universe must be enabled.
    from openmc_agent.plan_builder.closed_loop.policy import enabled_gates
    active = [g.value for g in enabled_gates(policy)]
    assert "facts" in active
    assert "material_universe" in active
    assert "placement" not in active
    assert "axial_geometry" not in active


def test_harness_policy_records_stop_target() -> None:
    policy = make_five_gate_controlled_policy(
        enabled_gate_ids=("facts", "material_universe"),
        stop_after_gate="material_universe",
    )
    assert policy.stop_after_gate is PlanGateId.MATERIAL_UNIVERSE


def test_run_config_carries_campaign_stop_after_gate() -> None:
    case = RealCampaignCaseSpec(
        case_id="x",
        input_path="/tmp/x.md",
        operating_state="",
        benchmark_label="X",
        model="fake:test",
        output_dir="/tmp/out",
    )
    campaign = CanaryCampaignConfig(
        case=case,
        runs=1,
        model="fake:test",
        stop_after_gate="material_universe",
    )
    run_config = CanaryRunConfig(
        run_id="run_001",
        run_index=1,
        case=campaign.case,
        policy=object(),
        env_status=object(),
        fingerprint=object(),
        output_dir="/tmp/out/runs/run_001",
        model=campaign.model,
        stop_after_gate=campaign.stop_after_gate,
    )
    assert run_config.stop_after_gate == "material_universe"


def test_graph_router_stops_on_incremental_stop_after_gate() -> None:
    from openmc_agent.graph import _plan_generation_router

    route = _plan_generation_router({
        "incremental_execution_result": {
            "ok": True,
            "summary": {"stopped_after_gate": "material_universe"},
        },
        "plan_build_state": {},
    })

    assert route == "stop"


def test_harness_make_policy_with_placement_stop_enables_facts_and_mu() -> None:
    """Placement stop must include Facts + MU as barriers."""

    cumulative = enabled_gates_through("placement")
    policy = make_five_gate_controlled_policy(
        enabled_gate_ids=tuple(g.value for g in cumulative),
    )
    from openmc_agent.plan_builder.closed_loop.policy import enabled_gates
    active = [g.value for g in enabled_gates(policy)]
    assert active == ["facts", "material_universe", "placement"]
