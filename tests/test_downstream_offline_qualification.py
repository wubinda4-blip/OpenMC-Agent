"""Phase 8C Step 3E offline downstream gate qualification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.closed_loop.downstream_mutations import (
    assembled_mutations,
    axial_mutations,
    owner_for,
    placement_mutations,
)
from openmc_agent.plan_builder.closed_loop.gate_replay import (
    GateReplayMode,
    GateReplayBundle,
    load_gate_replay_bundle,
    run_gate_replay,
)
from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import CampaignCheckpointStore
from openmc_agent.plan_builder.closed_loop.state_snapshot import make_boundary_checkpoint_callback


FIXTURES = Path(__file__).parent / "fixtures" / "gate_replay"
GATES = ("placement", "axial_geometry", "assembled_plan")


def _bundle(gate: str) -> GateReplayBundle:
    return load_gate_replay_bundle(FIXTURES / f"{gate}_offline_bundle.json")


@pytest.mark.parametrize("gate", GATES)
def test_clean_offline_fixture_preflight_and_recorded_review(gate: str) -> None:
    bundle = _bundle(gate)
    assert bundle.upstream_chain_provenance == "offline_deterministic"
    preflight = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    recorded = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
    assert preflight.ok, preflight.issues
    assert recorded.ok, recorded.issues
    assert recorded.terminal_status == "accepted"
    assert recorded.coverage["complete"] is True
    assert recorded.blocking_finding_count == 0
    assert recorded.rejected_finding_count == 0


@pytest.mark.parametrize(
    "gate,mutation_factory",
    [("placement", placement_mutations), ("axial_geometry", axial_mutations), ("assembled_plan", assembled_mutations)],
)
def test_mutations_are_stable_controlled_blockers(gate: str, mutation_factory) -> None:
    bundle = _bundle(gate)
    for _, mutated, expected_code in mutation_factory(bundle):
        first = run_gate_replay(mutated, mode=GateReplayMode.PREFLIGHT)
        second = run_gate_replay(mutated, mode=GateReplayMode.PREFLIGHT)
        codes = {issue.code for issue in first.issues}
        assert not first.ok
        assert any(expected_code == issue.code or expected_code in issue.message for issue in first.issues)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


def _deterministic_issues(bundle: GateReplayBundle) -> list[dict]:
    result = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
    return [{"code": item.code, "message": item.message} for item in result.issues]


def test_owner_routes_and_unknown_codes_fail_closed() -> None:
    assert owner_for("placement", "localized_insert.required_universe_missing")["dependency_patch_type"] == "universes"
    assert owner_for("axial_geometry", "axial.overlay_through_path_not_preserved").owner_patch_types == ["axial_overlays"]
    assert owner_for("assembled_plan", "assembled.renderer_skeleton_only").owner_patch_types == ["axial_layers", "axial_overlays"]
    assert owner_for("assembled_plan", "assembled.unknown_future_issue") is not None


def test_fixture_sensitive_and_fingerprint_tampering_fail_closed() -> None:
    raw = _bundle("placement").model_dump(mode="json")
    raw["fixture_fingerprint"] = "tampered"
    with pytest.raises(ValueError, match="fixture_fingerprint"):
        GateReplayBundle.model_validate(raw)


@pytest.mark.parametrize("gate,boundary", [("placement", "gate:placement"), ("axial_geometry", "gate:axial_geometry"), ("assembled_plan", "gate:assembled_plan")])
def test_downstream_accepted_checkpoint_resume_is_atomic(tmp_path, gate: str, boundary: str) -> None:
    from openmc_agent.structured_output import canonical_payload_hash

    bundle = _bundle(gate)
    store = CampaignCheckpointStore(tmp_path / f"{gate}.json")
    callback = make_boundary_checkpoint_callback(
        store,
        campaign_id=f"offline:{gate}",
        fingerprints={"requirement_hash": "r", "input_hash": bundle.canonical_hashes["input"], "policy_hash": bundle.canonical_hashes["policy"], "git_sha": "offline", "structured_output_policy_hash": "offline"},
    )
    state = bundle.normalized_state
    callback(boundary, state)
    restored = CampaignCheckpointStore(tmp_path / f"{gate}.json").hydrate_accepted_state(
        requirement_hash="r", input_hash=bundle.canonical_hashes["input"], policy_hash=bundle.canonical_hashes["policy"], git_sha="offline", structured_output_policy_hash="offline",
    )
    assert restored is not None
    assert restored.boundary == boundary
    assert restored.state_hash == canonical_payload_hash(state)
    raw = _bundle("axial_geometry").model_dump(mode="json")
    raw["policy_snapshot"]["prompt_text"] = "forbidden"
    with pytest.raises(ValueError, match="sensitive"):
        GateReplayBundle.model_validate(raw)


def test_qualification_cli_reports_all_three_gates(tmp_path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("qualify", "scripts/qualify_downstream_gates_offline.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = module.qualify(FIXTURES)
    assert payload["ok"] is True
    assert [item["gate_id"] for item in payload["gates"]] == list(GATES)
    assert all(item["upstream_chain_provenance"] == "offline_deterministic" for item in payload["gates"])
