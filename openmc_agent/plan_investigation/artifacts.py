"""Atomic, deterministic artifact writing for plan investigation.

Step 1 artifacts are written under ``<output_dir>/workflow/investigation/``:

* ``source_manifest.json`` — per-document metadata.
* ``source_index.json`` — sections + per-line hashes per source.
* ``evidence_ledger.json`` — full ledger.
* ``evidence_conflicts.json`` — conflicts list (may be empty).
* ``unresolved_claims.json`` — claims whose status is ``unresolved``.
* ``investigation_summary.json`` — :class:`EvidenceLedgerSummary` roll-up.

Failures raise :class:`PlanInvestigationIssue` rather than being swallowed.
Atomic writes (``tmp`` + ``replace``) ensure partial artifacts are never
observed on disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    ledger_summary,
    recompute_ledger_hash,
)
from .models import EvidenceStatus
from .source_index import SourceIndex

__all__ = [
    "write_plan_investigation_artifacts",
    "INVESTIGATION_ARTIFACT_DIR",
]


INVESTIGATION_ARTIFACT_DIR: str = "workflow/investigation"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, value: Any) -> None:
    """Write ``value`` as canonical JSON to ``path`` atomically.

    Canonical form: UTF-8, ``ensure_ascii=False``, ``indent=2``,
    ``sort_keys=True``, ``allow_nan=False``.  Raises on any I/O or
    serialization error; never silently swallows.
    """

    payload = _to_jsonable(value)
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlanInvestigationIssue(
            "plan_investigation.artifact_serialization_failed",
            "failed to canonical-serialize artifact",
            details={"target": str(path), "error": f"{type(exc).__name__}: {exc}"},
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        # Best-effort cleanup of the tmp file on failure.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise PlanInvestigationIssue(
            "plan_investigation.artifact_write_failed",
            "failed to write artifact atomically",
            details={"target": str(path), "error": str(exc)},
        ) from exc


def _to_jsonable(value: Any) -> Any:
    """Recursively convert pydantic models / tuples into JSON-compatible
    primitives.  Raises :class:`PlanInvestigationIssue` if a value cannot be
    converted (e.g. raw ``object()`` instance in a metadata dict).
    """

    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - surface as PlanInvestigationIssue
            raise PlanInvestigationIssue(
                "plan_investigation.artifact_serialization_failed",
                "model_dump failed during artifact serialization",
                details={"error": f"{type(exc).__name__}: {exc}"},
            ) from exc
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def write_plan_investigation_artifacts(
    *,
    output_dir: Path,
    source_indexes: list[SourceIndex],
    ledger: PlanningEvidenceLedger,
) -> dict[str, Path]:
    """Write all Step 1 plan-investigation artifacts.

    Returns a mapping of artifact name -> on-disk path.  Raises on any
    serialization or I/O failure.
    """

    if not source_indexes:
        raise PlanInvestigationIssue(
            "plan_investigation.artifact_write_failed",
            "write_plan_investigation_artifacts requires at least one source index",
        )

    out_root = Path(output_dir) / INVESTIGATION_ARTIFACT_DIR

    # Verify ledger hash BEFORE writing so a tampered ledger never reaches disk.
    recomputed = recompute_ledger_hash(ledger)
    if ledger.ledger_hash and ledger.ledger_hash != recomputed:
        raise PlanInvestigationIssue(
            "plan_investigation.ledger_hash_mismatch",
            "refusing to write artifacts for a ledger whose hash is stale",
            details={"expected": recomputed, "actual": ledger.ledger_hash},
        )

    # 1. source_manifest.json — per-document metadata only.
    manifest = {
        "artifact_version": "0.1",
        "sources": [
            {
                "source_id": idx.document.source_id,
                "source_kind": idx.document.source_kind.value,
                "title": idx.document.title,
                "origin_label": idx.document.origin_label,
                "line_count": idx.document.line_count,
                "char_count": idx.document.char_count,
                "section_count": idx.document.section_count,
                "content_hash": idx.document.content_hash,
                "normalized_content_hash": idx.document.normalized_content_hash,
                "index_hash": idx.index_hash,
                "index_version": idx.index_version,
                "metadata": _to_jsonable(idx.document.metadata),
            }
            for idx in source_indexes
        ],
    }

    # 2. source_index.json — sections + per-line hashes per source.  Line
    # *text* is included so the artifact is self-contained for review; this
    # is acceptable because the source IS the user's problem statement
    # (already user-supplied data, not a host secret).
    indexes_payload = {
        "artifact_version": "0.1",
        "indexes": [
            {
                "source_id": idx.document.source_id,
                "index_hash": idx.index_hash,
                "index_version": idx.index_version,
                "sections": [
                    {
                        "section_id": s.section_id,
                        "heading": s.heading,
                        "level": s.level,
                        "section_path": list(s.section_path),
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "parent_section_id": s.parent_section_id,
                        "content_hash": s.content_hash,
                    }
                    for s in idx.sections
                ],
                "line_records": [
                    {
                        "line_number": rec.line_number,
                        "text": rec.text,
                        "line_hash": rec.line_hash,
                    }
                    for rec in idx.line_records
                ],
            }
            for idx in source_indexes
        ],
    }

    # 3. evidence_ledger.json — the full ledger (model_dump).
    ledger_payload = _to_jsonable(ledger)

    # 4. evidence_conflicts.json — conflicts only.
    conflicts_payload = {
        "artifact_version": "0.1",
        "conflicts": [
            _to_jsonable(c)
            for c in sorted(ledger.conflicts.values(), key=lambda c: c.conflict_id)
        ],
    }

    # 5. unresolved_claims.json — claims whose status is unresolved.
    unresolved = sorted(
        (
            claim
            for claim in ledger.claims.values()
            if claim.status == EvidenceStatus.UNRESOLVED
        ),
        key=lambda c: c.claim_id,
    )
    unresolved_payload = {
        "artifact_version": "0.1",
        "unresolved_claim_ids": [c.claim_id for c in unresolved],
        "claims": [_to_jsonable(c) for c in unresolved],
    }

    # 6. investigation_summary.json.
    summary = ledger_summary(ledger)
    summary_payload = {
        "artifact_version": "0.1",
        "ledger_hash": ledger.ledger_hash or recomputed,
        "requirement_hash": ledger.requirement_hash,
        "summary": _to_jsonable(summary),
    }

    artifacts: dict[str, Path] = {
        "source_manifest": out_root / "source_manifest.json",
        "source_index": out_root / "source_index.json",
        "evidence_ledger": out_root / "evidence_ledger.json",
        "evidence_conflicts": out_root / "evidence_conflicts.json",
        "unresolved_claims": out_root / "unresolved_claims.json",
        "investigation_summary": out_root / "investigation_summary.json",
    }

    _atomic_write_json(artifacts["source_manifest"], manifest)
    _atomic_write_json(artifacts["source_index"], indexes_payload)
    _atomic_write_json(artifacts["evidence_ledger"], ledger_payload)
    _atomic_write_json(artifacts["evidence_conflicts"], conflicts_payload)
    _atomic_write_json(artifacts["unresolved_claims"], unresolved_payload)
    _atomic_write_json(artifacts["investigation_summary"], summary_payload)

    return artifacts
