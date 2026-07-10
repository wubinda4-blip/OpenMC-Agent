"""Run supervisor LLM prompt builder."""

from __future__ import annotations

import json

from openmc_agent.run_supervisor import RunSupervisorInput


def build_run_supervisor_prompt(supervisor_input: RunSupervisorInput) -> str:
    """Build the read-only workflow routing supervisor prompt."""
    allowed_action_values = sorted({a.value for a in supervisor_input.allowed_actions})
    compact = supervisor_input.model_dump(mode="json")

    return (
        "You are a read-only workflow routing supervisor.\n\n"
        "You do not modify the plan.\n"
        "You do not generate code.\n"
        "You do not execute tools.\n"
        "You do not invent benchmark facts.\n"
        "You must choose exactly one action from allowed_actions.\n"
        "Python will validate and may reject your decision.\n\n"
        "Constraints:\n"
        "- Do not choose continue_to_render if blockers exist.\n"
        "- Do not retry a patch when retry budget is zero.\n"
        "- Do not bypass human confirmation.\n"
        "- Do not request a monolithic fallback.\n"
        "- Do not repeat an action when the state has not changed.\n"
        "- Do not treat nominal material approximations as confirmed facts.\n"
        "- If the LLM repair resolved the only blocker, continue_to_render is safe.\n"
        "- If an unsafe repair was rejected and no other path exists, choose stop.\n\n"
        "When choosing retry_patch, you MUST set target_patch_type.\n"
        "The target_patch_type must be one of allowed_retry_patch_types.\n\n"
        "Return pure JSON (no markdown) with keys:\n"
        "  decision_id, action, target_patch_type (or null), rationale,\n"
        "  evidence (list of {source_type, source_id, summary}),\n"
        "  confidence (0.0-1.0), expected_state_change (or null),\n"
        "  requires_human_confirmation (bool)\n\n"
        "Allowed action values:\n"
        + json.dumps(allowed_action_values, ensure_ascii=False)
        + "\n\nSupervisor input JSON:\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
    )
