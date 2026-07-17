"""Stable SHA-256 fingerprints for the plan closed-loop protocol."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=canonical_json_dumps)
    return value


def canonical_json_dumps(value: Any) -> str:
    """Serialize JSON values independently of mapping insertion order."""
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_dumps(value).encode("utf-8")).hexdigest()


def compute_source_excerpt_hash(
    source_path: str | None, line_start: int | None, line_end: int | None, text: str,
) -> str:
    return _digest({"source_path": source_path, "line_start": line_start, "line_end": line_end, "text": text})


def compute_finding_fingerprint(
    *, gate_id: str, code: str, category: str, affected_patch_types: list[str],
    affected_json_paths: list[str], source_evidence_hashes: list[str] | None = None,
) -> str:
    return _digest({
        "gate_id": gate_id, "code": code, "category": category,
        "affected_patch_types": list(affected_patch_types),
        "affected_json_paths": list(affected_json_paths),
        "source_evidence_hashes": list(source_evidence_hashes or []),
    })


def compute_issue_fingerprint(
    *, gate_id: str, code: str, affected_patch_type: str | None = None,
    json_path: str | None = None, expected: Any = None, actual: Any = None,
) -> str:
    """Fingerprint stable issue semantics, intentionally excluding prose messages."""
    return _digest({
        "gate_id": gate_id, "code": code, "affected_patch_type": affected_patch_type,
        "json_path": json_path, "expected": expected, "actual": actual,
    })


def compute_candidate_hash(*, target_patch_type: str, candidate_patch: Any) -> str:
    return _digest({"target_patch_type": target_patch_type, "candidate_patch": candidate_patch})


def compute_evidence_pack_hash(value: Any) -> str:
    payload = _jsonable(value)
    if isinstance(payload, dict):
        payload.pop("input_hash", None)
        payload.pop("evidence_pack_id", None)
    return _digest(payload)


def compute_stage_state_hash(value: Any) -> str:
    payload = _jsonable(value)
    if isinstance(payload, dict):
        for key in ("started_at", "updated_at", "completed_at"):
            payload.pop(key, None)
    return _digest(payload)
