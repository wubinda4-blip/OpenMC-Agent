import json

import pytest

from openmc_agent.plan_builder import executor
from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state, transition_stage
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy, PlanGateId, PlanStageStatus
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.patches import FactsPatch
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def test_controlled_facts_approve_precedes_downstream(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["facts"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["facts"])
    monkeypatch.setattr(executor, "assemble_state_if_ready", lambda state, **_: state.model_copy(update={"assembled_plan": {"ok": True}}))
    llm = FakePatchLLM([json.dumps({"patch_type": "facts"})])
    captured = {}
    def reviewer(_):
        captured["called"] = True
        # Evidence is only known after prompt construction; recover it from prompt payload.
        data = json.loads(_.split("INPUT:\n", 1)[1])
        evidence = data["source_excerpts"][0]["evidence_hash"]
        return json.dumps({"review_status": "complete", "reviewed_evidence_hashes": [evidence], "coverage_summary": {}, "findings": []})
    result = run_incremental_planning(requirement="small source", state=PlanBuildState(state_id="s", requirement_text="small source"), llm_client=llm, plan_loop_policy={"mode": "controlled"}, plan_reviewer_client=reviewer, plan_loop_output_dir=tmp_path)
    assert result.ok and captured["called"]
    assert result.state.plan_loop_stages["plan_gate_facts"].status.value == "accepted"
    gate_result = json.loads((tmp_path / "incremental" / "plan_closed_loop" / "facts_gate_result.json").read_text())
    assert gate_result["initial_decision"]["action"] == "approve"
    assert gate_result["candidate_validation"] == {"attempted": False, "final": None, "rounds": []}
    assert gate_result["candidate_commit"] == {"attempted": False, "committed": False}
    assert gate_result["final_gate_status"]["status"] == "accepted"


def test_controlled_resume_never_bypasses_blocked_facts_gate(monkeypatch) -> None:
    """A graph retry must not skip a valid facts envelope below a blocked gate."""
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["facts", "materials"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["facts", "materials"])

    state = PlanBuildState(state_id="blocked", requirement_text="source")
    state.add_patch(PlanPatchEnvelope(
        patch_id="facts_1",
        patch_type="facts",
        content=FactsPatch().model_dump(mode="json"),
        status="valid",
    ))
    policy = PlanClosedLoopPolicy(mode="controlled", gate_enabled={"facts": True})
    initialize_plan_loop_state(state, policy, ["facts", "materials"])
    transition_stage(state.plan_loop_stages["plan_gate_facts"], PlanStageStatus.BLOCKED)

    result = run_incremental_planning(
        requirement="source",
        state=state,
        llm_client=lambda _prompt: (_ for _ in ()).throw(AssertionError("downstream proposer must not run")),
        plan_loop_policy=policy,
    )

    assert not result.ok
    assert [issue.code for issue in result.issues] == ["planning.facts_gate_not_accepted"]
    assert result.plan_loop_outcome["active_gate_id"] == "facts"
    assert not any(env.patch_type == "materials" for env in state.patches.values())


def test_stop_after_material_universe_returns_before_downstream(monkeypatch, tmp_path) -> None:
    """A MU milestone canary must not continue into downstream patch generation."""

    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["facts", "materials", "universes", "assembly_catalog"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["facts", "materials", "universes", "assembly_catalog"])
    monkeypatch.setattr(executor, "assemble_state_if_ready", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("assembly must not run after MU stop")))

    from openmc_agent.plan_builder.closed_loop.facts_reviewer import FactsReviewResult
    from openmc_agent.plan_builder.closed_loop.material_universe_reviewer import MaterialUniverseReviewResult
    import openmc_agent.plan_builder.closed_loop.facts_reviewer as facts_reviewer_module
    import openmc_agent.plan_builder.closed_loop.material_universe_reviewer as mu_reviewer_module

    calls = {"facts": 0, "mu": 0}

    def accept_facts(**_kwargs):
        calls["facts"] += 1
        return FactsReviewResult(ok=True, coverage_complete=True, reviewer_calls=1)

    def accept_mu(**_kwargs):
        calls["mu"] += 1
        return MaterialUniverseReviewResult(ok=True, coverage_complete=True, reviewer_calls=1)

    monkeypatch.setattr(facts_reviewer_module, "run_facts_review", accept_facts)
    monkeypatch.setattr(mu_reviewer_module, "run_material_universe_review", accept_mu)

    fake = FakePatchLLM([
        json.dumps({"patch_type": "facts", "model_scope": "single_assembly"}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "fuel", "name": "fuel", "role": "fuel", "density_g_cm3": 10.0},
        ]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fuel", "kind": "fuel_pin", "cells": [
                {"id": "fuel", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4},
            ]},
        ]}),
    ])
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True},
        material_universe_review_mode="controlled",
        stop_after_gate=PlanGateId.MATERIAL_UNIVERSE,
    )

    result = run_incremental_planning(
        requirement="small source",
        state=PlanBuildState(state_id="stop-mu", requirement_text="small source"),
        llm_client=fake,
        max_patch_attempts=1,
        plan_loop_policy=policy,
        plan_reviewer_client=lambda _prompt: "unused",
        plan_loop_output_dir=tmp_path,
        universes_generation_mode="off",
    )

    assert result.ok
    assert result.summary["stopped_after_gate"] == "material_universe"
    assert result.plan_loop_outcome["status"] == "stopped_after_gate"
    assert result.state.plan_loop_stages["plan_gate_material_universe"].status is PlanStageStatus.ACCEPTED
    assert calls == {"facts": 1, "mu": 1}
    assert "assembly_catalog" not in {env.patch_type for env in result.state.patches.values()}


