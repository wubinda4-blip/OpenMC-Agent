"""Artifact writer for planning-investigation tool calls.

Records each tool invocation as a compact, hash-stable entry under
``<output_dir>/workflow/investigation/tool_calls.json``.  Entries are
appended across calls; callers can write the file once at the end of an
orchestration run.

What gets recorded:

* ``tool_name``
* ``arguments_hash`` (deterministic SHA-256 over canonical JSON of arguments)
* ``result_hash`` (the tool's own ``execution_hash``)
* ``evidence_claim_ids`` (sorted; never the claim bodies themselves)
* ``caller_stage``

What NEVER gets recorded:

* Prompt text or reasoning.
* API keys or any environment variable.
* Host file system paths.
* Full source document bodies.
* Full result payloads (the per-result ``execution_hash`` is enough for
  audit; downstream consumers can recompute the payload if they need to).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic import PrivateAttr

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .hashing import content_hash
from .tool_models import InvestigationToolResult

__all__ = [
    "ToolCallRecord",
    "ToolCallLedger",
    "record_tool_call",
    "write_tool_call_artifact",
    "TOOL_CALLS_ARTIFACT_RELPATH",
]


TOOL_CALLS_ARTIFACT_RELPATH: str = "workflow/investigation/tool_calls.json"


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------


class ToolCallRecord(AgentBaseModel):
    """One tool-call audit entry.  Hash-stable, secret-free."""

    tool_name: str
    arguments_hash: str
    result_hash: str
    evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    caller_stage: str = "investigation"
    ok: bool = True
    error_codes: tuple[str, ...] = Field(default_factory=tuple)


class ToolCallLedger(AgentBaseModel):
    """In-memory accumulator for tool-call records.

    A small mutable container that the orchestrator owns locally.  It
    is NOT a singleton; each orchestration run gets its own instance.
    """

    records: list[ToolCallRecord] = Field(default_factory=list)
    _seen_hashes: set[str] = PrivateAttr(default_factory=set)

    def add(self, record: ToolCallRecord) -> bool:
        """Add a record.  Returns True if added, False if it was a
        duplicate (same ``arguments_hash`` + ``result_hash`` + tool_name).
        """

        key = f"{record.tool_name}|{record.arguments_hash}|{record.result_hash}"
        if key in self._seen_hashes:
            return False
        self._seen_hashes.add(key)
        self.records.append(record)
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_tool_call(
    ledger: ToolCallLedger,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: InvestigationToolResult,
    caller_stage: str = "investigation",
) -> ToolCallRecord:
    """Append a :class:`ToolCallRecord` to ``ledger`` and return it."""

    record = ToolCallRecord(
        tool_name=tool_name,
        arguments_hash=content_hash(arguments),
        result_hash=result.execution_hash,
        evidence_claim_ids=tuple(sorted(result.evidence_claim_ids)),
        caller_stage=caller_stage,
        ok=result.ok,
        error_codes=tuple(result.error_codes),
    )
    ledger.add(record)
    return record


def write_tool_call_artifact(
    *,
    output_dir: Path,
    ledger: ToolCallLedger,
) -> Path:
    """Write ``workflow/investigation/tool_calls.json`` atomically.

    The output is a JSON array of records sorted by
    ``(tool_name, arguments_hash, result_hash)`` for deterministic diff.
    Raises :class:`PlanInvestigationIssue` on serialisation or I/O
    failure; never silently swallows.
    """

    if not isinstance(ledger, ToolCallLedger):
        raise TypeError("ledger must be a ToolCallLedger instance")

    target = Path(output_dir) / TOOL_CALLS_ARTIFACT_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    try:
        records_payload: list[dict[str, Any]] = []
        for rec in sorted(
            ledger.records,
            key=lambda r: (r.tool_name, r.arguments_hash, r.result_hash),
        ):
            try:
                records_payload.append(rec.model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001
                raise PlanInvestigationIssue(
                    "plan_investigation.tool_artifact_serialization_failed",
                    "failed to serialize tool-call record",
                    details={"error": f"{type(exc).__name__}: {exc}"},
                ) from exc
        text = json.dumps(
            {
                "artifact_version": "0.1",
                "record_count": len(records_payload),
                "records": records_payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PlanInvestigationIssue(
            "plan_investigation.tool_artifact_serialization_failed",
            "failed to serialize tool-call ledger",
            details={"error": str(exc)},
        ) from exc
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
            "plan_investigation.tool_artifact_write_failed",
            "failed to write tool-call artifact",
            details={"target": str(target), "error": str(exc)},
        ) from exc
    return target
