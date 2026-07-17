"""Strict JSON Patch prompt for the facts-only revision role."""

from __future__ import annotations

import json


def build_facts_revision_prompt(*, facts_patch: dict, findings: list[dict], evidence: list[dict], allowed_paths: list[str], confirmed_facts: dict) -> str:
    return (
        "You are the Facts Revision Agent. Output only a JSON FactsRevisionProposal. "
        "Use add/replace/remove RFC6902 operations only; do not alter patch_type, root, unlisted paths, "
        "or confirmed facts. Do not infer facts absent from evidence. Requires-human findings are not repairable.\n"
        + json.dumps({"facts_patch": facts_patch, "findings": findings, "evidence": evidence,
                      "allowed_paths": allowed_paths, "confirmed_facts": confirmed_facts}, ensure_ascii=False)
    )
