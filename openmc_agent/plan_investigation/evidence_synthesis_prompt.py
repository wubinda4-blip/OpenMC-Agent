"""Strict JSON prompt + parser for component evidence synthesis.

The synthesis LLM is shown:
* The requirement excerpt + accepted Facts summary.
* The available SourceSpans (verbatim excerpts with span_ids).
* The reactor-neutral ontology (component_kind, profile_kind, predicate,
  applicability).
* The output contract.

It returns strict JSON:

    {
      "proposals": [
        {
          "component_kind": "...",
          "profile_kind": "...",
          "predicate": "...",
          "value": <JSON>,
          "source_span_ids": ["span_..."],
          "material_roles": [],
          "cell_roles": [],
          "unresolved_fields": []
        }
      ],
      "unresolved_questions": [
        {"subject": "...", "predicate": "...", "blocking_patch_types": []}
      ]
    }

No patch, no source line fabrication, no free-text facts.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .component_evidence import (
    APPLICABILITY_SCOPES,
    COMPONENT_KINDS,
    EVIDENCE_PREDICATES,
    PROFILE_KINDS,
    ComponentEvidenceProposal,
    ComponentEvidenceSynthesisResult,
    UnresolvedQuestion,
)
from .errors import PlanInvestigationIssue
from .models import EvidenceSourceRef

__all__ = [
    "ComponentEvidenceSynthesisInput",
    "build_component_evidence_synthesis_prompt",
    "parse_component_evidence_synthesis_output",
    "SYNTHESIS_OUTPUT_HEADER",
]


SYNTHESIS_OUTPUT_HEADER = (
    "You are a component-evidence synthesis agent for an OpenMC model-building pipeline."
)


# ---------------------------------------------------------------------------
# Synthesis input bundle
# ---------------------------------------------------------------------------


class SourceSpanDigest(AgentBaseModel):
    """Compact span summary shown to the synthesis LLM.

    Carries ``span_id`` + the verbatim excerpt + line range.  The LLM
    references spans by id only; it never invents line numbers.
    """

    span_id: str
    source_id: str
    excerpt: str
    start_line: int
    end_line: int
    section_path: tuple[str, ...] = Field(default_factory=tuple)


class ComponentEvidenceSynthesisInput(AgentBaseModel):
    """Inputs to the synthesis prompt."""

    patch_type: str
    requirement_excerpt: str
    accepted_facts_summary: dict[str, Any] = Field(default_factory=dict)
    available_spans: tuple[SourceSpanDigest, ...] = Field(default_factory=tuple)
    existing_evidence_summary: tuple[str, ...] = Field(default_factory=tuple)
    policy_hints: tuple[str, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_component_evidence_synthesis_prompt(
    synthesis_input: ComponentEvidenceSynthesisInput,
) -> str:
    """Build the strict-JSON synthesis prompt."""

    spans_block = _render_spans(synthesis_input.available_spans)
    facts_block = _render_facts(synthesis_input.accepted_facts_summary)
    existing_block = _render_existing_evidence(synthesis_input.existing_evidence_summary)
    policy_block = _render_policy_hints(synthesis_input.policy_hints)
    contract_block = _render_output_contract()

    sections = [
        SYNTHESIS_OUTPUT_HEADER,
        "",
        f"Target patch type: {synthesis_input.patch_type}",
        "",
        "Requirement excerpt:",
        "-----",
        synthesis_input.requirement_excerpt,
        "-----",
        "",
    ]
    if facts_block:
        sections.append("Accepted Facts (authoritative for scope and counts):")
        sections.append(facts_block)
        sections.append("")
    if existing_block:
        sections.append("Existing evidence claims in the ledger:")
        sections.append(existing_block)
        sections.append("")
    if policy_block:
        sections.append("Synthesis policy hints:")
        sections.append(policy_block)
        sections.append("")
    sections.append("Available SourceSpans (reference by span_id only):")
    sections.append(spans_block)
    sections.append("")
    sections.append(contract_block)
    return "\n".join(sections)


def _render_spans(spans: Iterable[SourceSpanDigest]) -> str:
    lines: list[str] = []
    for span in spans:
        lines.append(f"- span_id={span.span_id}  lines={span.start_line}-{span.end_line}")
        for line in span.excerpt.splitlines():
            lines.append(f"    | {line}")
    if not lines:
        lines.append("(no spans available — synthesis cannot reference source content)")
    return "\n".join(lines)


def _render_facts(facts: Mapping[str, Any]) -> str:
    if not facts:
        return ""
    lines: list[str] = []
    for key, value in facts.items():
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(rendered) > 120:
            rendered = rendered[:117] + "..."
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def _render_existing_evidence(items: Iterable[str]) -> str:
    out = list(items)
    if not out:
        return ""
    return "\n".join(f"- {item}" for item in out)


def _render_policy_hints(hints: Iterable[str]) -> str:
    out = list(hints)
    if not out:
        return ""
    return "\n".join(f"- {hint}" for hint in out)


def _render_output_contract() -> str:
    return (
        "Output contract (STRICT JSON, no markdown, no commentary):\n"
        "  {\n"
        "    \"proposals\": [\n"
        "      {\n"
        "        \"component_kind\": <one of: " + ", ".join(sorted(COMPONENT_KINDS)) + ">,\n"
        "        \"profile_kind\": <one of: " + ", ".join(sorted(PROFILE_KINDS)) + " or null>,\n"
        "        \"subject\": <short subject>,\n"
        "        \"predicate\": <one of: " + ", ".join(sorted(EVIDENCE_PREDICATES)) + ">,\n"
        "        \"value\": <JSON-compatible>,\n"
        "        \"source_span_ids\": [<span_id>, ...],\n"
        "        \"material_roles\": [<role>, ...],\n"
        "        \"cell_roles\": [<role>, ...],\n"
        "        \"applicability\": <one of: " + ", ".join(sorted(APPLICABILITY_SCOPES)) + ">,\n"
        "        \"unresolved_fields\": [<field>, ...]\n"
        "      }\n"
        "    ],\n"
        "    \"unresolved_questions\": [\n"
        "      {\"subject\": \"...\", \"predicate\": \"...\", \"blocking_patch_types\": []}\n"
        "    ]\n"
        "  }\n"
        "Rules:\n"
        "- Only reference span_ids that appear in the Available SourceSpans list.\n"
        "- Do NOT invent line numbers, span_ids, or source content.\n"
        "- Do NOT include patch / materials / universes / plan JSON.\n"
        "- Do NOT include notes / reasoning / prose outside the JSON.\n"
        "- Numerical values must appear verbatim in one of the referenced spans.\n"
        "- Unknown values go to unresolved_questions, not fabricated numbers."
    )


# ---------------------------------------------------------------------------
# Strict JSON parser
# ---------------------------------------------------------------------------


def parse_component_evidence_synthesis_output(
    raw: str,
    *,
    patch_type: str,
) -> ComponentEvidenceSynthesisResult | None:
    """Parse the LLM output into a :class:`ComponentEvidenceSynthesisResult`.

    Returns ``None`` when the output cannot be reduced to the strict
    JSON contract (the caller surfaces this as a controlled block).
    """

    text = raw.strip()
    if not text:
        return None
    payload = _extract_json_payload(text)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None
    # Reject unknown top-level keys.
    extra = set(payload.keys()) - {"proposals", "unresolved_questions", "summary"}
    if extra:
        return None
    proposals_raw = payload.get("proposals", [])
    if not isinstance(proposals_raw, list):
        return None
    proposals: list[ComponentEvidenceProposal] = []
    for item in proposals_raw:
        if not isinstance(item, dict):
            return None
        try:
            proposals.append(
                ComponentEvidenceProposal(
                    proposal_id="",
                    component_kind=item.get("component_kind", ""),
                    profile_kind=item.get("profile_kind"),
                    subject=str(item.get("subject", "")),
                    predicate=str(item.get("predicate", "")),
                    value=item.get("value"),
                    source_span_ids=tuple(item.get("source_span_ids", []) or []),
                    material_roles=tuple(item.get("material_roles", []) or []),
                    cell_roles=tuple(item.get("cell_roles", []) or []),
                    applicability=item.get("applicability", "global"),
                    axial_region_kind=item.get("axial_region_kind"),
                    host_component_kind=item.get("host_component_kind"),
                    source_label=str(item.get("source_label", "") or ""),
                    unresolved_fields=tuple(item.get("unresolved_fields", []) or []),
                )
            )
        except Exception:
            # ComponentEvidenceProposal validators raise ValueError
            # subclasses (PlanInvestigationIssue); Pydantic wraps them
            # as ValidationError.  Either way the proposal is invalid.
            return None
    unresolved_raw = payload.get("unresolved_questions", [])
    if not isinstance(unresolved_raw, list):
        return None
    unresolved: list[UnresolvedQuestion] = []
    for item in unresolved_raw:
        if not isinstance(item, dict):
            return None
        try:
            from .hashing import short_id

            unresolved.append(
                UnresolvedQuestion(
                    question_id=short_id(
                        "q",
                        {
                            "s": item.get("subject", ""),
                            "p": item.get("predicate", ""),
                        },
                    ),
                    subject=str(item.get("subject", "")),
                    predicate=str(item.get("predicate", "")),
                    blocking_patch_types=tuple(item.get("blocking_patch_types", []) or []),
                    suggested_research_terms=tuple(
                        item.get("suggested_research_terms", []) or []
                    ),
                )
            )
        except PlanInvestigationIssue:
            return None
    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        return None
    return ComponentEvidenceSynthesisResult(
        patch_type=patch_type,
        proposals=tuple(proposals),
        unresolved_questions=tuple(unresolved),
        summary=summary,
    )


def _extract_json_payload(text: str) -> Any:
    """Same tolerant extraction as the action-planning parser."""

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    obj = _extract_largest_balanced(text, "{", "}")
    if obj is not None:
        try:
            return json.loads(obj)
        except (ValueError, TypeError):
            pass
    return None


def _extract_largest_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    best = ""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch:
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start : i + 1]
                    if len(candidate) > len(best):
                        best = candidate
                    start = -1
    return best if best else None
