"""Tests for core layout prompt (P2-FULLCORE-1).

Note: core layout prompt tests are also covered in test_assembly_catalog_prompt.py.
This file adds more specific tests for the core_layout prompt.
"""

from openmc_agent.plan_builder.patch_prompts import build_patch_prompt


def test_core_layout_prompt_reactor_neutral_example():
    prompt = build_patch_prompt("core_layout", "test requirement")
    # The example should use generic type IDs, not VERA4 data
    assert "type_a" in prompt or "type_b" in prompt
    assert "VERA4" not in prompt


def test_core_layout_prompt_allows_or_forbids_keys():
    prompt = build_patch_prompt("core_layout", "test requirement")
    assert "Allowed top-level keys" in prompt
    assert "FORBIDDEN" in prompt


def test_core_layout_prompt_context_block():
    """Context should include multi-assembly fields when provided."""
    class MockContext:
        model_scope = "multi_assembly_core"
        assembly_count = 4
        known_assembly_type_ids = ["type_a", "type_b"]
        assembly_pitch_cm = 21.5

    prompt = build_patch_prompt("core_layout", "test", context=MockContext())
    assert "model_scope" in prompt
    assert "assembly_count" in prompt or "4" in prompt
    assert "type_a" in prompt
