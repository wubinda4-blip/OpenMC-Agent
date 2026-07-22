"""Phase 8C Step 3B GateReplayBundle + accepted-boundary checkpoint tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import (
    ACCEPTED_BOUNDARIES,
    BOUNDARY_GATE_ASSEMBLED_PLAN,
    BOUNDARY_GATE_AXIAL_GEOMETRY,
    BOUNDARY_GATE_FACTS,
    BOUNDARY_GATE_MATERIAL_UNIVERSE,
    BOUNDARY_GATE_PLACEMENT,
    BOUNDARY_PATCH_MATERIALS,
    BOUNDARY_PATCH_UNIVERSES,
    CampaignCheckpointStore,
    CampaignStateSnapshot,
    FactsActionCheckpoint,
    GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION,
)
from openmc_agent.plan_builder.closed_loop.gate_replay import (
    DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS,
    GATE_REPLAY_BUNDLE_SCHEMA_VERSION,
    GateReplayBundle,
    GateReplayMode,
    GateReplayResult,
    load_gate_replay_bundle,
    run_gate_replay,
)
from openmc_agent.plan_builder.closed_loop.material_universe_finding_classification import (
    classify_material_universe_finding,
)
from openmc_agent.plan_builder.closed_loop.material_universe_issue_policy import (
    registered_material_universe_issue_codes,
)
from openmc_agent.plan_builder.closed_loop.state_snapshot import (
    make_boundary_checkpoint_callback,
    make_facts_action_callback,
    sanitize_plan_build_state,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gate_replay"


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _facts_bundle() -> GateReplayBundle:
    return GateReplayBundle.model_validate(_load_fixture("facts_canary_bundle.json"))


def _mu_bundle() -> GateReplayBundle:
    return GateReplayBundle.model_validate(
        _load_fixture("material_universe_canary_bundle.json")
    )


def _mu_v13_findings_bundle() -> GateReplayBundle:
    return GateReplayBundle.model_validate(
        _load_fixture("material_universe_v13_findings_bundle.json")
    )


# ---------------------------------------------------------------------------
# Fixture validity (baseline)
# ---------------------------------------------------------------------------


def test_facts_fixture_loads_and_preflights_clean() -> None:
    bundle = _facts_bundle()
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert result.ok, [i.message for i in result.issues]
    assert result.upstream_validated
    assert result.hashes_validated
    assert result.state_complete
    assert result.sensitive_fields_rejected
    assert result.live_review_invoked is False


def test_mu_fixture_loads_and_preflights_clean() -> None:
    bundle = _mu_bundle()
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert result.ok, [i.message for i in result.issues]


def test_mu_v13_finding_fixture_preflights_clean() -> None:
    bundle = _mu_v13_findings_bundle()
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert result.ok, [i.message for i in result.issues]


# ---------------------------------------------------------------------------
# 1. Corruption / version drift
# ---------------------------------------------------------------------------


def test_bundle_wrong_schema_version_rejected() -> None:
    raw = _load_fixture("facts_canary_bundle.json")
    raw["bundle_schema_version"] = "0.0-bogus"
    with pytest.raises(Exception, match="unsupported bundle schema version"):
        GateReplayBundle.model_validate(raw)


def test_snapshot_schema_version_mismatch_fails_closed(tmp_path) -> None:
    # The bundle accepts a snapshot version field; the *store* hydration is
    # what enforces the exact snapshot schema version on resume.
    store = CampaignCheckpointStore(tmp_path / "test_ckpt_v.json")
    snap = CampaignStateSnapshot(
        campaign_id="c",
        boundary=BOUNDARY_GATE_FACTS,
        schema_version="0.0-wrong",
        sequence=1,
        state_hash="x",
        plan_build_state={"state_id": "s"},
        requirement_hash="r",
        input_hash="i",
        policy_hash="p",
        git_sha="g",
        structured_output_policy_hash="s",
    )
    store.accept_state_snapshot(snap)
    with pytest.raises(ValueError, match="schema version"):
        store.hydrate_accepted_state(
            requirement_hash="r", input_hash="i", policy_hash="p", git_sha="g",
            structured_output_policy_hash="s",
        )


# ---------------------------------------------------------------------------
# 2. Hash drift
# ---------------------------------------------------------------------------


def test_bundle_hash_drift_detected() -> None:
    raw = _load_fixture("facts_canary_bundle.json")
    raw["bundle_hash"] = "deadbeef" * 8
    with pytest.raises(Exception, match="bundle_hash does not match"):
        GateReplayBundle.model_validate(raw)


def test_preflight_detects_drift_when_hash_inconsistent() -> None:
    # Build a bundle, then tamper with the normalized_state after computing
    # the hash — the engine recomputes and flags the drift.
    bundle = _facts_bundle()
    bundle = bundle.model_copy(
        update={"normalized_state": {"state_id": "tampered"}},
    )
    # Recompute the "old" hash from the original content so it is now stale.
    original = _facts_bundle()
    bundle = bundle.model_copy(update={"bundle_hash": original.compute_bundle_hash()})
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert not result.ok
    assert any(i.code == "gate_replay.bundle_hash_drift" for i in result.issues)


# ---------------------------------------------------------------------------
# 3. Missing / unaccepted upstream
# ---------------------------------------------------------------------------


def test_mu_replay_requires_facts_upstream_accepted() -> None:
    bundle = _mu_bundle().model_copy(
        update={"upstream_accepted": {"facts": False}}
    )
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert not result.ok
    assert any(i.code == "gate_replay.upstream_not_accepted" for i in result.issues)


def test_mu_replay_missing_upstream_key_fails_closed() -> None:
    bundle = _mu_bundle().model_copy(update={"upstream_accepted": {}})
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert not result.ok
    assert any("upstream" in i.code for i in result.issues)


# ---------------------------------------------------------------------------
# 4. Sensitive fields
# ---------------------------------------------------------------------------


def test_bundle_with_prompt_text_rejected() -> None:
    raw = _load_fixture("facts_canary_bundle.json")
    raw["normalized_state"]["prompt_text"] = "SECRET PROMPT"
    with pytest.raises(Exception, match="sensitive"):
        GateReplayBundle.model_validate(raw)


def test_bundle_with_nested_raw_response_rejected() -> None:
    raw = _load_fixture("facts_canary_bundle.json")
    raw["recorded_reviews"][0]["raw_response"] = "provider raw output"
    with pytest.raises(Exception, match="sensitive"):
        GateReplayBundle.model_validate(raw)


def test_sanitizer_strips_sensitive_keys() -> None:
    dirty = {
        "state_id": "s",
        "prompt_text": "secret",
        "patches": {
            "p1": {
                "patch_id": "p1",
                "raw_text": "raw",
                "content": {"ok": True},
            }
        },
    }
    cleaned = sanitize_plan_build_state(dirty)
    assert "prompt_text" not in cleaned
    assert "raw_text" not in cleaned["patches"]["p1"]
    assert cleaned["patches"]["p1"]["content"]["ok"] is True


# ---------------------------------------------------------------------------
# 5. Deterministic MU old-error mutation vs current zero errors
# ---------------------------------------------------------------------------


def test_mu_old_error_mutation_now_clean_in_recorded_review() -> None:
    """A bundle whose earlier run recorded blocking errors, but whose current
    normalized state has zero cross-patch mismatches, replays cleanly in
    recorded-review mode (the recorded review is the *current* normalized one).
    """
    bundle = _mu_bundle()
    # The current recorded review is approve/ok (zero errors).
    assert bundle.recorded_reviews[0]["findings"] == []
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert result.ok
    assert result.recorded_review_replayed


def test_mu_v13_recorded_findings_close_to_nonblocking_diagnostics() -> None:
    """The sanitized v13 recorded-review fixture keeps the old MU finding
    target but current normalization closes stale/deterministic blockers.
    """
    bundle = _mu_v13_findings_bundle()
    recorded_codes = {
        finding["code"]
        for review in bundle.recorded_reviews
        for finding in review.get("findings", [])
    }
    assert recorded_codes == {
        "material_universe.enrichment_contract_mismatch",
        "material_universe.background_missing",
        "material_universe.contract_material_id_mismatch",
        "material_universe.contract_material_role_mismatch",
        "material_universe.material_role_conflict",
        "material_universe.material_count_role_count_mismatch",
    }

    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)

    assert result.ok, [i.message for i in result.issues]
    assert result.recorded_review_replayed
    assert result.review_output is not None
    diagnostics = result.review_output["finding_diagnostics"]
    assert diagnostics["coverage_complete"] is True
    assert diagnostics["blocking_finding_count"] == 0
    assert diagnostics["rejected_summary"] == {
        "material_universe_review.repeated_deterministic_issue": 2,
        "material_universe_review.stale_finding_closed": 1,
        "material_universe_review.over_specific_role_contract": 1,
    }
    remaining = {
        item["code"]: item["classification"]
        for item in diagnostics["classification_summary"]
    }
    assert remaining == {
        "material_universe.material_role_conflict": "reviewer_false_positive",
        "material_universe.material_count_role_count_mismatch": "binding_metadata_gap",
    }


def test_mu_recorded_review_rejects_scope_mismatch() -> None:
    bundle = _mu_v13_findings_bundle()
    reviews = [dict(item) for item in bundle.recorded_reviews]
    # Put a universes-only row into the materials scope.  Replay must reject
    # the finding instead of misrouting it to Materials.
    reviews[0] = dict(reviews[0])
    reviews[0]["findings"] = [
        {
            "code": "material_universe.material_role_conflict",
            "severity": "warning",
            "category": "cross_patch_mismatch",
            "message": "scope mismatch mutation",
            "evidence_refs": ["U024"],
            "contract_row_ids": ["rums:u_fuel_region_1_2p11"],
            "affected_json_paths": ["records[0]"],
            "repairable_by_llm": False,
            "requires_human": False,
            "confidence": 0.9,
            "expected_semantics": "materials scope should not own rums rows",
            "current_semantics": "mutated fixture",
            "metadata": {},
        }
    ]
    bundle = bundle.model_copy(update={"recorded_reviews": reviews})
    bundle = bundle.model_copy(update={"bundle_hash": bundle.compute_bundle_hash()})

    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)

    assert result.ok
    assert result.review_output is not None
    assert any(
        item["code"] == "material_universe_review.scope_contract_mismatch"
        for item in result.review_output["rejected"]
    )


def test_mu_finding_classifier_covers_registered_codes_and_unknown_fails_closed() -> None:
    for code in registered_material_universe_issue_codes():
        classification = classify_material_universe_finding(code)
        assert classification.classification != "unknown_code", code
        assert not classification.fail_closed, code
    composition = classify_material_universe_finding(
        "material_universe.invalid_composition_sum_for_basis"
    )
    assert composition.classification == "deterministic_preflight_gap"

    unknown = classify_material_universe_finding("material_universe.future_new_code")
    assert unknown.classification == "unknown_code"
    assert unknown.fail_closed


def test_mu_deterministic_error_in_state_blocks_preflight_state_check() -> None:
    """If the normalized state itself is incomplete (mutation removed it),
    preflight fails closed rather than masking an old error."""
    bundle = _mu_bundle().model_copy(update={"normalized_state": {}})
    # Recompute hash since content changed; keep it consistent so we test
    # the *state completeness* check, not the hash check.
    bundle = bundle.model_copy(update={"bundle_hash": bundle.compute_bundle_hash()})
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert not result.ok
    assert any("state" in i.code for i in result.issues)


# ---------------------------------------------------------------------------
# 6. Recorded malformed review
# ---------------------------------------------------------------------------


def test_recorded_review_malformed_object_rejected() -> None:
    bundle = _facts_bundle().model_copy(
        update={"recorded_reviews": ["not-an-object"]}
    )
    bundle = bundle.model_copy(update={"bundle_hash": bundle.compute_bundle_hash()})
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert not result.ok
    assert any(i.code == "gate_replay.malformed_recorded_review" for i in result.issues)


def test_recorded_review_missing_decision_rejected() -> None:
    bundle = _facts_bundle().model_copy(
        update={"recorded_reviews": [{"random_key": "no-decision"}]}
    )
    bundle = bundle.model_copy(update={"bundle_hash": bundle.compute_bundle_hash()})
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert not result.ok
    assert any(i.code == "gate_replay.malformed_recorded_review" for i in result.issues)


def test_recorded_review_empty_rejected() -> None:
    bundle = _facts_bundle().model_copy(update={"recorded_reviews": []})
    bundle = bundle.model_copy(update={"bundle_hash": bundle.compute_bundle_hash()})
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert not result.ok
    assert any(i.code == "gate_replay.no_recorded_reviews" for i in result.issues)


# ---------------------------------------------------------------------------
# 7. Live reviewer isolation with fake client
# ---------------------------------------------------------------------------


def test_live_review_with_fake_client_isolated() -> None:
    calls: list[str] = []

    def fake_reviewer(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "review_status": "complete",
            "findings": [],
            "reviewed_evidence_hashes": [],
            "coverage_summary": {},
        })

    bundle = _facts_bundle()
    result = run_gate_replay(
        bundle, mode=GateReplayMode.LIVE_REVIEW, reviewer_client=fake_reviewer
    )
    assert result.ok
    assert result.live_review_invoked
    assert len(calls) >= 1
    assert all("state_id" not in call for call in calls)


def test_live_review_without_client_fails_closed() -> None:
    bundle = _facts_bundle()
    result = run_gate_replay(
        bundle, mode=GateReplayMode.LIVE_REVIEW, reviewer_client=None
    )
    assert not result.ok
    assert any(i.code == "gate_replay.live_reviewer_missing" for i in result.issues)


def test_live_review_output_sanitized_no_raw_response() -> None:
    def fake_reviewer(prompt: str) -> str:
        return json.dumps({
            "review_status": "complete",
            "findings": [],
            "reviewed_evidence_hashes": [],
            "coverage_summary": {},
        })

    bundle = _facts_bundle()
    result = run_gate_replay(
        bundle, mode=GateReplayMode.LIVE_REVIEW, reviewer_client=fake_reviewer
    )
    sanitized = result.to_sanitized_dict()
    out = json.dumps(sanitized)
    assert "api_key" not in out
    assert "raw_outputs" not in out
    assert sanitized["review_output"] is not None
    assert "call_diagnostics" in sanitized["review_output"]


def test_live_reviewer_raising_caught() -> None:
    def boom(prompt: str) -> str:
        raise RuntimeError("boom")

    bundle = _facts_bundle()
    result = run_gate_replay(
        bundle, mode=GateReplayMode.LIVE_REVIEW, reviewer_client=boom
    )
    assert not result.ok
    assert any(i.code in {"gate_replay.live_reviewer_error", "gate_replay.live_review_failed"} for i in result.issues)


def test_default_live_timeout_documented() -> None:
    assert DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS == 1800


def test_downstream_bundle_requires_policy_snapshot_hash() -> None:
    raw = _facts_bundle().model_dump(mode="json")
    raw.update({
        "gate_id": "placement",
        "upstream_accepted": {"facts": True},
        "policy_snapshot": {"mode": "controlled", "placement_review_mode": "controlled"},
        "upstream_chain_provenance": "offline_deterministic",
        "canonical_hashes": {"input": "input", "policy": "policy", "policy_snapshot": "wrong"},
        "bundle_hash": "",
    })
    bundle = GateReplayBundle.model_validate(raw)
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    assert not result.ok
    assert any(item.code == "gate_replay.policy_snapshot_hash_drift" for item in result.issues)


def test_downstream_bundle_create_replays_through_production_review() -> None:
    from tests._axial_geometry_fixtures import state_with_axial_patches
    from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy

    bundle = GateReplayBundle.create(
        gate_id="axial_geometry",
        state=state_with_axial_patches(),
        policy=PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled"),
        upstream_accepted={"facts": True, "material_universe": True, "placement": True},
        canonical_hashes={"input": "input", "policy": "policy"},
        recorded_reviews=[{"review_status": "complete", "findings": []}],
    )
    result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert result.recorded_review_replayed
    assert result.terminal_status in {"accepted", "blocked"}
    assert "prompt" not in json.dumps(bundle.model_dump(mode="json"), sort_keys=True).lower()


# ---------------------------------------------------------------------------
# 8. Callback timing / crash injection
# ---------------------------------------------------------------------------


def test_boundary_callback_store_error_propagates(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "ckpt.json")

    def crashing_callback(boundary: str, state) -> None:
        raise RuntimeError("simulated checkpoint crash")

    # The production executor wraps this in try/except; here we verify the
    # callback factory itself swallows exceptions.
    cb = make_boundary_checkpoint_callback(store, campaign_id="c")
    # A raw crash would propagate, so the executor's _persist_checkpoint
    # helper is the safety net. Verify a *failing store* path is swallowed
    # by the factory by making the store path unwritable.
    readonly = tmp_path / "ro"
    readonly.mkdir()
    bad_store = CampaignCheckpointStore(readonly / "ckpt.json")
    readonly.chmod(0o444)
    try:
        cb2 = make_boundary_checkpoint_callback(bad_store, campaign_id="c")
        with pytest.raises(PermissionError):
            cb2(BOUNDARY_GATE_FACTS, {"state_id": "s"})
    finally:
        readonly.chmod(0o755)


def test_facts_action_callback_records_status_billing(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "ckpt.json")
    cb = make_facts_action_callback(store)
    cb(
        action_id="facts:search:1",
        tool_name="search_source_index",
        arguments_hash="h",
        status="provider_timeout",
        billed_call_count=2,
        provider_deadline="30",
        unfinished=True,
    )
    restored = CampaignCheckpointStore(tmp_path / "ckpt.json")
    action = restored.facts_action("facts:search:1")
    assert action is not None
    assert action.status == "provider_timeout"
    assert action.billed_call_count == 2


def test_facts_action_callback_store_error_propagates(tmp_path) -> None:
    readonly = tmp_path / "ro"
    readonly.mkdir()
    store = CampaignCheckpointStore(readonly / "ckpt.json")
    readonly.chmod(0o444)
    cb = make_facts_action_callback(store)
    try:
        with pytest.raises(PermissionError):
            cb(action_id="x", tool_name="t", arguments_hash="h")
    finally:
        readonly.chmod(0o755)


# ---------------------------------------------------------------------------
# 9. Resume avoiding prior accepted work
# ---------------------------------------------------------------------------


def test_resume_hydrates_latest_valid_boundary(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "ckpt.json")
    cb = make_boundary_checkpoint_callback(
        store,
        campaign_id="c",
        fingerprints={
            "requirement_hash": "r",
            "input_hash": "i",
            "policy_hash": "p",
            "git_sha": "g",
            "structured_output_policy_hash": "s",
        },
    )
    # Simulate accepted boundaries in order.
    cb(BOUNDARY_PATCH_MATERIALS, {"state_id": "s1", "requirement_text": "r"})
    cb(BOUNDARY_PATCH_UNIVERSES, {"state_id": "s2", "requirement_text": "r"})
    cb(BOUNDARY_GATE_FACTS, {"state_id": "s3", "requirement_text": "r"})
    cb(BOUNDARY_GATE_MATERIAL_UNIVERSE, {"state_id": "s4", "requirement_text": "r"})
    hydrated = store.hydrate_accepted_state(
        requirement_hash="r",
        input_hash="i",
        policy_hash="p",
        git_sha="g",
        structured_output_policy_hash="s",
    )
    assert hydrated is not None
    assert hydrated.boundary == BOUNDARY_GATE_MATERIAL_UNIVERSE
    assert hydrated.plan_build_state["state_id"] == "s4"


def test_resume_fails_closed_on_fingerprint_drift(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "ckpt.json")
    cb = make_boundary_checkpoint_callback(
        store,
        campaign_id="c",
        fingerprints={
            "requirement_hash": "r",
            "input_hash": "i",
            "policy_hash": "p",
            "git_sha": "g",
            "structured_output_policy_hash": "s",
        },
    )
    cb(BOUNDARY_GATE_FACTS, {"state_id": "s", "requirement_text": "r"})
    # Drift: change requirement_hash.
    with pytest.raises(ValueError, match="fingerprint drift"):
        store.hydrate_accepted_state(
            requirement_hash="DIFFERENT", input_hash="i", policy_hash="p", git_sha="g",
            structured_output_policy_hash="s",
        )


def test_resume_does_not_reuse_earlier_boundary_when_later_drifts(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "ckpt.json")
    # Write a valid facts snapshot then a material_universe snapshot with a
    # different git_sha (drift).  Resume must NOT fall back to the facts
    # snapshot — it returns None (fail-closed) because the latest boundary
    # drifts and earlier boundaries must not mask a stale later state.
    from openmc_agent.structured_output import canonical_payload_hash
    good_state = {"state_id": "s1", "requirement_text": "r"}
    drifted_state = {"state_id": "s2", "requirement_text": "r"}
    good = CampaignStateSnapshot(
        campaign_id="c",
        boundary=BOUNDARY_GATE_FACTS,
        sequence=1,
        state_hash=canonical_payload_hash(good_state),
        plan_build_state=good_state,
        requirement_hash="r",
        input_hash="i",
        policy_hash="p",
        git_sha="g",
        structured_output_policy_hash="s",
    )
    drifted = CampaignStateSnapshot(
        campaign_id="c",
        boundary=BOUNDARY_GATE_MATERIAL_UNIVERSE,
        sequence=2,
        state_hash=canonical_payload_hash(drifted_state),
        plan_build_state=drifted_state,
        requirement_hash="r",
        input_hash="i",
        policy_hash="p",
        git_sha="DIFFERENT",
        structured_output_policy_hash="s",
    )
    store.accept_state_snapshot(good)
    store.accept_state_snapshot(drifted)
    with pytest.raises(ValueError, match="fingerprint drift"):
        store.hydrate_accepted_state(
            requirement_hash="r", input_hash="i", policy_hash="p", git_sha="g",
            structured_output_policy_hash="s",
        )


def test_accepted_boundaries_ordered() -> None:
    assert ACCEPTED_BOUNDARIES[:4] == (
        BOUNDARY_GATE_FACTS,
        BOUNDARY_PATCH_MATERIALS,
        BOUNDARY_PATCH_UNIVERSES,
        BOUNDARY_GATE_MATERIAL_UNIVERSE,
    )
    assert ACCEPTED_BOUNDARIES[4:] == (
        BOUNDARY_GATE_PLACEMENT,
        BOUNDARY_GATE_AXIAL_GEOMETRY,
        BOUNDARY_GATE_ASSEMBLED_PLAN,
    )


def test_schema_versions_distinct_and_stable() -> None:
    assert GATE_REPLAY_BUNDLE_SCHEMA_VERSION == "1.0"
    assert GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# CLI smoke (no LLM)
# ---------------------------------------------------------------------------


def test_cli_preflight_runs_on_facts_fixture(tmp_path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "replay_plan_gate",
        str(Path(__file__).resolve().parent.parent / "scripts" / "replay_plan_gate.py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out_path = tmp_path / "result.json"
    rc = module.main(
        [
            "--bundle",
            str(FIXTURES / "facts_canary_bundle.json"),
            "--mode",
            "preflight",
            "--out",
            str(out_path),
        ]
    )
    assert rc == 0
    data = json.loads(out_path.read_text())
    assert data["ok"] is True
    assert data["mode"] == "preflight"
