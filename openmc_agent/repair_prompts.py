from __future__ import annotations

import json

from openmc_agent.repair_proposal import RepairProposalInput


def build_repair_proposal_prompt(repair_input: RepairProposalInput) -> str:
    payload = _safe_payload(repair_input.model_dump(mode="json"))
    return (
        "You are a repair proposal generator.\n"
        "You do not execute code. You do not generate Python. You do not output shell commands. "
        "You do not modify protected scientific facts. You only produce JSON Patch operations inside the provided allowlist. "
        "If no safe repair is possible, return an empty operations list and explain why in rationale.\n\n"
        "Hard rules:\n"
        "- Return JSON only: no markdown, no code fences, no prose outside JSON.\n"
        "- Do not use paths outside allowed_paths.\n"
        "- Do not modify density, concentration, enrichment, isotopic composition, nuclear data paths, cross-section paths, secrets, or benchmark constants.\n"
        "- Do not invent benchmark facts or physical constants.\n"
        "- If a repair requires real physical facts or human confirmation, return operations=[] and requires_human_confirmation=true.\n"
        "- Propose the smallest patch: prefer one or a few operations.\n"
        "- Do not replace an entire plan or large array unless explicitly allowlisted.\n\n"
        "Output contract:\n"
        "{\n"
        '  "proposal_id": "...",\n'
        '  "source_issue_codes": ["..."],\n'
        '  "source_audit_finding_codes": ["..."],\n'
        '  "rationale": "...",\n'
        '  "expected_effect": "...",\n'
        '  "operations": [{"op": "replace", "path": "/...", "value": "..."}],\n'
        '  "suggested_patch_target": "axial_overlays",\n'
        '  "requires_human_confirmation": false,\n'
        '  "confidence": 0.82\n'
        "}\n\n"
        "Repair input JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _safe_payload(value):
    if isinstance(value, str):
        return value.replace("ghp_", "<redacted>")[:2000]
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            lowered = str(key).lower()
            if any(term in lowered for term in ("secret", "token", "api_key", "password")):
                continue
            out[key] = _safe_payload(child)
        return out
    if isinstance(value, list):
        if value and all(isinstance(item, list) for item in value):
            return {"matrix_rows": len(value), "matrix_cols": max((len(item) for item in value), default=0), "omitted": "expanded matrix omitted"}
        return [_safe_payload(item) for item in value[:20]]
    return value
