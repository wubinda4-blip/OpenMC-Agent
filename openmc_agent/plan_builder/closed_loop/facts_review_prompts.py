"""JSON-only prompts for an independent evidence-grounded facts critic."""

from __future__ import annotations

import json
from .models import PlanEvidencePack


def build_facts_review_prompt(pack: PlanEvidencePack) -> str:
    payload = pack.model_dump(mode="json")
    return (
        "You are an independent Facts Evidence Critic, not a FactsPatch generator or OpenMC renderer.\n"
        "Compare only the supplied source excerpts with the current facts patch. Do not use external knowledge.\n"
        "Find omissions, contradictions, unsupported inference, count-scope ambiguity, and downstream-critical missing facts. "
        "Use only supplied evidence_hashes. Never output an action, patch, RFC6902 operations, Markdown, tools, or reasoning.\n"
        "Explicit source mismatch/omission may be repairable_by_llm=true; source ambiguity requires_human=true and repairable_by_llm=false.\n"
        "Return JSON matching FactsReviewModelOutput.\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    )


def build_facts_review_schema_retry_prompt(error: str) -> str:
    return "Return only valid FactsReviewModelOutput JSON. Prior schema error: " + error


def build_facts_synthesis_prompt(summary: dict) -> str:
    return "Merge only duplicate evidence-backed facts findings; do not create facts. Return FactsReviewModelOutput JSON.\n" + json.dumps(summary, ensure_ascii=False)
