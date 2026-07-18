"""Atomic writer for the per-session ``investigation_session.json`` artifact.

Records one entry per investigation session under
``<output_dir>/workflow/investigation/investigation_session.json``.

What gets recorded:

* ``session_id``, ``patch_type``, ``caller_stage``
* ``tool_calls`` (compact records: tool_name, hashes, claim ids)
* ``evidence_claim_ids`` (sorted)
* ``budget`` (the supplied :class:`InvestigationBudget`)
* ``budget_used`` (snapshot at end of session)
* ``blocked``, ``block_code`` (None when completed normally)
* ``warnings`` (sorted)
* ``result_hash``

What NEVER gets recorded:

* LLM prompts.
* ``reasoning_content`` or any other LLM-internal field.
* API keys or any environment variable.
* Host file system paths.
* Full source document bodies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openmc_agent.schemas import AgentBaseModel

from .agent import InvestigationResult
from .errors import PlanInvestigationIssue

__all__ = [
    "InvestigationSessionRecord",
    "write_investigation_session_artifact",
    "SESSION_ARTIFACT_RELPATH",
]


SESSION_ARTIFACT_RELPATH: str = "workflow/investigation/investigation_session.json"


class InvestigationSessionRecord(AgentBaseModel):
    """Compact, hash-stable record of one investigation session."""

    session_id: str
    patch_type: str
    caller_stage: str = "investigation"
    tool_calls: list[dict[str, Any]] = []  # ToolCallRecord.model_dump()
    evidence_claim_ids: tuple[str, ...] = ()
    budget: dict[str, Any] = {}
    budget_used: dict[str, Any] = {}
    completed: bool = False
    blocked: bool = False
    block_code: str | None = None
    warnings: tuple[str, ...] = ()
    result_hash: str


def _to_record(result: InvestigationResult) -> InvestigationSessionRecord:
    return InvestigationSessionRecord(
        session_id=result.session_id,
        patch_type=result.patch_type,
        tool_calls=[tc.model_dump(mode="json") for tc in result.tool_calls],
        evidence_claim_ids=tuple(sorted(result.evidence_claim_ids)),
        budget=result.budget.model_dump(mode="json"),
        budget_used=result.budget_used.model_dump(mode="json"),
        completed=result.completed,
        blocked=result.blocked,
        block_code=result.block_code,
        warnings=tuple(sorted(result.warnings)),
        result_hash=result.result_hash,
    )


def write_investigation_session_artifact(
    *,
    output_dir: Path,
    result: InvestigationResult,
) -> Path:
    """Write ``investigation_session.json`` atomically.

    Raises :class:`PlanInvestigationIssue` on serialisation / I/O failure.
    Never silently swallows.
    """

    record = _to_record(result)
    try:
        payload = record.model_dump(mode="json")
        text = json.dumps(
            {"artifact_version": "0.1", "session": payload},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PlanInvestigationIssue(
            "plan_investigation.session_artifact_serialization_failed",
            "failed to serialize investigation session",
            details={"error": str(exc)},
        ) from exc

    target = Path(output_dir) / SESSION_ARTIFACT_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise PlanInvestigationIssue(
            "plan_investigation.session_artifact_write_failed",
            "failed to write investigation session artifact",
            details={"target": str(target), "error": str(exc)},
        ) from exc
    return target
