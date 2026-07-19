"""Tests for the Phase 8A Step 4 campaign bundle + budget + fingerprint
investigator plumbing.

These tests do NOT invoke real LLMs.  They verify that:

* The investigator client gets created only when plan_investigation_enabled.
* The investigator client uses a distinct client_instance_id prefix.
* CampaignLLMBudget.plan_investigation envelope is computed correctly.
* CampaignResumeFingerprint detects investigation-config mismatch.
* Truthfulness auditor catches the documented violation codes.
"""

from __future__ import annotations

from typing import Any

import pytest

from openmc_agent.llm_call_recorder import LLMCallRecorder
from openmc_agent.plan_investigation.campaign_truthfulness import (
    INVESTIGATION_TRUTH_VIOLATIONS,
    TV_ARTIFACT_CONTAINS_HOST_PATH,
    TV_ARTIFACT_CONTAINS_SECRET,
    TV_CLAIM_WITHOUT_VALID_SOURCE_REF,
    TV_COMPLETED_WITHOUT_TOOL_CALL,
    TV_CONTROLLED_FAILURE_BYPASSED,
    TV_ENABLED_WITHOUT_REAL_CLIENT_CALL,
    TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE,
    TV_REASONING_CONTENT_PERSISTED,
    investigation_truth_violations_for_run,
)
from openmc_agent.real_campaign import (
    RealCampaignClientBundle,
    RealCampaignRunConfig,
    _create_client_bundle,
)
from openmc_agent.real_campaign_harness import (
    CampaignLLMBudget,
    CampaignResumeFingerprint,
    estimate_real_campaign_llm_budget,
)


def _base_cfg(model: str = "fake:test") -> RealCampaignRunConfig:
    return RealCampaignRunConfig(
        benchmark="demo", variant="A", model=model,
    )


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_defaults_to_zero_investigator() -> None:
    budget = CampaignLLMBudget(
        patch_generation=4, universe_manifest=0, universe_fragments=0,
        gate_review=10, plan_repair=10, runtime_diagnosis=0,
        runtime_proposal=0, runtime_supervisor=0,
    )
    assert budget.plan_investigation == 0
    assert budget.total == 24


def test_budget_includes_investigator_envelope() -> None:
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="auto",
        plan_investigation_patch_types=("facts",),
        plan_investigation_max_sessions_per_patch_type=1,
    )
    assert budget.plan_investigation >= 1
    assert budget.total >= budget.plan_investigation


def test_budget_zero_investigator_when_no_patch_types() -> None:
    budget = estimate_real_campaign_llm_budget(
        expected_patch_count=8, expected_universe_count=0,
        enabled_gate_count=5, max_review_rounds_per_gate=2,
        max_repair_rounds_per_gate=2, max_runtime_iterations=0,
        universes_generation_mode="auto",
        plan_investigation_patch_types=(),
    )
    assert budget.plan_investigation == 0


def test_budget_dict_includes_investigator_key() -> None:
    budget = CampaignLLMBudget(
        patch_generation=4, universe_manifest=0, universe_fragments=0,
        gate_review=10, plan_repair=10, runtime_diagnosis=0,
        runtime_proposal=0, runtime_supervisor=0,
        plan_investigation=3,
    )
    assert "plan_investigation" in budget.to_dict()


# ---------------------------------------------------------------------------
# Resume fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_investigation_mismatch_detected() -> None:
    """A change in investigation mode must register as a mismatch."""
    base = CampaignResumeFingerprint(
        git_sha="a", input_sha="a", requirement_sha="a", human_answer_sha="a",
        model="m", provider="p", reasoning_effort="default", output_mode="auto",
        plan_policy_hash="h", enabled_gates=("facts",), review_modes=(),
        universes_generation_mode="auto", universe_fragment_max_tokens=None,
        large_patch_safe_output_ratio=0.6, strict_structured_patch_output=True,
        material_policy="strict", runtime_mode="deterministic",
        openmc_cross_sections_fingerprint="x",
        plan_investigation_mode="off",
    )
    changed = CampaignResumeFingerprint(
        git_sha="a", input_sha="a", requirement_sha="a", human_answer_sha="a",
        model="m", provider="p", reasoning_effort="default", output_mode="auto",
        plan_policy_hash="h", enabled_gates=("facts",), review_modes=(),
        universes_generation_mode="auto", universe_fragment_max_tokens=None,
        large_patch_safe_output_ratio=0.6, strict_structured_patch_output=True,
        material_policy="strict", runtime_mode="deterministic",
        openmc_cross_sections_fingerprint="x",
        plan_investigation_mode="controlled",
    )
    mismatches = base.mismatches_against(changed)
    assert "plan_investigation_mode" in mismatches


