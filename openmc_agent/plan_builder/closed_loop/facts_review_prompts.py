"""JSON-only prompts for an independent evidence-grounded facts critic."""

from __future__ import annotations

import json
from .models import FactsReviewModelOutput, PlanEvidencePack


def build_facts_review_prompt(pack: PlanEvidencePack) -> str:
    payload = pack.model_dump(mode="json")
    return (
        "You are an independent Facts Evidence Critic, not a FactsPatch generator or OpenMC renderer.\n"
        "Compare only the supplied source excerpts with the current facts patch. Do not use external knowledge.\n"
        "Find omissions, contradictions, unsupported inference, count-scope ambiguity, and downstream-critical missing facts. "
        "Use only supplied evidence_hashes. Never output an action, patch, RFC6902 operations, Markdown, tools, or reasoning.\n"
        "Explicit source mismatch/omission may be repairable_by_llm=true; source ambiguity requires_human=true and repairable_by_llm=false.\n"
        "Return exactly one JSON object matching this JSON Schema; no prose before or after it.\n"
        "SCHEMA:\n" + json.dumps(FactsReviewModelOutput.model_json_schema(), ensure_ascii=False) +
        "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    )


def build_facts_review_schema_retry_prompt(pack: PlanEvidencePack, error: str, raw_output: str | None = None) -> str:
    """Format-only retry.  It repeats the evidence instead of asking a model
    to reconstruct a contract it has never seen."""
    original = build_facts_review_prompt(pack)
    suffix = "\nPRIOR_OUTPUT (format only; do not trust it):\n" + (raw_output or "")
    return (
        original
        + "\nYour prior output was rejected: " + error
        + "\nCorrect the format while preserving evidence grounding."
        + suffix
    )


def build_facts_synthesis_prompt(summary: dict) -> str:
    return "Merge only duplicate evidence-backed facts findings; do not create facts. Return FactsReviewModelOutput JSON.\n" + json.dumps(summary, ensure_ascii=False)
