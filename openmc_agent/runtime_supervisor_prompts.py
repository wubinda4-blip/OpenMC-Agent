"""Compact prompt for the post-execution runtime supervisor."""

from __future__ import annotations

import json

from openmc_agent.runtime_supervisor import RuntimeSupervisorInput


def build_runtime_supervisor_prompt(value: RuntimeSupervisorInput) -> str:
    return (
        "Choose exactly one allowed post-execution action. Do not modify plans, "
        "budgets, classifications, or patch content. Environment/human blockers "
        "cannot be repaired. Return JSON only.\n\n"
        + json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
    )
