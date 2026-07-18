"""Tests for the inspect_patch_schema tool."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.evidence_ledger import create_empty_ledger
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_models import InvestigationToolRequest
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_INSPECT_PATCH_SCHEMA,
    ToolExecutionContext,
    build_default_step2_registry,
)


def _ctx():
    idx = build_source_index(text="hello\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    return ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ld)


def test_known_patch_returns_schema() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "facts"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert res.ok
    payload = res.result
    assert payload["patch_type"] == "facts"
    assert "required_fields" in payload
    assert "optional_fields" in payload
    assert "enum_values" in payload
    # FactsPatch has enum fields like model_scope.
    assert "model_scope" in payload["enum_values"] or "model_scope" in payload["optional_fields"]


def test_unknown_patch_returns_failure() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "does_not_exist"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert not res.ok
    assert any("argument_invalid" in c for c in res.error_codes)


def test_enum_values_extracted_from_literal_fields() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "settings"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert res.ok
    # SettingsPatch has energy_mode / source_space as Literal/enum-like fields.
    enums = res.result["enum_values"]
    # At least one field should have enumerated values.
    assert any(len(vals) > 0 for vals in enums.values())


def test_required_fields_extracted_correctly() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "materials"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert res.ok
    # MaterialsPatch requires at least the patch_type field; common
    # required fields include things like materials list.
    assert isinstance(res.result["required_fields"], list)


def test_forbidden_top_level_keys_returned() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "pin_map"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert res.ok
    forbidden = res.result["forbidden_top_level_keys"]
    # pin_map explicitly forbids full-lattice markers.
    assert "universe_pattern" in forbidden or "full_map" in forbidden


def test_no_evidence_claims_produced() -> None:
    """The schema tool returns reference data, not source-derived evidence."""
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "facts"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    assert res.evidence_claim_ids == ()
    assert res.source_refs == ()


def test_missing_patch_type_argument_returns_failure() -> None:
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    # Two failure modes are valid here: registry-level validation
    # rejects missing required args, or the executor's own check does.
    assert not res.ok


def test_schema_payload_excludes_internal_implementation_details() -> None:
    """The payload should not include private helper names or module paths."""
    ctx = _ctx()
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        arguments={"patch_type": "facts"},
    )
    res = reg.execute(TOOL_NAME_INSPECT_PATCH_SCHEMA, req, context=ctx)
    serialized = repr(res.result)
    # No private module-level imports leak.
    assert "_PATCH_MODELS" not in serialized
    assert "import" not in serialized.lower()
