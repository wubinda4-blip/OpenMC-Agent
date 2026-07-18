"""Tests for facts revision JSON extraction and retry logic.

These tests cover the Phase 7A fixes that let the facts revision path
survive the DS Flash failure mode where the model spends its output
budget on ``reasoning_content`` and returns an empty ``content`` (or
prose-wrapped JSON).
"""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_builder.closed_loop.facts_revision import (
    _extract_facts_revision_payload,
    normalize_facts_revision,
)


_VALID_PROPOSAL = {
    "proposal_id": "p1",
    "confidence": 0.9,
    "operations": [
        {"op": "replace", "path": "/model_scope", "value": "multi_assembly_core"},
    ],
}


def test_extract_passthrough_dict():
    payload = {"operations": []}
    assert _extract_facts_revision_payload(payload) is payload


def test_extract_pure_json_string():
    text = json.dumps(_VALID_PROPOSAL)
    payload = _extract_facts_revision_payload(text)
    assert payload["proposal_id"] == "p1"


def test_extract_prose_wrapped_json():
    """The DS Flash failure mode: chain-of-thought prose followed by JSON."""
    text = (
        "We need to compare the source with the facts patch. Let's reason "
        "step by step.\n"
        "First, model_scope conflicts with multi_assembly_core.\n"
        "Now, format the JSON.\n"
        + json.dumps(_VALID_PROPOSAL)
    )
    payload = _extract_facts_revision_payload(text)
    assert payload["proposal_id"] == "p1"
    assert "operations" in payload


def test_extract_multiple_candidates_picks_last_with_operations():
    """If multiple JSON objects appear, prefer the last with operations."""
    text = (
        "```json\n" + json.dumps({"review_status": "complete"}) + "\n```\n"
        "Now the proposal:\n" + json.dumps(_VALID_PROPOSAL)
    )
    payload = _extract_facts_revision_payload(text)
    assert "operations" in payload
    assert payload["proposal_id"] == "p1"


def test_extract_empty_string_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_facts_revision_payload("")


def test_extract_whitespace_only_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_facts_revision_payload("   \n  ")


def test_extract_non_json_text_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_facts_revision_payload("the model just returned prose")


def test_normalize_accepts_pure_json():
    text = json.dumps(_VALID_PROPOSAL)
    proposal = normalize_facts_revision(text)
    assert proposal.proposal_id == "p1"
    assert len(proposal.operations) == 1


def test_normalize_accepts_prose_wrapped_json():
    """The main bug-fix: facts revision now handles prose+JSON."""
    text = (
        "Let me think about this. The model_scope should be multi_assembly. "
        "Here is my proposal.\n" + json.dumps(_VALID_PROPOSAL)
    )
    proposal = normalize_facts_revision(text)
    assert proposal.proposal_id == "p1"


def test_normalize_rejects_empty_operations():
    bad = {"proposal_id": "p2", "operations": []}
    with pytest.raises(ValueError):
        normalize_facts_revision(json.dumps(bad))


def test_normalize_rejects_invalid_op():
    bad = {
        "proposal_id": "p3",
        "operations": [{"op": "copy", "path": "/x", "value": 1}],
    }
    with pytest.raises(ValueError):
        normalize_facts_revision(json.dumps(bad))
