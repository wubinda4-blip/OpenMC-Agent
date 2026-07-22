"""Tests for Phase 8C Step 3G live-review orchestration scripts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import (
    ACCEPTED_BOUNDARIES,
    BOUNDARY_GATE_ASSEMBLED_PLAN,
    BOUNDARY_GATE_AXIAL_GEOMETRY,
    BOUNDARY_GATE_PLACEMENT,
    CampaignCheckpointStore,
    CampaignStateSnapshot,
    checkpoint_fingerprint,
)
from openmc_agent.plan_builder.closed_loop.gate_replay import GateReplayBundle
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "gate_replay"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / f"{name}.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# extract_downstream_replay_bundles.py
# ---------------------------------------------------------------------------


def _make_synthetic_checkpoint(tmp_path: Path) -> Path:
    """Create a minimal campaign_checkpoint.json with downstream boundary snapshots."""
    store = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    state = PlanBuildState(state_id="synthetic", requirement_text="test")
    fingerprints = {
        "requirement_hash": "r", "input_hash": "i", "policy_hash": "p",
        "git_sha": "g", "structured_output_policy_hash": "s",
    }
    for sequence, boundary in enumerate(
        [BOUNDARY_GATE_PLACEMENT, BOUNDARY_GATE_AXIAL_GEOMETRY, BOUNDARY_GATE_ASSEMBLED_PLAN], start=1
    ):
        snap = CampaignStateSnapshot(
            campaign_id="synthetic", boundary=boundary, sequence=sequence,
            state_hash=checkpoint_fingerprint(state.model_dump(mode="json")),
            plan_build_state=state.model_dump(mode="json"),
            accepted_at=f"t{sequence}", **fingerprints,
        )
        store.accept_state_snapshot(snap)
    return tmp_path / "campaign_checkpoint.json"


def test_extract_bundles_from_synthetic_checkpoint(tmp_path: Path) -> None:
    extract = _load_script("extract_downstream_replay_bundles")
    ckpt = _make_synthetic_checkpoint(tmp_path)
    out_dir = tmp_path / "bundles"
    bundles = extract.extract_bundles(ckpt, out_dir)
    assert set(bundles.keys()) == {"placement", "axial_geometry", "assembled_plan"}
    for gate_id, bundle in bundles.items():
        assert bundle.gate_id == gate_id
        assert bundle.upstream_chain_provenance == "production_accepted"
        assert bundle.recorded_reviews == []
        assert (out_dir / f"{gate_id}_live_bundle.json").exists()


def test_extract_skips_missing_boundaries(tmp_path: Path) -> None:
    extract = _load_script("extract_downstream_replay_bundles")
    store = CampaignCheckpointStore(tmp_path / "empty_ckpt.json")
    bundles = extract.extract_bundles(tmp_path / "empty_ckpt.json", None)
    assert bundles == {}


# ---------------------------------------------------------------------------
# run_downstream_live_review.py
# ---------------------------------------------------------------------------


def test_run_recorded_review_offline_all_three_gates() -> None:
    run_live = _load_script("run_downstream_live_review")
    payload = run_live.run_sequential_live_review(
        str(FIXTURES), model="offline", mode="recorded-review",
    )
    assert payload["ok"] is True
    assert [item["gate_id"] for item in payload["gates"]] == ["placement", "axial_geometry", "assembled_plan"]
    for item in payload["gates"]:
        assert item["ok"] is True
        assert item["terminal_status"] == "accepted"


def test_run_preflight_only_mode() -> None:
    run_live = _load_script("run_downstream_live_review")
    payload = run_live.run_sequential_live_review(
        str(FIXTURES), model="offline", mode="preflight",
    )
    assert payload["ok"] is True
    for item in payload["gates"]:
        assert item["ok"] is True


def test_continue_on_fail_does_not_stop_sequence() -> None:
    run_live = _load_script("run_downstream_live_review")
    payload = run_live.run_sequential_live_review(
        str(FIXTURES), model="offline", mode="recorded-review",
        gates=("placement", "nonexistent", "axial_geometry"),
        continue_on_fail=True,
    )
    assert payload["ok"] is False
    assert len(payload["gates"]) == 3
    assert payload["gates"][1]["skipped"] is True


def test_default_break_on_first_failure() -> None:
    run_live = _load_script("run_downstream_live_review")
    payload = run_live.run_sequential_live_review(
        str(FIXTURES), model="offline", mode="recorded-review",
        gates=("nonexistent", "placement"),
    )
    assert payload["ok"] is False
    assert len(payload["gates"]) == 1
    assert payload["gates"][0]["skipped"] is True


def test_live_review_without_model_returns_error() -> None:
    run_live = _load_script("run_downstream_live_review")
    rc = run_live.main([
        "--bundle-dir", str(FIXTURES),
        "--mode", "live-review",
    ])
    assert rc == 2


def test_cli_recorded_review_writes_output(tmp_path: Path) -> None:
    run_live = _load_script("run_downstream_live_review")
    out_path = tmp_path / "result.json"
    rc = run_live.main([
        "--bundle-dir", str(FIXTURES),
        "--mode", "recorded-review",
        "--model", "offline",
        "--out", str(out_path),
    ])
    assert rc == 0
    data = json.loads(out_path.read_text())
    assert data["ok"] is True
    assert len(data["gates"]) == 3
