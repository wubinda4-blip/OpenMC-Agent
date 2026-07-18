"""Prompt construction + evidence rendering for the investigation stage.

Two responsibilities:

* :func:`build_investigation_prompt` — builds the LLM prompt for the
  investigation agent.  Strict JSON output contract; describes available
  tools, requirement excerpt, existing evidence summary, policy
  suggestions, and remaining budget.
* :func:`render_investigation_evidence_for_prompt` — renders evidence
  claims as a structured "use as constraints, not free text" section
  that the patch generator prepends to the patch prompt.

Both functions are pure: no I/O, no LLM calls.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .agent import InvestigationContext
from .errors import PlanInvestigationIssue
from .models import EvidenceClaim

__all__ = [
    "build_investigation_prompt",
    "render_investigation_evidence_for_prompt",
    "INVESTIGATION_PROMPT_HEADER",
    "EVIDENCE_SECTION_HEADER",
]


INVESTIGATION_PROMPT_HEADER = (
    "You are a planning-investigation agent for an OpenMC model-building pipeline."
)
EVIDENCE_SECTION_HEADER = (
    "Evidence Claims (use as constraints, NOT as free text)"
)


# ---------------------------------------------------------------------------
# Investigation prompt
# ---------------------------------------------------------------------------


def build_investigation_prompt(context: InvestigationContext) -> str:
    """Build the LLM prompt for one investigation session.

    Output contract (strict JSON):
        {"actions": [{"tool": "<name>", "arguments": {...}}], "summary": "..."}

    The LLM is told:

    * Which tools exist (name + input schema).
    * What evidence is already in the ledger (subject/predicate/value).
    * What policy suggestions apply to this patch type.
    * How much budget remains.
    * That anything outside the JSON contract will be rejected.

    The prompt is intentionally compact: requirement excerpt, evidence
    summary, tool list.  No few-shot examples, no prose padding.
    """

    tool_descriptions = _render_tool_section(context.available_tools)
    evidence_summary = _render_existing_evidence(context.existing_evidence)
    policy_lines = list(context.policy_suggestions)
    budget_text = (
        f"budget: max_tool_calls={context.budget.max_tool_calls}, "
        f"max_results_per_tool={context.budget.max_results_per_tool}, "
        f"max_evidence_claims={context.budget.max_evidence_claims}"
    )

    sections = [
        INVESTIGATION_PROMPT_HEADER,
        "",
        f"Target patch type: {context.patch_type}",
        budget_text,
        "",
        "Requirement excerpt (authoritative source of truth):",
        "-----",
        context.requirement_excerpt,
        "-----",
        "",
    ]
    if evidence_summary:
        sections.append("Existing evidence claims already in the ledger:")
        sections.append(evidence_summary)
        sections.append("")
    if policy_lines:
        sections.append("Patch-type policy suggestions (advisory; you may deviate):")
        for line in policy_lines:
            sections.append(f"- {line}")
        sections.append("")
    sections.append("Available tools:")
    sections.append(tool_descriptions)
    sections.append("")
    sections.append(_render_output_contract())
    return "\n".join(sections)


def _render_tool_section(
    available_tools: Iterable["object"],
) -> str:
    """Render the tool list as ``name — description — input schema``."""

    lines: list[str] = []
    for spec in available_tools:
        # We treat the spec as a duck-typed object with the relevant
        # attributes; InvestigationToolSpec satisfies this.
        name = getattr(spec, "name", "")
        description = getattr(spec, "description", "")
        input_schema = getattr(spec, "input_schema", {}) or {}
        produces_evidence = getattr(spec, "produces_evidence", True)
        lines.append(f"- {name}: {description}")
        lines.append(
            f"    produces_evidence={produces_evidence}; "
            f"input_schema={json.dumps(input_schema, sort_keys=True, ensure_ascii=False)}"
        )
    if not lines:
        lines.append("(no tools registered)")
    return "\n".join(lines)


def _render_existing_evidence(
    claims: Iterable[EvidenceClaim],
    *,
    max_items: int = 30,
) -> str:
    """Compact one-line-per-claim summary of evidence already in the ledger."""

    out: list[str] = []
    for claim in claims:
        if len(out) >= max_items:
            out.append("... (further claims omitted)")
            break
        value_repr = _compact_value(claim.value)
        out.append(
            f"- {claim.subject}.{claim.predicate} = {value_repr} "
            f"({claim.status.value}/{claim.criticality.value})"
        )
    return "\n".join(out)


def _compact_value(value: Any, *, max_len: int = 80) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) > max_len:
        return rendered[: max_len - 3] + "..."
    return rendered


def _render_output_contract() -> str:
    return (
        "Output contract (STRICT JSON, no markdown, no commentary):\n"
        "  {\"actions\": [{\"tool\": <tool_name>, \"arguments\": {<tool input schema>}}], "
        "\"summary\": \"<one short audit sentence>\"}\n"
        "Rules:\n"
        "- \"actions\" may be an empty list (if no investigation is needed).\n"
        "- Each \"tool\" value MUST be one of the listed tool names.\n"
        "- Each \"arguments\" value MUST match the tool's input schema.\n"
        "- Do NOT include any other top-level key.  Do NOT include markdown.\n"
        "- Anything outside this contract will be rejected and the session blocked."
    )


# ---------------------------------------------------------------------------
# Evidence injection into the patch prompt
# ---------------------------------------------------------------------------


def render_investigation_evidence_for_prompt(
    evidence: Iterable[Mapping[str, Any]] | list[dict[str, Any]],
) -> str:
    """Render evidence claims as a structured prompt section.

    The patch generator prepends the returned text to its prompt so the
    LLM treats the claims as constraints rather than as free text to
    echo back.

    Expected claim shape (produced by
    :func:`openmc_agent.plan_investigation.agent.collect_evidence_for_patch_prompt`):
        {
            "claim_id": "...",
            "subject": "...",
            "predicate": "...",
            "value": <JSON-compatible>,
            "status": "explicit" | ...,
            "criticality": "informational" | ...,
            "source_spans": [{"source_id": "...", "span_id": "..."}]
        }
    """

    payload = list(evidence)
    if not payload:
        return ""
    lines = [EVIDENCE_SECTION_HEADER, ""]
    for claim in payload:
        _validate_claim_payload(claim)
        value_repr = _compact_value(claim.get("value"), max_len=120)
        spans_repr = ", ".join(
            f"{span.get('source_id')}:{span.get('span_id')}"
            for span in claim.get("source_spans", [])
        )
        lines.append(
            f"- [{claim.get('claim_id')}] {claim.get('subject')}.{claim.get('predicate')}"
            f" = {value_repr}"
            f"  ({claim.get('status')}/{claim.get('criticality')})"
        )
        if spans_repr:
            lines.append(f"    sources: {spans_repr}")
    lines.append("")
    lines.append(
        "Treat the claims above as constraints.  Do NOT copy their prose into "
        "the patch.  Do NOT invent values that contradict them."
    )
    return "\n".join(lines)


def _validate_claim_payload(claim: Mapping[str, Any]) -> None:
    required = {"claim_id", "subject", "predicate", "value", "status"}
    missing = required - set(claim.keys())
    if missing:
        raise PlanInvestigationIssue(
            "plan_investigation.evidence_payload_invalid",
            "evidence claim payload is missing required keys",
            details={"missing": sorted(missing)},
        )
