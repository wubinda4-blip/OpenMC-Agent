"""Regression tests for fuel_variant_id prompt/retry fix (commit 35a5c37).

Verifies:
1. assembly_catalog prompt schema includes fuel_variant_id
2. With fuel_variant_requirements, rules require the field on fuel types
3. Without fuel variant contract, omission is allowed
4. fuel_variant_missing retry extracts assembly_type_id from issue path
5. Retry includes validator's expected variant
6. Corner/edge/center IDs parse correctly
7. Multiple missing types all listed
8. Retry output contract requires single assembly_catalog JSON
9. Prompt artifact expansion doesn't affect actual prompt
10. No deterministic injection of fuel_variant_id into LLM patch
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.patch_generator import (
    FakePatchLLM,
    PatchGenerationContext,
    generate_patch,
)
from openmc_agent.plan_builder.patch_prompts import build_patch_prompt, build_retry_prompt


# ---------------------------------------------------------------------------
# 1. Schema includes fuel_variant_id
# ---------------------------------------------------------------------------

def test_assembly_catalog_prompt_has_fuel_variant_id() -> None:
    prompt = build_patch_prompt("assembly_catalog", "req", None)
    assert '"fuel_variant_id"' in prompt


# ---------------------------------------------------------------------------
# 2. With fuel_variant_requirements, rules require the field
# ---------------------------------------------------------------------------

def test_fuel_variant_rules_present_with_requirements() -> None:
    ctx = PatchGenerationContext(
        fuel_variant_requirements=[
            {"variant_id": "v1", "source_label": "region1", "enrichment_wt_percent": 3.1},
            {"variant_id": "v2", "source_label": "region2", "enrichment_wt_percent": 3.6},
        ],
    )
    prompt = build_patch_prompt("assembly_catalog", "req", ctx)
    assert "fuel_variant_id" in prompt
    assert "fuel_variant_requirements" in prompt


# ---------------------------------------------------------------------------
# 3. Without fuel variant contract, omission is OK
# ---------------------------------------------------------------------------

def test_no_fuel_variant_requirements_allows_omission() -> None:
    """When no fuel_variant_requirements are in context, the prompt should not
    force the field.  The schema shows it as 'omit_if_no_fuel_variant_requirements'."""
    ctx = PatchGenerationContext()  # no fuel_variant_requirements
    prompt = build_patch_prompt("assembly_catalog", "req", ctx)
    assert "omit_if_no_fuel_variant_requirements" in prompt


# ---------------------------------------------------------------------------
# 4-5. Retry extracts assembly_type_id and expected variant
# ---------------------------------------------------------------------------

def test_retry_extracts_type_id_and_expected() -> None:
    issues = [
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing fuel_variant_id",
            "path": "assembly_types[center_type].fuel_variant_id",
            "expected": "fuel_region1",
        },
    ]
    prompt = build_retry_prompt(
        "assembly_catalog", "req", None, issues, 1,
    )
    assert "center_type" in prompt
    assert "fuel_region1" in prompt


# ---------------------------------------------------------------------------
# 6. Corner/edge/center IDs parse correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_id", ["corner", "edge", "center", "type_a", "CE_type"])
def test_retry_parses_various_type_ids(type_id: str) -> None:
    """Ensure various assembly type IDs are extracted from the issue path."""
    issues = [
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing",
            "path": f"assembly_types[{type_id}].fuel_variant_id",
            "expected": "v1",
        },
    ]
    prompt = build_retry_prompt(
        "assembly_catalog", "req", None, issues, 1,
    )
    assert type_id in prompt
    assert "v1" in prompt


# ---------------------------------------------------------------------------
# 7. Multiple missing assembly types all listed
# ---------------------------------------------------------------------------

def test_retry_lists_multiple_missing_types() -> None:
    issues = [
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing",
            "path": "assembly_types[type_a].fuel_variant_id",
            "expected": "v1",
        },
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing",
            "path": "assembly_types[type_b].fuel_variant_id",
            "expected": "v2",
        },
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing",
            "path": "assembly_types[type_c].fuel_variant_id",
            "expected": "v3",
        },
    ]
    prompt = build_retry_prompt(
        "assembly_catalog", "req", None, issues, 1,
    )
    for tid, exp in [("type_a", "v1"), ("type_b", "v2"), ("type_c", "v3")]:
        assert tid in prompt
        assert exp in prompt


# ---------------------------------------------------------------------------
# 8. Retry output contract still requires single assembly_catalog JSON
# ---------------------------------------------------------------------------

def test_retry_requires_single_json() -> None:
    issues = [
        {
            "code": "patch.assembly_catalog.fuel_variant_missing",
            "severity": "error",
            "message": "missing",
            "path": "assembly_types[t].fuel_variant_id",
            "expected": "v1",
        },
    ]
    prompt = build_retry_prompt(
        "assembly_catalog", "req", None, issues, 1,
    )
    assert "ONLY JSON" in prompt or "only JSON" in prompt


# ---------------------------------------------------------------------------
# 9. Prompt artifact expansion doesn't affect actual prompt sent to LLM
# ---------------------------------------------------------------------------

def test_prompt_text_matches_sent_prompt() -> None:
    """The prompt_text saved in the attempt should match what was sent to the LLM."""
    raw = json.dumps({"patch_type": "settings", "source_strategy": "active_fuel_box"})
    fake = FakePatchLLM([raw])
    result = generate_patch(
        patch_type="settings",
        requirement="test",
        llm_client=fake, max_attempts=1,
    )
    assert result.ok is True
    saved_prompt = result.attempts[0].prompt_text
    assert saved_prompt is not None
    assert saved_prompt == fake.prompts[0]


# ---------------------------------------------------------------------------
# 10. No deterministic injection of fuel_variant_id into LLM patch
# ---------------------------------------------------------------------------

def test_no_deterministic_fuel_variant_injection() -> None:
    """generate_patch should NOT inject fuel_variant_id into the LLM output.
    The field must come from the LLM itself."""
    raw = json.dumps({
        "patch_type": "assembly_catalog",
        "assembly_types": [
            {
                "assembly_type_id": "t1",
                "pin_map": {"lattice_size": [3, 3], "default_universe_id": "u1"},
            },
        ],
    })
    fake = FakePatchLLM([raw])
    ctx = PatchGenerationContext(
        fuel_variant_requirements=[
            {"variant_id": "v1", "source_label": "region1", "enrichment_wt_percent": 3.1},
        ],
    )
    result = generate_patch(
        patch_type="assembly_catalog",
        requirement="test reactor",
        context=ctx,
        llm_client=fake, max_attempts=1,
    )
    # The LLM omitted fuel_variant_id; the generator should NOT inject it.
    # The parsed patch should NOT have fuel_variant_id.
    if result.parsed_patch:
        at = result.parsed_patch.get("assembly_types", [{}])[0]
        assert "fuel_variant_id" not in at or at.get("fuel_variant_id") is None
