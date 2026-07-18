"""Real campaign client bundle tests."""

from openmc_agent.real_campaign import RealCampaignClientBundle, RealCampaignRunConfig, _create_client_bundle
from openmc_agent.llm_call_recorder import LLMCallRecorder


def _config(model: str = "fake:test") -> RealCampaignRunConfig:
    return RealCampaignRunConfig(
        run_id="r1",
        run_index=1,
        input_path="/tmp/in.md",
        model=model,
    )


def test_bundle_defaults_to_no_reviewer_no_repair():
    """Legacy callers (both flags False) get None for both new clients."""
    bundle = _create_client_bundle(_config(), recorder=None)
    assert bundle.plan_reviewer_client is None
    assert bundle.plan_repair_client is None
    assert bundle.reviewer_client_instance_id == ""
    assert bundle.repair_client_instance_id == ""


def test_bundle_creates_real_reviewer_when_enabled():
    """Phase 7A campaign enables plan_reviewer_enabled."""
    bundle = _create_client_bundle(
        _config(),
        recorder=None,
        plan_reviewer_enabled=True,
    )
    assert bundle.plan_reviewer_client is not None


def test_bundle_creates_real_repair_when_enabled():
    bundle = _create_client_bundle(
        _config(),
        recorder=None,
        plan_repair_enabled=True,
    )
    assert bundle.plan_repair_client is not None


def test_bundle_creates_both_when_both_enabled():
    bundle = _create_client_bundle(
        _config(),
        recorder=None,
        plan_reviewer_enabled=True,
        plan_repair_enabled=True,
    )
    assert bundle.plan_reviewer_client is not None
    assert bundle.plan_repair_client is not None


def test_bundle_registers_clients_with_recorder():
    recorder = LLMCallRecorder(run_id="r1", model="fake:test", provider="fake")
    bundle = _create_client_bundle(
        _config(),
        recorder=recorder,
        plan_reviewer_enabled=True,
        plan_repair_enabled=True,
    )
    # Recorder should know about patch / diag / proposer / plan_reviewer /
    # plan_repair (supervisor only when mode='real').
    assert bundle.patch_client_instance_id in recorder._client_instance_ids
    assert bundle.reviewer_client_instance_id in recorder._client_instance_ids
    assert bundle.repair_client_instance_id in recorder._client_instance_ids


def test_bundle_uses_per_role_model_overrides():
    bundle = _create_client_bundle(
        _config(model="fake:patch"),
        recorder=None,
        plan_reviewer_enabled=True,
        plan_repair_enabled=True,
        plan_reviewer_model="fake:reviewer",
        plan_repair_model="fake:repair",
    )
    # We don't reach into the wrapped client to inspect the model name, but
    # the construction must not crash and the clients must be present.
    assert bundle.plan_reviewer_client is not None
    assert bundle.plan_repair_client is not None


def test_bundle_supervisor_stays_optional():
    bundle = _create_client_bundle(
        _config(),
        recorder=None,
        plan_reviewer_enabled=True,
        plan_repair_enabled=True,
    )
    # Deterministic supervisor mode (default) → no real supervisor client.
    assert bundle.runtime_supervisor_client is None