def test_stop_after_material_universe_honors_accepted_checkpoint(monkeypatch) -> None:
    """A restored accepted MU checkpoint should stop before any new LLM call."""

    from openmc_agent.plan_builder.closed_loop.controller import initialize_plan_loop_state, transition_stage

    state = PlanBuildState(state_id="stop-mu-resume", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "fuel", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "fuel", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}]}]}, status="valid"))
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True},
        material_universe_review_mode="controlled",
        stop_after_gate=PlanGateId.MATERIAL_UNIVERSE,
    )
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes", "assembly_catalog"])
    for stage_id in ("plan_gate_facts", "plan_gate_material_universe"):
        stage = state.plan_loop_stages[stage_id]
        transition_stage(stage, PlanStageStatus.PROPOSING)
        transition_stage(stage, PlanStageStatus.VALIDATING)
        transition_stage(stage, PlanStageStatus.REVIEWING)
        transition_stage(stage, PlanStageStatus.ACCEPTED)

    result = run_incremental_planning(
        requirement="r",
        state=state,
        llm_client=lambda _prompt: (_ for _ in ()).throw(AssertionError("LLM must not run after accepted MU checkpoint")),
        task_order=["assembly_catalog"],
        plan_loop_policy=policy,
    )

    assert result.ok
    assert result.summary["stopped_after_gate"] == "material_universe"


def test_mu_deterministic_preflight_routes_universe_retry_without_reviewer(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: ["materials"])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: ["materials"])
    monkeypatch.setattr(
        executor,
        "assemble_state_if_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("assembly must not run while MU preflight is blocked")
        ),
    )

    state = PlanBuildState(state_id="mu-det-retry", requirement_text="source")
    state.add_patch(PlanPatchEnvelope(
        patch_id="facts",
        patch_type="facts",
        content={
            "patch_type": "facts",
            "model_scope": "single_assembly",
            "fuel_variant_requirements": [
                {"variant_id": "region1", "enrichment_wt_percent": 2.11},
                {"variant_id": "region2", "enrichment_wt_percent": 2.619},
            ],
        },
        status="valid",
    ))
    state.add_patch(PlanPatchEnvelope(
        patch_id="universes",
        patch_type="universes",
        content={
            "patch_type": "universes",
            "universes": [
                {
                    "universe_id": "u_region2",
                    "kind": "fuel_pin",
                    "metadata": {"fuel_variant_id": "region2"},
                    "cells": [
                        {
                            "id": "fuel_inner",
                            "role": "fuel",
                            "material_id": "mat_region1",
                            "region_kind": "cylinder",
                            "r_min_cm": 0.0,
                            "r_max_cm": 0.4,
                        },
                        {
                            "id": "bg",
                            "role": "background",
                            "material_id": "coolant",
                            "region_kind": "background",
                        },
                    ],
                }
            ],
        },
        status="valid",
    ))
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True},
        material_universe_review_mode="controlled",
    )
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes"])
    facts_stage = state.plan_loop_stages["plan_gate_facts"]
    transition_stage(facts_stage, PlanStageStatus.PROPOSING)
    transition_stage(facts_stage, PlanStageStatus.VALIDATING)
    transition_stage(facts_stage, PlanStageStatus.REVIEWING)
    transition_stage(facts_stage, PlanStageStatus.ACCEPTED)

    fake = FakePatchLLM([
        json.dumps({
            "patch_type": "materials",
            "materials": [
                {
                    "material_id": "mat_region1",
                    "name": "Region 1 Fuel",
                    "role": "fuel",
                    "density_g_cm3": 10.0,
                    "source_variant_id": "region1",
                },
                {
                    "material_id": "mat_region2",
                    "name": "Region 2 Fuel",
                    "role": "fuel",
                    "density_g_cm3": 10.0,
                    "source_variant_id": "region2",
                },
                {
                    "material_id": "coolant",
                    "name": "coolant",
                    "role": "coolant",
                    "density_g_cm3": 1.0,
                },
            ],
        })
    ])
    reviewer_called = {"value": False}

    def reviewer(_prompt):
        reviewer_called["value"] = True
        raise AssertionError("MU reviewer must be skipped for deterministic blockers")

    result = run_incremental_planning(
        requirement="source",
        state=state,
        llm_client=fake,
        max_patch_attempts=1,
        task_order=["materials"],
        plan_loop_policy=policy,
        plan_reviewer_client=reviewer,
        plan_loop_output_dir=tmp_path,
        universes_generation_mode="off",
    )

    assert not result.ok
    assert result.summary["failed_patch_type"] == "universes"
    assert result.summary["issue_codes"] == [
        "planning.material_universe.contract_preflight_failed"
    ]
    assert reviewer_called["value"] is False
    assert result.state.plan_loop_stages[
        "plan_gate_material_universe"
    ].status is PlanStageStatus.BLOCKED
    retry_files = sorted(
        (tmp_path / "incremental" / "plan_closed_loop").glob(
            "material_universe_retry_request_*.json"
        )
    )
    assert retry_files
    retry_payload = json.loads(retry_files[0].read_text())
    assert retry_payload["owner_patch_types"] == ["universes"]


