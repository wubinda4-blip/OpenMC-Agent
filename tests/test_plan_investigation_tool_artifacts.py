"""Tests for the tool-call artifact writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_artifacts import (
    TOOL_CALLS_ARTIFACT_RELPATH,
    ToolCallLedger,
    record_tool_call,
    write_tool_call_artifact,
)
from openmc_agent.plan_investigation.tool_models import InvestigationToolRequest, InvestigationToolResult
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_SEARCH_SOURCE_INDEX,
    ToolExecutionContext,
    build_default_step2_registry,
)


def _ctx():
    idx = build_source_index(text="alpha\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    return idx, ld, ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ld)


def test_tool_calls_json_written(tmp_path: Path) -> None:
    idx, ld, ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    tc_ledger = ToolCallLedger()
    record_tool_call(tc_ledger, tool_name=res.tool_name, arguments=req.arguments, result=res)
    path = write_tool_call_artifact(output_dir=tmp_path, ledger=tc_ledger)
    assert path.exists()
    assert path == tmp_path / TOOL_CALLS_ARTIFACT_RELPATH


def test_payload_sorted_and_stable(tmp_path: Path) -> None:
    """Sorted output: same calls in different orders produce same file bytes."""
    idx, ld, ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res1 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    res2 = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)

    ledger_a = ToolCallLedger()
    record_tool_call(ledger_a, tool_name="search_source_index", arguments=req.arguments, result=res1)
    record_tool_call(ledger_a, tool_name="search_source_index", arguments=req.arguments, result=res2)

    ledger_b = ToolCallLedger()
    record_tool_call(ledger_b, tool_name="search_source_index", arguments=req.arguments, result=res2)
    record_tool_call(ledger_b, tool_name="search_source_index", arguments=req.arguments, result=res1)

    path_a = write_tool_call_artifact(output_dir=tmp_path / "a", ledger=ledger_a)
    path_b = write_tool_call_artifact(output_dir=tmp_path / "b", ledger=ledger_b)
    # Dedup means both ledgers have one record; content matches.
    assert path_a.read_text() == path_b.read_text()


def test_dedup_same_call_twice_kept_once() -> None:
    res = InvestigationToolResult(
        ok=True, tool_name="search_source_index", result={"x": 1}
    )
    ledger = ToolCallLedger()
    rec1 = record_tool_call(ledger, tool_name="t", arguments={"q": "x"}, result=res)
    rec2 = record_tool_call(ledger, tool_name="t", arguments={"q": "x"}, result=res)
    assert len(ledger.records) == 1
    assert rec1 == rec2


def test_artifact_excludes_prompts_and_paths(tmp_path: Path) -> None:
    idx, ld, ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    ledger = ToolCallLedger()
    record_tool_call(ledger, tool_name=res.tool_name, arguments=req.arguments, result=res)
    path = write_tool_call_artifact(output_dir=tmp_path, ledger=ledger)
    text = path.read_text(encoding="utf-8")
    # No API keys, no home paths, no prompt content.
    assert "DEEPSEEK_API_KEY" not in text
    assert "SENSENOVA_API_KEY" not in text
    assert "/home/" not in text
    assert "prompt" not in text.lower()


def test_artifact_atomic_failure_raises(tmp_path: Path) -> None:
    """If a record contains a non-JSON value, the write must raise."""
    ledger = ToolCallLedger()
    # Inject a non-JSON value into a record's evidence_claim_ids via
    # direct attribute set (bypassing Pydantic revalidation).
    res = InvestigationToolResult(
        ok=True, tool_name="t", result={"x": 1}
    )
    rec = record_tool_call(ledger, tool_name="t", arguments={}, result=res)
    object.__setattr__(rec, "caller_stage", object())  # non-JSON value
    with pytest.raises(PlanInvestigationIssue):
        write_tool_call_artifact(output_dir=tmp_path, ledger=ledger)


def test_artifact_includes_only_audit_fields(tmp_path: Path) -> None:
    """Records contain ONLY: tool_name, arguments_hash, result_hash,
    evidence_claim_ids, caller_stage, ok, error_codes.  No arguments
    body, no result body.
    """
    idx, ld, ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        arguments={"query": "alpha"},
    )
    res = reg.execute(TOOL_NAME_SEARCH_SOURCE_INDEX, req, context=ctx)
    ledger = ToolCallLedger()
    record_tool_call(ledger, tool_name=res.tool_name, arguments=req.arguments, result=res)
    path = write_tool_call_artifact(output_dir=tmp_path, ledger=ledger)
    payload = json.loads(path.read_text(encoding="utf-8"))
    rec = payload["records"][0]
    assert set(rec.keys()) == {
        "tool_name",
        "arguments_hash",
        "result_hash",
        "evidence_claim_ids",
        "caller_stage",
        "ok",
        "error_codes",
    }
    # Specifically: no "result" body, no "arguments" body.
    assert "result" not in rec
    assert "arguments" not in rec
    assert "excerpt" not in rec
