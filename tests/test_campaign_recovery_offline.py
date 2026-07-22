"""Phase 8C Step 3F offline campaign recovery qualification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import ACCEPTED_BOUNDARIES
from openmc_agent.plan_builder.closed_loop.campaign_recovery import (
    CampaignRecoveryFault,
    CampaignRecoveryScenario,
    run_campaign_recovery_matrix,
    run_campaign_recovery_scenario,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate_replay"


def test_clean_campaign_reaches_all_accepted_boundaries_without_recovery_calls() -> None:
    result = run_campaign_recovery_scenario(bundle_dir=FIXTURES, fault=CampaignRecoveryFault.CLEAN, scenario_id="clean")
    assert result.terminal_status == "accepted"
    assert result.reused_boundaries == list(ACCEPTED_BOUNDARIES)
    assert result.invalidated_boundaries == []
    assert all(value == 0 for value in result.recovery_call_counts.values())
    assert set(result.gate_call_counts) == {"facts", "material_universe", "placement", "axial_geometry", "assembled_plan"}


@pytest.mark.parametrize(
    "fault,target,boundary",
    [
        (CampaignRecoveryFault.INPUT_HASH_DRIFT, "placement", "gate:placement"),
        (CampaignRecoveryFault.POLICY_HASH_DRIFT, "placement", "gate:placement"),
        (CampaignRecoveryFault.CHECKPOINT_CORRUPTION, "placement", "gate:placement"),
        (CampaignRecoveryFault.BUNDLE_HASH_CORRUPTION, "placement", "gate:placement"),
        (CampaignRecoveryFault.SENSITIVE_FIELD, "placement", "gate:placement"),
        (CampaignRecoveryFault.REVIEW_SCHEMA_FAILURE, "placement", "gate:placement"),
        (CampaignRecoveryFault.REVIEW_FINDING_BLOCKER, "placement", "gate:placement"),
    ],
)
def test_target_faults_fail_closed_and_invalidate_target_suffix(fault, target, boundary) -> None:
    result = run_campaign_recovery_scenario(bundle_dir=FIXTURES, fault=fault, target=target)
    assert result.terminal_status == "blocked"
    assert result.issue_codes
    assert boundary in result.invalidated_boundaries
    assert all(value == 0 for value in result.recovery_call_counts.values())


def test_missing_upstream_fails_closed_without_replaying_old_output() -> None:
    result = run_campaign_recovery_scenario(
        bundle_dir=FIXTURES,
        fault=CampaignRecoveryFault.MISSING_UPSTREAM,
        target="assembled_plan",
    )
    assert result.terminal_status == "blocked"
    assert "gate_replay.upstream_not_accepted" in result.issue_codes
    assert result.recovery_call_counts["assembled_plan"] == 0


def test_upstream_change_uses_production_dependency_closure() -> None:
    from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH

    result = run_campaign_recovery_scenario(
        bundle_dir=FIXTURES,
        fault=CampaignRecoveryFault.UPSTREAM_PATCH_CHANGE,
        target="materials",
    )
    expected_patch_closure = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.transitive_dependents(["materials"])
    assert result.dependency_closure == expected_patch_closure
    assert result.invalidated_boundaries == [
        "gate:material_universe", "gate:placement", "gate:axial_geometry", "gate:assembled_plan"
    ]
    assert result.reused_boundaries == ["gate:facts", "patch:materials", "patch:universes"]


def test_scenario_and_result_reject_sensitive_fields() -> None:
    with pytest.raises(ValueError, match="sensitive"):
        CampaignRecoveryScenario(scenario_id="bad", fault=CampaignRecoveryFault.CLEAN, metadata={"prompt": "x"})


def test_full_matrix_is_machine_readable_and_sanitized() -> None:
    result = run_campaign_recovery_matrix(FIXTURES)
    assert result.ok
    assert len(result.scenarios) >= 10
    text = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    for forbidden in ("prompt", "reasoning", "raw_response", "api_key", "secret"):
        assert forbidden not in text.lower()


def test_recovery_cli_smoke() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("recovery_cli", "scripts/qualify_campaign_recovery_offline.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = Path("/tmp/phase8c_step3f_recovery.json")
    assert module.main.__name__ == "main"
    payload = run_campaign_recovery_matrix(FIXTURES).model_dump(mode="json")
    output.write_text(json.dumps(payload), encoding="utf-8")
    assert json.loads(output.read_text())["ok"] is True
