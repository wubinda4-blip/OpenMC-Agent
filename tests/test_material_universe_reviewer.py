"""Phase 4: Material-Universe reviewer normalization."""

from __future__ import annotations

import json

from openmc_agent.plan_builder.closed_loop.material_universe_evidence import build_material_universe_evidence_pack
from openmc_agent.plan_builder.closed_loop.material_universe_reviewer import run_material_universe_review
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state() -> PlanBuildState:
    state = PlanBuildState(state_id="mu-rev", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="facts", patch_type="facts", content={"patch_type": "facts", "model_scope": "single_assembly"}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "name": "f", "role": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4}]}]}, status="valid"))
    from openmc_agent.plan_builder.closed_loop.controller import initialize_gate_stage
    from openmc_agent.plan_builder.closed_loop.models import PlanGateId, PlanStageStatus
    stage = initialize_gate_stage(PlanGateId.MATERIAL_UNIVERSE, ["materials", "universes"])
    # The reviewer only runs in REVIEWING state; set it directly for the test.
    stage.status = PlanStageStatus.REVIEWING
    state.plan_loop_stages[stage.stage_id] = stage
    return state


def test_clean_json_review_passes_with_coverage() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="advisory")
    pack = build_material_universe_evidence_pack(state=state, policy=policy)
    expected_materials = {m.material_id for m in pack.binding_view.material_records}
    expected_universes = {u.universe_id for u in pack.binding_view.universe_records}
    expected_rows = {r.row_id for r in pack.contract_matrix.rows}
    evidence_refs = [item.ref_id for item in pack.evidence_items]

    def reviewer(prompt: str) -> str:
        return json.dumps({
            "review_status": "complete",
            "findings": [],
            "reviewed_contract_row_ids": list(expected_rows),
            "reviewed_evidence_refs": evidence_refs[:3],
            "coverage_summary": {
                "reviewed_material_ids": list(expected_materials),
                "reviewed_universe_ids": list(expected_universes),
                "reviewed_contract_row_ids": list(expected_rows),
                "reviewed_evidence_refs": evidence_refs[:3],
            },
        })

    result = run_material_universe_review(evidence_pack=pack, reviewer_client=reviewer, state=state, policy=policy)
    assert result.ok
    assert result.coverage_complete


def test_unknown_evidence_ref_rejected() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="advisory")
    pack = build_material_universe_evidence_pack(state=state, policy=policy)

    def reviewer(prompt: str) -> str:
        return json.dumps({
            "review_status": "complete",
            "findings": [{"code": "test.bad_ref", "severity": "error", "category": "cross_patch_mismatch", "message": "x", "evidence_refs": ["ZZZ999"], "confidence": 0.9}],
            "reviewed_contract_row_ids": [],
            "reviewed_evidence_refs": [],
            "coverage_summary": {},
        })

    result = run_material_universe_review(evidence_pack=pack, reviewer_client=reviewer, state=state, policy=policy)
    assert any(r["code"] == "material_universe_review.unknown_evidence_ref" for r in result.rejected)


def test_owner_action_fields_rejected() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="advisory")
    pack = build_material_universe_evidence_pack(state=state, policy=policy)
    evidence_ref = pack.evidence_items[0].ref_id if pack.evidence_items else "F001"

    def reviewer(prompt: str) -> str:
        return json.dumps({
            "review_status": "complete",
            "findings": [{"code": "test.owner", "severity": "warning", "category": "cross_patch_mismatch", "message": "x", "evidence_refs": [evidence_ref], "confidence": 0.9, "metadata": {"owner": "materials"}}],
            "reviewed_contract_row_ids": [],
            "reviewed_evidence_refs": [],
            "coverage_summary": {},
        })

    result = run_material_universe_review(evidence_pack=pack, reviewer_client=reviewer, state=state, policy=policy)
    assert any("owner_action_forbidden" in r["code"] for r in result.rejected)


def test_coverage_incomplete_marks_review_failed() -> None:
    state = _state()
    policy = PlanClosedLoopPolicy(mode="advisory")
    pack = build_material_universe_evidence_pack(state=state, policy=policy)

    def reviewer(prompt: str) -> str:
        return json.dumps({
            "review_status": "complete",
            "findings": [],
            "reviewed_contract_row_ids": [],
            "reviewed_evidence_refs": [],
            "coverage_summary": {"reviewed_material_ids": [], "reviewed_universe_ids": []},
        })

    result = run_material_universe_review(evidence_pack=pack, reviewer_client=reviewer, state=state, policy=policy)
    assert not result.coverage_complete
    assert result.failure_code == "material_universe_review.coverage_incomplete"
