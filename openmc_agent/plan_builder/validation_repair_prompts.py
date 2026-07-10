"""Strict JSON-only prompt for patch-level validation repair."""

from __future__ import annotations

import json

from .validation_repair import PatchRepairRequest


def build_patch_repair_prompt(request: PatchRepairRequest) -> str:
    payload = request.model_dump(mode="json")
    return (
        "You repair exactly one existing incremental plan patch using RFC6902 operations.\n"
        "Return ONLY a JSON object conforming to PatchRepairProposal; no markdown, prose, "
        "Python, shell, complete SimulationPlan, or complete replacement patch.\n"
        "Operations are restricted to test/add/replace/remove and the supplied allowed paths. "
        "Never modify materials density/composition, benchmark facts/constants, secrets, or "
        "nuclear-data/environment paths.\n\n"
        "Structured repair request:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        + "\n\nThe proposal target_patch_type must exactly match the request."
    )
