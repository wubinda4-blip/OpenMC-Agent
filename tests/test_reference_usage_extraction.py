"""Tests for reference patch provenance extraction (S4)."""

from __future__ import annotations

from openmc_agent.real_campaign import RealCampaignRunResult, _populate_provenance, validate_real_run_truthfulness


def _make_result(**kw):
    defaults = dict(
        run_id="t1", status="completed", final_disposition="FIRST_PASS_SUCCESS",
        started_at="", completed_at="", duration_s=0,
        git_sha="", input_sha="", configuration_hash="",
        provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=True, llm_call_count=1,
    )
    defaults.update(kw)
    return RealCampaignRunResult(**defaults)


def test_reference_patches_extracted_from_build_state():
    result = _make_result()
    ws = {
        "plan_build_state": {
            "patches": {
                "p1": {"patch_type": "universes", "status": "accepted", "source": "reference",
                       "reference_source": "fixture_3B"},
                "p2": {"patch_type": "axial_layers", "status": "accepted"},
            },
        },
    }
    _populate_provenance(result, ws)
    assert "p1" in result.reference_patches_used
    assert "fixture_3B" in result.reference_patch_sources


def test_no_reference_patches_when_all_generated():
    result = _make_result()
    ws = {
        "plan_build_state": {
            "patches": {
                "p1": {"patch_type": "universes", "status": "accepted"},
                "p2": {"patch_type": "axial_layers", "status": "accepted"},
            },
        },
    }
    _populate_provenance(result, ws)
    assert result.reference_patches_used == []


def test_reference_patch_policy_extracted():
    result = _make_result()
    ws = {"reference_patch_policy": "strict"}
    _populate_provenance(result, ws)
    assert result.reference_patch_policy == "strict"


def test_reference_patch_usage_flagged_in_truthfulness():
    result = _make_result(reference_patches_used=["ref_001"])
    violations = validate_real_run_truthfulness(result, {})
    assert "reference_patches_used" in violations


def test_no_reference_patches_clean():
    result = _make_result()
    violations = validate_real_run_truthfulness(result, {})
    assert "reference_patches_used" not in violations
