"""Tests for the InvestigationToolRegistry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import (
    PlanningEvidenceLedger,
    create_empty_ledger,
)
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_artifacts import ToolCallLedger, record_tool_call, write_tool_call_artifact
from openmc_agent.plan_investigation.tool_models import (
    InvestigationToolRequest,
    InvestigationToolResult,
    InvestigationToolSpec,
    ToolCapability,
)
from openmc_agent.plan_investigation.tool_registry import (
    InvestigationToolRegistry,
    ToolExecutionContext,
    build_default_step2_registry,
)


def test_default_registry_lists_four_tools() -> None:
    reg = build_default_step2_registry()
    names = {spec.name for spec in reg.list_tools()}
    assert names == {
        "search_source_index",
        "inspect_requirement_structure",
        "inspect_patch_schema",
        "query_evidence_ledger",
    }


def test_registry_get_returns_spec() -> None:
    reg = build_default_step2_registry()
    spec = reg.get("search_source_index")
    assert spec.capability == ToolCapability.SOURCE_SEARCH


def test_registry_get_unknown_raises() -> None:
    reg = build_default_step2_registry()
    with pytest.raises(PlanInvestigationIssue):
        reg.get("does_not_exist")


def test_register_rejects_duplicate_name() -> None:
    reg = build_default_step2_registry()
    spec = reg.get("search_source_index")
    # Try to register a different executor under the same name.
    def other_executor(ctx, req):  # noqa: ANN001
        return InvestigationToolResult(ok=True, tool_name="search_source_index", result={})

    with pytest.raises(PlanInvestigationIssue):
        reg.register(spec, other_executor)


def test_register_rejects_reserved_capability() -> None:
    """REPOSITORY_INSPECTION is reserved and must be rejected.  The spec
    validator catches this at construction time (so the registry never
    sees the bad spec), and a separate registry-level check would also
    catch it if a spec slipped through.
    """
    with pytest.raises((PlanInvestigationIssue, ValidationError)):
        InvestigationToolSpec(
            name="repo_grep",
            description="bogus",
            capability=ToolCapability.REPOSITORY_INSPECTION,
        )


def test_validate_arguments_missing_required() -> None:
    reg = build_default_step2_registry()
    issues = reg.validate_arguments("search_source_index", {})
    assert any("missing" in issue.code or "argument" in issue.code for issue in issues)


def test_validate_arguments_unknown_key_rejected() -> None:
    reg = build_default_step2_registry()
    issues = reg.validate_arguments(
        "search_source_index",
        {"query": "x", "unexpected_key": 1},
    )
    assert any("unknown" in issue.code for issue in issues)


def test_execute_unknown_tool_name_returns_failure_result() -> None:
    reg = build_default_step2_registry()
    idx = build_source_index(text="hello\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    ctx = ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ledger)
    req = InvestigationToolRequest(
        tool_name="nonexistent_tool",
        arguments={},
    )
    with pytest.raises(PlanInvestigationIssue):
        reg.execute("nonexistent_tool", req, context=ctx)


def test_execute_with_request_tool_name_mismatch_raises() -> None:
    reg = build_default_step2_registry()
    idx = build_source_index(text="hello\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ledger = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    ctx = ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ledger)
    req = InvestigationToolRequest(tool_name="other_tool", arguments={"query": "x"})
    with pytest.raises(PlanInvestigationIssue):
        reg.execute("search_source_index", req, context=ctx)


def test_list_tools_filtered_by_capability() -> None:
    reg = build_default_step2_registry()
    source_search_tools = reg.list_tools(capability=ToolCapability.SOURCE_SEARCH)
    assert len(source_search_tools) == 1
    assert source_search_tools[0].name == "search_source_index"


def test_step2_does_not_register_repository_inspection_tool() -> None:
    reg = build_default_step2_registry()
    capabilities = {spec.capability for spec in reg.list_tools()}
    assert ToolCapability.REPOSITORY_INSPECTION not in capabilities
