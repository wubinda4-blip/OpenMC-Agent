"""Phase 8C Step 3G: downstream gate blocking→recovery cycle tests.

Two test patterns:

* Placement: full ``execute_plan_retry_loop`` cycle — blocking finding →
  retry request → candidate production → atomic commit → gate invalidation →
  gate replay → reclassification (resolved / no_progress / cycle).

* Axial / Assembled: re-replay recovery — mutate state to introduce a
  preflight blocker, verify replay blocked, then fix the state and verify
  the second replay is accepted with zero repeat calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openmc_agent.plan_builder.closed_loop.gate_replay import (
    GateReplayBundle,
    GateReplayMode,
    run_gate_replay,
)
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.retry_controller import (
    execute_plan_retry_loop,
    normalize_retry_request,
)
from openmc_agent.plan_builder.closed_loop.retry_models import (
    RetryExecutionStatus,
    RetryTriggerOrigin,
)
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope

FIXTURES = Path(__file__).parent / "fixtures" / "gate_replay"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _placement_state() -> PlanBuildState:
    """A placement-ready state with a single absorber insert requirement."""
    facts = {
        "patch_type": "facts", "model_scope": "single_assembly",
        "localized_insert_requirements": [{
            "requirement_id": "absorber", "insert_kind": "absorber_insert",
            "assembly_type_ids": [], "expected_coordinate_count_per_assembly": 1,
            "host_kind": "guide_tube", "required_profile_id": "p1",
            "required_segment_roles": ["absorber"], "expected_insert_universe_ids": ["abs"],
            "anchor_z_cm": 1.0, "control_state_id": "inserted",
        }],
    }
    universes = {"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c_fuel", "role": "fuel", "material_id": "fuel"}]},
        {"universe_id": "abs", "kind": "custom", "cells": [{"id": "c_abs", "role": "absorber", "material_id": "fuel"}]},
    ]}
    profiles = {"patch_type": "localized_insert_profiles", "profiles": [
        {"profile_id": "p1", "anchor_kind": "bottom", "anchor_z_cm": 1.0, "segments": [
            {"segment_id": "s", "relative_z_min_cm": 0, "relative_z_max_cm": 1, "universe_id": "abs", "role": "absorber"},
        ]},
    ]}
    pin = {"patch_type": "pin_map", "lattice_size": [3, 3], "default_universe_id": "fuel",
           "guide_tube_coords": [[1, 1]], "instrument_tube_coords": [],
           "localized_insert_intents": [{"insert_id": "i", "insert_kind": "absorber_insert",
                                          "insert_universe_id": "abs", "coordinates": [[1, 1]],
                                          "axial_profile_id": "p1", "anchor_z_cm": 1.0, "control_state_id": "inserted"}]}
    state = PlanBuildState(state_id="recovery_test", requirement_text="reactor-neutral placement")
    for patch in (facts, universes, profiles, pin):
        state.add_patch(PlanPatchEnvelope(patch_id=patch["patch_type"], patch_type=patch["patch_type"], content=patch, status="valid"))
    return state


# ---------------------------------------------------------------------------
# Part 1: Placement retry_controller loop (4 tests)
# ---------------------------------------------------------------------------


def test_placement_blocking_to_resolved_in_single_round() -> None:
    """A blocking placement finding routes to universes owner; the producer
    adds the missing universe and the gate_replayer confirms resolution."""
    state = _placement_state()
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials", patch_type="materials",
        content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]},
        status="valid",
    ))
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"],
         "dependency_patch_type": "universes", "required_ids": ["abs2"], "reason": "missing universe"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None

    def producer(req, plan, clone):
        env = next(e for e in clone.patches.values() if e.patch_type == "universes" and e.status == "valid")
        content = json.loads(json.dumps(env.content))
        content["universes"].append({"universe_id": "abs2", "kind": "custom", "cells": [{"id": "c_abs2", "role": "absorber", "material_id": "fuel"}]})
        return {"universes": content}

    def validator(req, plan, clone):
        env = next((e for e in clone.patches.values() if e.patch_type == "universes" and e.status == "valid"), None)
        ids = {u.get("universe_id") for u in (env.content.get("universes", []) if env else [])}
        return [] if "abs2" in ids else [{"code": "retry.required_universe_ids_missing", "severity": "error"}]

    call_count = {"n": 0}

    def gate_replayer(state_arg, plan, gates_invalid):
        call_count["n"] += 1
        return [], []

    policy = PlanClosedLoopPolicy(mode="controlled")
    outcome = execute_plan_retry_loop(
        state=state, policy=policy,
        candidate_producer=producer, candidate_validator=validator,
        gate_replayer=gate_replayer,
    )
    assert outcome.status is RetryExecutionStatus.RESOLVED
    assert call_count["n"] >= 1
    assert request.request_id not in state.plan_retry_pending_request_ids


def test_placement_no_progress_when_candidate_never_fixes_issue() -> None:
    """The producer always returns the same missing-universe candidate;
    duplicate detection triggers NO_PROGRESS."""
    state = _placement_state()
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials", patch_type="materials",
        content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]},
        status="valid",
    ))
    request = normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"],
         "dependency_patch_type": "universes", "required_ids": ["abs2"], "reason": "missing universe"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    assert request is not None

    def same_producer(req, plan, clone):
        env = next(e for e in clone.patches.values() if e.patch_type == "universes" and e.status == "valid")
        return {"universes": json.loads(json.dumps(env.content))}

    def reject(req, plan, clone):
        return [{"code": "retry.required_universe_ids_missing", "severity": "error"}]

    policy = PlanClosedLoopPolicy(mode="controlled", max_attempts_per_retry_request=3)
    outcome = execute_plan_retry_loop(
        state=state, policy=policy,
        candidate_producer=same_producer, candidate_validator=reject,
    )
    assert outcome.status is RetryExecutionStatus.NO_PROGRESS


def test_placement_gate_replayer_callback_reports_remaining_issues() -> None:
    """gate_replayer returns remaining issues after owner commit; the first
    round reclassifies as next_request_required (RESUMED).  The request
    becomes stale on re-selection because the owner patch hash changed."""
    state = _placement_state()
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials", patch_type="materials",
        content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]},
        status="valid",
    ))
    state.validation_issues = [{"code": "localized_insert.required_universe_missing", "severity": "error"}]
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"],
         "dependency_patch_type": "universes", "required_ids": ["abs2"], "reason": "missing"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )

    def producer(req, plan, clone):
        env = next(e for e in clone.patches.values() if e.patch_type == "universes" and e.status == "valid")
        content = json.loads(json.dumps(env.content))
        content["universes"].append({"universe_id": "abs2", "kind": "custom", "cells": [{"id": "c_abs2", "role": "absorber", "material_id": "fuel"}]})
        return {"universes": content}

    def validator(req, plan, clone):
        return []

    def gate_replayer(state_arg, plan, gates_invalid):
        return [], [{"code": "different_placement.error", "severity": "error"}]

    policy = PlanClosedLoopPolicy(mode="controlled")
    outcome = execute_plan_retry_loop(
        state=state, policy=policy,
        candidate_producer=producer, candidate_validator=validator,
        gate_replayer=gate_replayer,
    )
    # The first round succeeds (owner commit + gate replay), producing a
    # RESUMED reclassification.  Re-selection fails because the universes
    # patch hash changed, making the request stale.
    assert outcome.status is RetryExecutionStatus.BLOCKED
    assert len(state.plan_retry_rounds) >= 1
    first_round = state.plan_retry_rounds[0]
    assert first_round.reclassification == "next_request_required"
    assert first_round.gates_replayed is not None


def test_placement_budget_exhaustion_stops_retry() -> None:
    """max_rounds=1 forces BUDGET_EXHAUSTED when the request cannot be resolved
    in a single round."""
    state = _placement_state()
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials", patch_type="materials",
        content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0, "role": "fuel"}]},
        status="valid",
    ))
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"],
         "dependency_patch_type": "universes", "required_ids": ["abs2"], "reason": "missing"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    policy = PlanClosedLoopPolicy(mode="controlled", max_retry_rounds=1)
    outcome = execute_plan_retry_loop(
        state=state, policy=policy, candidate_producer=None, max_rounds=1,
    )
    assert outcome.status in {RetryExecutionStatus.BUDGET_EXHAUSTED, RetryExecutionStatus.FAILED}


# ---------------------------------------------------------------------------
# Part 2: Axial / Assembled re-replay recovery (6 tests)
# ---------------------------------------------------------------------------


def _clean_review(gate: str) -> dict:
    return {
        "review_status": "complete", "findings": [],
        "reviewed_evidence_refs": [], "reviewed_contract_row_ids": [],
        "coverage_summary": {}, "metadata": {"fixture_mode": "offline_deterministic", "gate": gate},
    }


def _axial_bundle() -> GateReplayBundle:
    from tests._axial_geometry_fixtures import state_with_axial_patches
    return GateReplayBundle.create(
        gate_id="axial_geometry",
        state=state_with_axial_patches(),
        policy=PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled"),
        upstream_accepted={"facts": True, "material_universe": True, "placement": True},
        canonical_hashes={"input": "axial_input", "policy": "axial_policy"},
        recorded_reviews=[_clean_review("axial_geometry")],
        upstream_chain_provenance="offline_deterministic",
    )


def _assembled_bundle() -> GateReplayBundle:
    from tests._assembled_plan_fixtures import make_assembled_plan, state_with_assembled_plan
    plan = make_assembled_plan()
    model = plan.complex_model.model_copy(update={"materials": [plan.complex_model.materials[0]]})
    return GateReplayBundle.create(
        gate_id="assembled_plan",
        state=state_with_assembled_plan(plan=plan.model_copy(update={"complex_model": model})),
        policy=PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled"),
        upstream_accepted={"facts": True, "material_universe": True, "placement": True, "axial_geometry": True},
        canonical_hashes={"input": "assembled_input", "policy": "assembled_policy"},
        recorded_reviews=[_clean_review("assembled_plan")],
        upstream_chain_provenance="offline_deterministic",
    )


def _mutate_layer_overlap(bundle: GateReplayBundle) -> GateReplayBundle:
    """Introduce a zero-thickness layer that triggers a preflight blocker."""
    raw = bundle.model_dump(mode="json")
    for env in raw["normalized_state"]["patches"].values():
        if env.get("patch_type") == "axial_layers":
            env["content"]["layers"][1]["z_min_cm"] = 95.0
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    return GateReplayBundle.model_validate(raw)


def _fix_layer_overlap(bundle: GateReplayBundle) -> GateReplayBundle:
    """Restore the original layer interval."""
    raw = bundle.model_dump(mode="json")
    for env in raw["normalized_state"]["patches"].values():
        if env.get("patch_type") == "axial_layers":
            env["content"]["layers"][1]["z_min_cm"] = 10.0
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    return GateReplayBundle.model_validate(raw)


def test_axial_blocker_then_recovery_replay_accepted() -> None:
    clean = _axial_bundle()
    blocked = _mutate_layer_overlap(clean)
    first = run_gate_replay(blocked, mode=GateReplayMode.PREFLIGHT)
    assert not first.ok
    assert any("axial.layer" in issue.message for issue in first.issues)
    recovered = _fix_layer_overlap(blocked)
    second = run_gate_replay(recovered, mode=GateReplayMode.RECORDED_REVIEW)
    assert second.ok
    assert second.terminal_status == "accepted"
    assert second.blocking_finding_count == 0


def test_axial_recovery_does_not_reuse_blocked_output() -> None:
    blocked = _mutate_layer_overlap(_axial_bundle())
    first = run_gate_replay(blocked, mode=GateReplayMode.RECORDED_REVIEW)
    assert not first.ok
    assert first.recorded_review_replayed is False
    assert first.review_output is None
    assert any(
        issue.code == "gate_replay.deterministic_preflight"
        for issue in first.issues
    )
    recovered = _fix_layer_overlap(blocked)
    second = run_gate_replay(recovered, mode=GateReplayMode.RECORDED_REVIEW)
    assert second.ok
    assert second.review_output is not None
    assert second.recorded_review_replayed


def test_axial_rejected_finding_fail_closed_in_recorded_review() -> None:
    bundle = _axial_bundle()
    raw = bundle.model_dump(mode="json")
    raw["recorded_reviews"][0]["findings"] = [{
        "code": "axial.unknown_future_code", "severity": "error",
        "category": "representation_error", "message": "test",
        "evidence_refs": ["ZZZ999"], "contract_row_ids": [],
        "repairable_by_llm": False, "requires_human": False, "confidence": 0.5,
    }]
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    bundle = GateReplayBundle.model_validate(raw)
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert not result.ok
    assert result.rejected_finding_count > 0


def test_assembled_blocker_then_recovery_replay_accepted() -> None:
    clean = _assembled_bundle()
    raw = clean.model_dump(mode="json")
    raw["normalized_state"]["assembled_plan"]["complex_model"]["lattices"][0]["universe_pattern"][0][0] = "missing"
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    blocked = GateReplayBundle.model_validate(raw)
    first = run_gate_replay(blocked, mode=GateReplayMode.PREFLIGHT)
    assert not first.ok
    raw2 = blocked.model_dump(mode="json")
    raw2["normalized_state"]["assembled_plan"]["complex_model"]["lattices"][0]["universe_pattern"][0][0] = "u1"
    raw2["bundle_hash"] = ""
    raw2["fixture_fingerprint"] = ""
    recovered = GateReplayBundle.model_validate(raw2)
    second = run_gate_replay(recovered, mode=GateReplayMode.RECORDED_REVIEW)
    assert second.ok
    assert second.terminal_status == "accepted"


def test_assembled_recovery_replay_is_deterministic() -> None:
    recovered = _assembled_bundle()
    r1 = run_gate_replay(recovered, mode=GateReplayMode.RECORDED_REVIEW)
    r2 = run_gate_replay(recovered, mode=GateReplayMode.RECORDED_REVIEW)
    assert r1.ok and r2.ok
    assert r1.model_dump(mode="json") == r2.model_dump(mode="json")


def test_assembled_rejected_finding_fail_closed_in_recorded_review() -> None:
    bundle = _assembled_bundle()
    raw = bundle.model_dump(mode="json")
    raw["recorded_reviews"][0]["findings"] = [{
        "code": "assembled.unknown_future_code", "severity": "error",
        "category": "representation_error", "message": "test",
        "evidence_refs": ["ZZZ999"], "contract_row_ids": [],
        "repairable_by_llm": False, "requires_human": False, "confidence": 0.5,
    }]
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    bundle = GateReplayBundle.model_validate(raw)
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert not result.ok
    assert result.rejected_finding_count > 0
