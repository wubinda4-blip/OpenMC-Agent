"""Issue-scoped RFC6902 request for the Placement Revision Agent."""

from __future__ import annotations

import json
from typing import Any

from .models import PlacementRevisionProposal


def build_placement_revision_prompt(*, patches: dict[str, dict[str, Any]], findings: list[dict[str, Any]], evidence_pack: dict[str, Any], allowed_paths: dict[str, list[str]], confirmed_records: list[dict[str, Any]]) -> str:
    return """You are the Placement Revision Agent. Return one strict JSON RFC6902 proposal only.
Repair only the listed placement findings. Do not modify Facts, Materials, Universes, axial patches, settings, or a patch root. Do not change protected fields or any path outside the exact issue-scoped allowlist. Do not repair a requires_human finding. Use only supplied evidence and confirmed records.
""" + json.dumps({"schema": PlacementRevisionProposal.model_json_schema(), "patches": patches, "findings": findings, "evidence_pack": evidence_pack, "allowed_paths": allowed_paths, "confirmed_records": confirmed_records}, ensure_ascii=False, sort_keys=True)
