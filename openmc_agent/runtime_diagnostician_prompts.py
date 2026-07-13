"""Prompts for the LLM runtime diagnostician."""

from __future__ import annotations

import json
from typing import Any


def build_runtime_diagnosis_prompt(diagnosis_input: dict[str, Any]) -> str:
    """Build the diagnostician prompt from structured evidence."""
    failure = diagnosis_input.get("failure", {})
    evidence = diagnosis_input.get("evidence", [])

    return f"""You are a runtime failure diagnostician for an OpenMC reactor model agent.

Your job: diagnose the root cause of the runtime failure below. You do NOT modify
the plan. You only return a structured diagnosis.

RULES:
- You may only choose target_patch_type from the policy candidate list.
- You may only reference paths that exist in the hard evidence.
- You may NOT change the deterministic classification.
- You may NOT invent material compositions, radii, z-bounds, or coordinates.
- If you cannot uniquely identify the owning patch and a safe repair, return
  disposition="no_safe_repair".
- Environment/nuclear-data errors must NOT be reclassified as plan-fixable.
- Every claim must reference an evidence_id.

Return JSON matching the RuntimeDiagnosis schema.

FAILURE:
{json.dumps(failure, indent=2, ensure_ascii=False)[:2000]}

EVIDENCE:
{json.dumps(evidence, indent=2, ensure_ascii=False)[:3000]}

max_mutating_operations: {diagnosis_input.get('max_mutating_operations', 4)}
"""


def build_runtime_diagnosis_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the diagnosis response."""
    from openmc_agent.runtime_diagnostician import RuntimeDiagnosis
    return RuntimeDiagnosis.model_json_schema()
