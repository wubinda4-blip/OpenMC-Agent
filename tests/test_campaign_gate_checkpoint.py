"""Phase 8C Step 3A campaign checkpoint contracts."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import (
    CampaignGateCheckpoint,
    CampaignCheckpointStore,
    FactsActionCheckpoint,
)


def _checkpoint() -> CampaignGateCheckpoint:
    return CampaignGateCheckpoint.create(
        campaign_id="c1",
        gate_id="material_universe",
        input_payload={"materials_hash": "m", "universes_hash": "u"},
        evidence={"claims": ["a"]},
        inventory={"requirements": ["r1"]},
        structured_output_policy={"max_attempts": 2, "temperature": 0},
        canonical_hashes={"facts": "f", "policy": "p"},
    )


def test_checkpoint_reuses_only_when_all_fingerprints_match(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    checkpoint = _checkpoint()
    store.accept_gate(checkpoint)
    restored = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    found = restored.lookup_gate(
        "material_universe",
        input_payload={"materials_hash": "m", "universes_hash": "u"},
        evidence={"claims": ["a"]},
        inventory={"requirements": ["r1"]},
        structured_output_policy={"max_attempts": 2, "temperature": 0},
        canonical_hashes={"facts": "f", "policy": "p"},
    )
    assert found is not None
    assert found.input_hash == checkpoint.input_hash


def test_checkpoint_fingerprint_mismatch_fails_closed(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    store.accept_gate(_checkpoint())
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        store.lookup_gate(
            "material_universe",
            input_payload={"materials_hash": "changed", "universes_hash": "u"},
            evidence={"claims": ["a"]},
            inventory={"requirements": ["r1"]},
            structured_output_policy={"max_attempts": 2, "temperature": 0},
            canonical_hashes={"facts": "f", "policy": "p"},
        )


def test_facts_action_checkpoint_preserves_timeout_telemetry(tmp_path) -> None:
    store = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json")
    store.record_facts_action(
        FactsActionCheckpoint(
            action_id="facts:search:1",
            tool_name="search_source_index",
            arguments_hash="payload-hash",
            status="provider_timeout",
            billed_call_count=1,
            provider_deadline="30",
        )
    )
    action = CampaignCheckpointStore(tmp_path / "campaign_checkpoint.json").facts_action("facts:search:1")
    assert action is not None
    assert action.status == "provider_timeout"
    assert action.billed_call_count == 1
    assert action.arguments_hash == "payload-hash"
