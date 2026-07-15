"""Tests for assembly catalog and core layout prompts (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patch_prompts import build_patch_prompt


def test_assembly_catalog_prompt_has_rules():
    prompt = build_patch_prompt("assembly_catalog", "test requirement")
    assert "assembly_catalog" in prompt
    assert "assembly_type_id" in prompt
    assert "pin_map" in prompt
    # Should not contain VERA4-specific data
    assert "VERA4" not in prompt


def test_assembly_catalog_prompt_no_expanded_lattice():
    prompt = build_patch_prompt("assembly_catalog", "test requirement")
    assert "NOT output a full expanded" in prompt or "NOT" in prompt
    assert "sparse" in prompt.lower() or "ONLY" in prompt


def test_assembly_catalog_prompt_output_contract():
    prompt = build_patch_prompt("assembly_catalog", "test requirement")
    assert "patch_type" in prompt
    assert "CRITICAL OUTPUT CONTRACT" in prompt


def test_core_layout_prompt_has_rules():
    prompt = build_patch_prompt("core_layout", "test requirement")
    assert "core_layout" in prompt
    assert "assembly_pattern" in prompt
    assert "assembly_type_id" in prompt
    assert "boundary" in prompt


def test_core_layout_prompt_no_pin_coordinates():
    prompt = build_patch_prompt("core_layout", "test requirement")
    # Core layout should not ask for pin-level details
    assert "NOT" in prompt
    assert "pin coordinates" in prompt or "pin" in prompt.lower()


def test_core_layout_prompt_shape_consistency():
    prompt = build_patch_prompt("core_layout", "test requirement")
    assert "shape" in prompt
    assert "row" in prompt.lower()


def test_facts_prompt_has_model_scope():
    prompt = build_patch_prompt("facts", "test requirement")
    assert "model_scope" in prompt
    assert "multi_assembly_core" in prompt
    assert "assembly_count" in prompt
    assert "scoped_expected_counts" in prompt


def test_facts_prompt_warns_about_division():
    prompt = build_patch_prompt("facts", "test requirement")
    assert "divide" in prompt.lower() or "division" in prompt.lower()


def test_assembly_catalog_prompt_no_core_placement():
    prompt = build_patch_prompt("assembly_catalog", "test requirement")
    assert "not" in prompt.lower()
    assert "placement" in prompt.lower() or "core" in prompt.lower()


def test_core_layout_prompt_no_materials():
    prompt = build_patch_prompt("core_layout", "test requirement")
    assert "NOT re-define materials" in prompt or "materials" in prompt
