"""Strict JSON-only prompt for patch-level validation repair."""

from __future__ import annotations

import json

from .validation_repair import PatchRepairRequest


def build_patch_repair_prompt(request: PatchRepairRequest) -> str:
    payload = request.model_dump(mode="json")
    return (
        "You repair exactly one existing incremental plan patch using RFC6902 operations.\n"
        "Return ONLY a JSON object conforming to the exact template below; no markdown, prose, "
        "Python, shell, complete SimulationPlan, or complete replacement patch.\n"
        "Operations are restricted to test/add/replace/remove and the supplied allowed paths. "
        "Never modify materials density/composition, benchmark facts/constants, secrets, or "
        "nuclear-data/environment paths.\n"
        "All five top-level keys are required. Do not omit rationale. Do not omit confidence. "
        "confidence must be a number between 0.0 and 1.0.\n\n"
        "When semantic_context is present, use its count deltas and explicit usage "
        "classification rather than guessing from universe names. Do not change expected_counts "
        "and do not edit unrelated coordinate groups.\n\n"
        "Exact output template (replace placeholder values only):\n"
        "{\n"
        f'  "repair_id": {json.dumps(request.repair_id)},\n'
        f'  "target_patch_type": {json.dumps(request.target_patch_type)},\n'
        '  "operations": [\n'
        '    {"op": "replace", "path": "/allowed/path", "value": "replacement value"}\n'
        "  ],\n"
        '  "rationale": "Brief explanation of why these operations address the validator issue.",\n'
        '  "confidence": 0.0\n'
        "}\n\n"
        "Structured repair request:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        + "\n\nCopy repair_id and target_patch_type exactly from the request."
    )


def build_patch_repair_schema_correction_prompt(
    request: PatchRepairRequest,
    *,
    previous_raw_output: dict[str, object] | None,
) -> str:
    """Ask once for shape correction without inviting a new repair strategy."""
    return (
        "Your previous response failed the PatchRepairModelOutput schema. "
        "Do not change the repair strategy. Return the same intended operations in the exact "
        "required JSON shape. Return only one JSON object, with no markdown.\n\n"
        "Required envelope:\n"
        "{\n"
        f'  "repair_id": {json.dumps(request.repair_id)},\n'
        f'  "target_patch_type": {json.dumps(request.target_patch_type)},\n'
        '  "operations": [{"op": "replace", "path": "/allowed/path", "value": "replacement value"}],\n'
        '  "rationale": "Brief explanation.",\n'
        '  "confidence": 0.0\n'
        "}\n\n"
        "Previous JSON response:\n"
        + json.dumps(previous_raw_output or {}, ensure_ascii=False, sort_keys=True)
    )
