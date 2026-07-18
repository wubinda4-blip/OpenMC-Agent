"""Tests for the investigation prompt builder + evidence injection renderer."""

from __future__ import annotations

import json

import pytest

from openmc_agent.plan_investigation.agent import InvestigationContext
from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.prompt import (
    EVIDENCE_SECTION_HEADER,
    build_investigation_prompt,
    render_investigation_evidence_for_prompt,
)
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_registry import build_default_step2_registry


def _ctx(text="alpha\nbeta\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    reg = build_default_step2_registry()
    return InvestigationContext(
        requirement_text=text,
        patch_type="facts",
        available_tools=tuple(reg.list_tools()),
        source_indexes={idx.document.source_id: idx},
        ledger=ld,
        policy_suggestions=("recommended tools: search_source_index",),
    )


def test_prompt_contains_output_contract() -> None:
    prompt = build_investigation_prompt(_ctx())
    assert "STRICT JSON" in prompt
    assert '"actions"' in prompt


def test_prompt_contains_tool_names() -> None:
    prompt = build_investigation_prompt(_ctx())
    assert "search_source_index" in prompt
    assert "inspect_requirement_structure" in prompt
    assert "inspect_patch_schema" in prompt
    assert "query_evidence_ledger" in prompt


def test_prompt_contains_requirement_excerpt() -> None:
    text = "line one\nline two with key info\n"
    prompt = build_investigation_prompt(_ctx(text))
    assert "line one" in prompt
    assert "line two with key info" in prompt


def test_prompt_truncates_long_requirement() -> None:
    long_text = "word " * 5000  # ~25 KB
    prompt = build_investigation_prompt(_ctx(long_text))
    assert "[truncated]" in prompt
    # Prompt stays under 10 KB even for huge requirements.
    assert len(prompt) < 10000


def test_prompt_includes_policy_suggestions() -> None:
    ctx = _ctx()
    prompt = build_investigation_prompt(ctx)
    assert "Patch-type policy suggestions" in prompt
    assert "recommended tools: search_source_index" in prompt


def test_prompt_includes_budget() -> None:
    prompt = build_investigation_prompt(_ctx())
    assert "max_tool_calls=5" in prompt
    assert "max_results_per_tool=50" in prompt
    assert "max_evidence_claims=100" in prompt


def test_prompt_excludes_api_keys_and_paths() -> None:
    prompt = build_investigation_prompt(_ctx())
    assert "DEEPSEEK_API_KEY" not in prompt
    assert "SENSENOVA_API_KEY" not in prompt
    assert "/home/" not in prompt


def test_render_evidence_section_empty_returns_empty() -> None:
    assert render_investigation_evidence_for_prompt([]) == ""


def test_render_evidence_section_with_claims() -> None:
    claims = [
        {
            "claim_id": "claim_x",
            "subject": "model",
            "predicate": "scope_indicator_present",
            "value": "full_core",
            "status": "explicit",
            "criticality": "supporting",
            "source_spans": [{"source_id": "src_a", "span_id": "span_b"}],
        }
    ]
    rendered = render_investigation_evidence_for_prompt(claims)
    assert EVIDENCE_SECTION_HEADER in rendered
    assert "claim_x" in rendered
    assert "model.scope_indicator_present" in rendered
    assert "full_core" in rendered
    assert "src_a:span_b" in rendered
    assert "use as constraints" in rendered


def test_render_evidence_rejects_missing_required_keys() -> None:
    with pytest.raises(PlanInvestigationIssue):
        render_investigation_evidence_for_prompt(
            [{"claim_id": "x", "subject": "y"}]  # missing predicate/value/status
        )


def test_render_evidence_compact_value_for_long_dicts() -> None:
    """Long claim values are truncated to keep the prompt compact."""
    big_value = {"k" + str(i): i for i in range(50)}
    rendered = render_investigation_evidence_for_prompt(
        [
            {
                "claim_id": "c1",
                "subject": "s",
                "predicate": "p",
                "value": big_value,
                "status": "explicit",
                "criticality": "informational",
                "source_spans": [],
            }
        ]
    )
    assert "..." in rendered
    assert len(rendered) < 1000


def test_prompt_does_not_include_secret_investigation_data() -> None:
    """Even if a claim's metadata somehow contained a secret, the
    prompt builder does not surface it (only structured fields are
    rendered).
    """
    from openmc_agent.plan_investigation.models import (
        EvidenceClaim,
        EvidenceSourceRef,
        EvidenceStatus,
    )

    idx = build_source_index(text="alpha\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(ref,),
        metadata={"secret_api_key": "should_not_appear"},
    )
    ctx = _ctx()
    ctx = ctx.model_copy(update={"existing_evidence": (claim,)})
    prompt = build_investigation_prompt(ctx)
    assert "should_not_appear" not in prompt
    assert "secret_api_key" not in prompt
