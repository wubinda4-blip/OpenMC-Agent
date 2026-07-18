"""Phase 7A real campaign truthfulness tests."""

from openmc_agent.real_campaign import RealCampaignRunResult
from openmc_agent.real_campaign_harness import (
    TRUTHFULNESS_VIOLATION_CODES,
    validate_real_canary_truthfulness,
)


def _result(**kwargs) -> RealCampaignRunResult:
    base = dict(
        run_id="r1", status="completed", final_disposition="UNKNOWN",
        started_at="", completed_at="", duration_s=0.0,
        git_sha="", input_sha="", configuration_hash="",
        provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=False,
        llm_call_count=1,
    )
    base.update(kwargs)
    return RealCampaignRunResult(**base)


def test_truthfulness_codes_includes_required_phase7a_violations():
    required = {
        "fake_client_used",
        "client_fallback_used",
        "reference_patch_used",
        "gold_few_shot_used",
        "benchmark_specific_few_shot_used",
        "monolithic_fallback_attempted",
        "monolithic_fallback_used",
        "render_before_final_gate_accepted",
        "export_before_final_gate_accepted",
        "smoke_before_final_gate_accepted",
        "partial_fragment_exposed",
        "missing_real_reviewer_call",
        "gate_auto_accepted",
        "stale_assembled_plan_executed",
        "reasoning_content_persisted",
        "provider_evidence_unverifiable",
    }
    assert required.issubset(set(TRUTHFULNESS_VIOLATION_CODES))


def test_clean_real_run_has_no_violations():
    result = _result(real_llm_verified=True)
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    # An empty run with no gate activity and no render should have only the
    # legacy "real_llm_not_verified" path; here real_llm_verified=True.
    assert "fake_client_used" not in violations
    assert "render_before_final_gate_accepted" not in violations


def test_fake_client_used_is_violation():
    result = _result(fake_client_used=True, real_llm_verified=False)
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "fake_client_used" in violations


def test_partial_fragment_exposed_is_violation():
    result = _result(partial_fragment_exposed=True)
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "partial_fragment_exposed" in violations


def test_reasoning_content_persisted_is_violation():
    result = _result(reasoning_content_persisted=True)
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "reasoning_content_persisted" in violations


def test_gate_auto_accepted_with_zero_reviews_is_violation():
    """A gate marked accepted but with 0 reviewer calls is auto-accepted."""
    result = _result(real_llm_verified=True)
    ws = {
        "plan_build_state": {
            "plan_loop_stages": {
                "facts": {
                    "gate_id": "facts",
                    "status": "accepted",
                    "review_count": 0,
                    "metadata": {},
                },
            },
        },
    }
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert any(v.startswith("gate_auto_accepted") for v in violations)


def test_monolithic_fallback_when_fragmented_expected_is_violation():
    result = _result(
        fragmented_universes_used=False,
        monolithic_fallback_used=True,
    )
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=True,
    )
    assert "fragmented_universes_expected_but_monolithic_used" in violations


def test_missing_reviewer_call_when_gate_reviewed_is_violation():
    """A gate that reached REVIEWED/ACCEPTED but the campaign recorded
    zero reviewer network calls — missing_real_reviewer_call."""
    result = _result(
        real_llm_verified=True,
        plan_reviewer_network_call_count=0,
    )
    ws = {
        "plan_build_state": {
            "plan_loop_stages": {
                "facts": {
                    "gate_id": "facts",
                    "status": "accepted",
                    "review_count": 1,
                    "metadata": {"accepted_input_hash": "h1"},
                },
            },
        },
    }
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "missing_real_reviewer_call" in violations


def test_reference_patch_usage_is_violation():
    result = _result(reference_patches_used=["p1"])
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "reference_patches_used" in violations


def test_violations_are_deduplicated():
    """Repeated identical violations collapse to a single entry."""
    result = _result(
        partial_fragment_exposed=True,
    )
    ws = {}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert violations.count("partial_fragment_exposed") == 1