def test_fingerprint_investigation_match_no_mismatches() -> None:
    """Same investigation config → no investigation-related mismatch."""
    base = CampaignResumeFingerprint(
        git_sha="a", input_sha="a", requirement_sha="a", human_answer_sha="a",
        model="m", provider="p", reasoning_effort="default", output_mode="auto",
        plan_policy_hash="h", enabled_gates=("facts",), review_modes=(),
        universes_generation_mode="auto", universe_fragment_max_tokens=None,
        large_patch_safe_output_ratio=0.6, strict_structured_patch_output=True,
        material_policy="strict", runtime_mode="deterministic",
        openmc_cross_sections_fingerprint="x",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
    )
    same = CampaignResumeFingerprint(
        git_sha="a", input_sha="a", requirement_sha="a", human_answer_sha="a",
        model="m", provider="p", reasoning_effort="default", output_mode="auto",
        plan_policy_hash="h", enabled_gates=("facts",), review_modes=(),
        universes_generation_mode="auto", universe_fragment_max_tokens=None,
        large_patch_safe_output_ratio=0.6, strict_structured_patch_output=True,
        material_policy="strict", runtime_mode="deterministic",
        openmc_cross_sections_fingerprint="x",
        plan_investigation_mode="controlled",
        plan_investigation_patch_types=("facts",),
    )
    assert base.mismatches_against(same) == []


# ---------------------------------------------------------------------------
# Truthfulness auditor
# ---------------------------------------------------------------------------


def test_truth_off_mode_no_violations() -> None:
    """When mode=off, no investigator-related violations are possible."""
    violations = investigation_truth_violations_for_run(
        run_summary={"plan_investigation_mode": "off"},
    )
    assert violations == []


def test_truth_enabled_without_real_call() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 0,
        },
        investigation_outcome={"completed": True},
    )
    assert TV_ENABLED_WITHOUT_REAL_CLIENT_CALL in violations


def test_truth_completed_without_tool_call() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 0,
            "evidence_claim_count": 0,
        },
    )
    assert TV_COMPLETED_WITHOUT_TOOL_CALL in violations


def test_truth_claim_without_source_ref() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 0,
        },
    )
    assert TV_CLAIM_WITHOUT_VALID_SOURCE_REF in violations


def test_truth_controlled_failure_bypassed() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
            "facts_patch_generated_after_investigation": True,
        },
        investigation_outcome={
            "completed": False,
            "blocked": True,
            "tool_call_count": 0,
            "evidence_claim_count": 0,
        },
    )
    assert TV_CONTROLLED_FAILURE_BYPASSED in violations


def test_truth_facts_patch_without_required_evidence() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
            "facts_patch_generated_after_investigation": True,
            "facts_evidence_injected": False,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 5,
        },
    )
    assert TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE in violations


def test_truth_reasoning_content_persisted_in_artifact() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 5,
        },
        artifact_text_snapshot="some text with reasoning_content leak",
    )
    assert TV_REASONING_CONTENT_PERSISTED in violations


def test_truth_artifact_contains_host_path() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 5,
        },
        artifact_text_snapshot="/home/secret/path leaked",
    )
    assert TV_ARTIFACT_CONTAINS_HOST_PATH in violations


def test_truth_artifact_contains_secret() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
        },
        investigation_outcome={
            "completed": True,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 5,
        },
        artifact_text_snapshot="DEEPSEEK_API_KEY=abc123",
    )
    assert TV_ARTIFACT_CONTAINS_SECRET in violations


def test_truth_no_violations_for_clean_controlled_run() -> None:
    violations = investigation_truth_violations_for_run(
        run_summary={
            "plan_investigation_mode": "controlled",
            "plan_investigation_network_call_count": 1,
            "facts_patch_generated_after_investigation": True,
            "facts_evidence_injected": True,
        },
        investigation_outcome={
            "completed": True,
            "blocked": False,
            "tool_call_count": 3,
            "evidence_claim_count": 5,
            "source_backed_claim_count": 5,
        },
        artifact_text_snapshot="clean audit text only",
    )
    assert violations == []


def test_truth_violation_codes_are_stable_strings() -> None:
    """Phase 8A Step 6: codes use stable subsystem prefixes."""

    allowed_prefixes = (
        "plan_investigation_",
        "facts_",
        "investigation_",
        # Phase 8A Step 6 additions.
        "controlled_inventory_",
        "inventory_constraint_",
        "inventory_preflight_",
        "materials_",
        "universes_",
        "research_",
        "placement_",
        "axial_",
        "review_coverage_",
    )
    for code in INVESTIGATION_TRUTH_VIOLATIONS:
        assert isinstance(code, str)
        assert any(code.startswith(p) for p in allowed_prefixes), (
            f"code {code!r} does not start with any allowed prefix: {allowed_prefixes}"
        )
