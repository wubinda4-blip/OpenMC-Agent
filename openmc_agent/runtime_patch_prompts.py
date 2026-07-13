"""Prompts for the constrained LLM runtime patch proposer."""

from __future__ import annotations

import json
from typing import Any


def build_runtime_patch_proposal_prompt(proposal_input: dict[str, Any]) -> str:
    """Build the proposer prompt from a validated diagnosis + target patch."""
    allowed = proposal_input.get("allowed_paths", [])
    forbidden = proposal_input.get("forbidden_paths", [])
    current_patch = proposal_input.get("current_patch", {})

    return f"""You are a constrained patch proposer for OpenMC runtime repair.

You receive a validated diagnosis and the current target patch. You output
RFC6902 operations to fix the runtime failure.

RULES:
- Return JSON only, matching the LLMRuntimeRepairProposal schema.
- Every path MUST be in the allowed_paths list.
- NO path may touch forbidden_paths or protected scientific facts.
- Every replacement value must already appear in the supplied hard evidence.
- Do NOT use epsilon, averages, or invented geometry values.
- Mutating operations (add/replace/remove) must be preceded by a test op.
- Maximum {proposal_input.get('max_mutating_operations', 4)} mutating operations.
- If no safe operation exists, return operations=[].

ALLOWED PATHS:
{json.dumps(allowed, indent=2)}

FORBIDDEN PATHS:
{json.dumps(forbidden, indent=2)}

CURRENT TARGET PATCH ({proposal_input.get('target_patch_type', 'unknown')}):
{json.dumps(current_patch, indent=2, ensure_ascii=False)[:2000]}

REPAIR KIND: {proposal_input.get('repair_kind', 'unknown')}
"""


def build_runtime_patch_proposal_json_schema() -> dict[str, Any]:
    """Return the JSON schema for the proposal response."""
    from openmc_agent.runtime_patch_proposer import LLMRuntimeRepairProposal
    return LLMRuntimeRepairProposal.model_json_schema()
