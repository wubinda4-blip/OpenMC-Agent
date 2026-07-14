"""VERA3B runtime stability CLI tests."""

from __future__ import annotations

import json
from pathlib import Path


def test_stability_cli_without_key_writes_not_run_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from scripts.evaluate_vera3b_runtime_stability import main
    monkeypatch.setattr("sys.argv", [
        "stability", "--output-dir", str(tmp_path),
        "--profile", "pilot", "--runs", "3",
    ])
    assert main() == 2  # environment error
    manifest = json.loads((tmp_path / "campaign_manifest.json").read_text())
    assert manifest["aggregate_status"] == "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"


def test_stability_cli_with_key_needs_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from scripts.evaluate_vera3b_runtime_stability import main
    monkeypatch.setattr("sys.argv", [
        "stability", "--output-dir", str(tmp_path),
        "--profile", "qualification", "--runs", "10",
    ])
    assert main() == 2  # confirmation required
    manifest = json.loads((tmp_path / "campaign_manifest.json").read_text())
    assert manifest["aggregate_status"] == "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"


def test_stability_cli_manifest_has_correct_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from scripts.evaluate_vera3b_runtime_stability import main
    monkeypatch.setattr("sys.argv", [
        "stability", "--output-dir", str(tmp_path),
        "--profile", "qualification",
    ])
    assert main() == 2
    manifest = json.loads((tmp_path / "campaign_manifest.json").read_text())
    cfg = manifest["configuration"]
    assert cfg["reference_patch_policy"] == "off"
    assert cfg["allow_monolithic_fallback_for_incremental_failure"] is False
    assert cfg["incremental_planning"] is True
    assert cfg["runtime_supervisor"] is True
    assert manifest["requested_runs"] == 10


def test_stability_cli_default_profile_is_pilot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from scripts.evaluate_vera3b_runtime_stability import main
    monkeypatch.setattr("sys.argv", [
        "stability", "--output-dir", str(tmp_path),
    ])
    assert main() == 2
    manifest = json.loads((tmp_path / "campaign_manifest.json").read_text())
    assert manifest["profile"] == "pilot"
    assert manifest["requested_runs"] == 3