@pytest.mark.parametrize(
    ("blocked_before_resume", "expected_issue_code"),
    [
        (False, "planning.material_universe.contract_preflight_failed"),
        (True, "planning.material_universe_gate_not_accepted"),
    ],
)
def test_final_barrier_blocks_assembly_when_mu_preflight_fails(
    monkeypatch,
    tmp_path,
    blocked_before_resume,
    expected_issue_code,
) -> None:
    monkeypatch.setattr(executor, "default_patch_task_order", lambda _: [])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda _: [])
    monkeypatch.setattr(
        executor,
        "assemble_state_if_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("assembly must not run before MU gate accepts")
        ),
    )

    state = PlanBuildState(state_id="mu-final-barrier", requirement_text="source")
    state.add_patch(PlanPatchEnvelope(
        patch_id="facts",
        patch_type="facts",
        content={
            "patch_type": "facts",
            "model_scope": "single_assembly",
            "fuel_variant_requirements": [
                {"variant_id": "region1", "enrichment_wt_percent": 2.11},
                {"variant_id": "region2", "enrichment_wt_percent": 2.619},
            ],
        },
        status="valid",
    ))
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials",
        patch_type="materials",
        content={
            "patch_type": "materials",
            "materials": [
                {
                    "material_id": "mat_region1",
                    "name": "Region 1 Fuel",
                    "role": "fuel",
                    "density_g_cm3": 10.0,
                    "source_variant_id": "region1",
                },
                {
                    "material_id": "mat_region2",
                    "name": "Region 2 Fuel",
                    "role": "fuel",
                    "density_g_cm3": 10.0,
                    "source_variant_id": "region2",
                },
                {
                    "material_id": "coolant",
                    "name": "coolant",
                    "role": "coolant",
                    "density_g_cm3": 1.0,
                },
            ],
        },
        status="valid",
    ))
    state.add_patch(PlanPatchEnvelope(
        patch_id="universes",
        patch_type="universes",
        content={
            "patch_type": "universes",
            "universes": [
                {
                    "universe_id": "u_region2",
                    "kind": "fuel_pin",
                    "metadata": {"fuel_variant_id": "region2"},
                    "cells": [
                        {
                            "id": "fuel_inner",
                            "role": "fuel",
                            "material_id": "mat_region1",
                            "region_kind": "cylinder",
                            "r_min_cm": 0.0,
                            "r_max_cm": 0.4,
                        },
                        {
                            "id": "bg",
                            "role": "background",
                            "material_id": "coolant",
                            "region_kind": "background",
                        },
                    ],
                }
            ],
        },
        status="valid",
    ))
    policy = PlanClosedLoopPolicy(
        mode="controlled",
        gate_enabled={PlanGateId.FACTS: True, PlanGateId.MATERIAL_UNIVERSE: True},
        material_universe_review_mode="controlled",
    )
    initialize_plan_loop_state(state, policy, ["facts", "materials", "universes"])
    facts_stage = state.plan_loop_stages["plan_gate_facts"]
    transition_stage(facts_stage, PlanStageStatus.PROPOSING)
    transition_stage(facts_stage, PlanStageStatus.VALIDATING)
    transition_stage(facts_stage, PlanStageStatus.REVIEWING)
    transition_stage(facts_stage, PlanStageStatus.ACCEPTED)
    if blocked_before_resume:
        transition_stage(
            state.plan_loop_stages["plan_gate_material_universe"],
            PlanStageStatus.BLOCKED,
        )

    reviewer_called = {"value": False}

    def reviewer(_prompt):
        reviewer_called["value"] = True
        raise AssertionError("MU reviewer must be skipped for deterministic blockers")

    result = run_incremental_planning(
        requirement="source",
        state=state,
        llm_client=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("LLM must not run when all required patches are valid")
        ),
        plan_loop_policy=policy,
        plan_reviewer_client=reviewer,
        plan_loop_output_dir=tmp_path,
        universes_generation_mode="off",
    )

    assert not result.ok
    assert result.summary["failed_patch_type"] == "universes"
    assert result.summary["issue_codes"] == [expected_issue_code]
    assert result.plan_loop_outcome["active_gate_id"] == "material_universe"
    assert reviewer_called["value"] is False
    assert result.state.plan_loop_stages[
        "plan_gate_material_universe"
    ].status is PlanStageStatus.BLOCKED
