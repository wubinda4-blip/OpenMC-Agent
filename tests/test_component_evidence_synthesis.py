"""Tests for the evidence synthesis prompt + strict JSON parser."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_investigation.component_evidence import (
    ComponentEvidenceSynthesisResult,
)
from openmc_agent.plan_investigation.evidence_synthesis_prompt import (
    ComponentEvidenceSynthesisInput,
    SourceSpanDigest,
    build_component_evidence_synthesis_prompt,
    parse_component_evidence_synthesis_output,
)


def _input():
    return ComponentEvidenceSynthesisInput(
        patch_type="facts",
        requirement_excerpt="The model represents a full 3 by 3 core.",
        accepted_facts_summary={"model_scope": "multi_assembly_core"},
        available_spans=(
            SourceSpanDigest(
                span_id="span_abc",
                source_id="src_x",
                excerpt="Fuel enrichment is 3.5 wt%.",
                start_line=10,
                end_line=10,
            ),
        ),
        existing_evidence_summary=("model.scope_indicator_present = full_core",),
        policy_hints=("Use only the documented component ontology.",),
    )


def test_prompt_contains_required_sections() -> None:
    prompt = build_component_evidence_synthesis_prompt(_input())
    assert "Requirement excerpt" in prompt
    assert "Available SourceSpans" in prompt
    assert "span_abc" in prompt
    assert "Fuel enrichment" in prompt
    assert "Output contract" in prompt
    assert "STRICT JSON" in prompt


def test_prompt_contains_ontology_enums() -> None:
    prompt = build_component_evidence_synthesis_prompt(_input())
    # Component kinds enumerated.
    assert "fuel_pin" in prompt
    assert "guide_tube" in prompt
    assert "end_plug" in prompt
    # Predicates enumerated.
    assert "geometry.profile_required" in prompt
    assert "material.density_present" in prompt


def test_prompt_no_secrets_or_host_paths() -> None:
    prompt = build_component_evidence_synthesis_prompt(_input())
    assert "DEEPSEEK_API_KEY" not in prompt
    assert "SENSENOVA_API_KEY" not in prompt
    assert "/home/" not in prompt


def test_parse_valid_json_output() -> None:
    raw = json.dumps({
        "proposals": [
            {
                "component_kind": "fuel_pin",
                "profile_kind": "active_fuel_pin",
                "subject": "fuel_pin",
                "predicate": "geometry.profile_required",
                "value": {"layers": ["fuel", "gap"]},
                "source_span_ids": ["span_abc"],
                "material_roles": ["fuel"],
                "cell_roles": ["fuel"],
            }
        ],
        "unresolved_questions": [
            {"subject": "clad_thickness", "predicate": "geometry.profile_radius_boundary"}
        ],
    })
    result = parse_component_evidence_synthesis_output(raw, patch_type="facts")
    assert result is not None
    assert len(result.proposals) == 1
    assert len(result.unresolved_questions) == 1
    assert result.synthesis_hash


def test_parse_rejects_prose_only_output() -> None:
    result = parse_component_evidence_synthesis_output(
        "The fuel pin has 4 layers.", patch_type="facts"
    )
    assert result is None


def test_parse_strips_markdown_fence() -> None:
    raw = "```json\n" + json.dumps({
        "proposals": [
            {
                "component_kind": "fuel_pin",
                "subject": "x",
                "predicate": "geometry.component_present",
            }
        ],
    }) + "\n```"
    result = parse_component_evidence_synthesis_output(raw, patch_type="facts")
    assert result is not None
    assert len(result.proposals) == 1


def test_parse_rejects_unknown_top_level_keys() -> None:
    raw = json.dumps({
        "proposals": [],
        "patch": {"patch_type": "facts"},  # forbidden
    })
    assert parse_component_evidence_synthesis_output(raw, patch_type="facts") is None


def test_parse_rejects_invalid_component_kind() -> None:
    raw = json.dumps({
        "proposals": [
            {
                "component_kind": "pwr_fuel_pin",  # not in ontology
                "subject": "x",
                "predicate": "geometry.component_present",
            }
        ],
    })
    assert parse_component_evidence_synthesis_output(raw, patch_type="facts") is None


def test_parse_rejects_invalid_predicate() -> None:
    raw = json.dumps({
        "proposals": [
            {
                "component_kind": "fuel_pin",
                "subject": "x",
                "predicate": "custom.unknown",
            }
        ],
    })
    assert parse_component_evidence_synthesis_output(raw, patch_type="facts") is None


def test_parse_empty_actions_list_is_valid() -> None:
    """An empty proposals list is valid: the LLM found no new evidence."""
    raw = json.dumps({"proposals": [], "unresolved_questions": []})
    result = parse_component_evidence_synthesis_output(raw, patch_type="facts")
    assert result is not None
    assert len(result.proposals) == 0


def test_synthesis_hash_changes_with_proposal_value() -> None:
    a = parse_component_evidence_synthesis_output(
        json.dumps({
            "proposals": [
                {
                    "component_kind": "fuel_pin",
                    "subject": "x",
                    "predicate": "geometry.profile_radius_boundary",
                    "value": 0.5,
                }
            ],
        }),
        patch_type="facts",
    )
    b = parse_component_evidence_synthesis_output(
        json.dumps({
            "proposals": [
                {
                    "component_kind": "fuel_pin",
                    "subject": "x",
                    "predicate": "geometry.profile_radius_boundary",
                    "value": 0.6,
                }
            ],
        }),
        patch_type="facts",
    )
    assert a is not None and b is not None
    assert a.synthesis_hash != b.synthesis_hash
