"""Tests for the inspect_requirement_structure tool.

Reactor-neutral coverage: full core / assembly / lattice / spacer grid /
fuel enrichment / control rod / loading map / grid-size pattern.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.evidence_ledger import (
    create_empty_ledger,
    find_claims,
)
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index
from openmc_agent.plan_investigation.tool_models import InvestigationToolRequest
from openmc_agent.plan_investigation.tool_registry import (
    TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
    ToolExecutionContext,
    build_default_step2_registry,
)


def _ctx(text):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    return idx, ld, ToolExecutionContext(source_indexes={idx.document.source_id: idx}, ledger=ld)


def _run(ctx, **args):
    reg = build_default_step2_registry()
    req = InvestigationToolRequest(
        tool_name=TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
        arguments=args,
    )
    return reg.execute(TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE, req, context=ctx)


def test_detects_full_core_indicator() -> None:
    text = "The model is a full core representation.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "full_core" in indicators
    matches = find_claims(ld, predicate="scope_indicator_present", subject="model")
    assert any(c.value == "full_core" for c in matches)


def test_detects_assembly_indicator() -> None:
    text = "Build a 17x17 fuel assembly.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "assembly" in indicators


def test_detects_lattice_indicator() -> None:
    text = "Use a rectangular lattice of assemblies.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "lattice" in indicators


def test_detects_spacer_grid_indicator() -> None:
    text = "There are 8 spacer grids along the active fuel.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "spacer_grid" in indicators


def test_detects_fuel_enrichment_indicator() -> None:
    text = "Fuel enrichment is 3.5 wt%.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "fuel_enrichment" in indicators


def test_detects_control_rod_indicator() -> None:
    text = "Control rods insert from the top.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "control_rod" in indicators


def test_detects_loading_map_indicator() -> None:
    text = "The loading map places fresh fuel at the periphery.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "loading_map" in indicators


def test_grid_size_pattern_n_by_n() -> None:
    text = "The core is a 3 by 3 lattice.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert res.result["grid_sizes"] == [
        {"rows": 3, "cols": 3, "line": 1, "match_text": "3 by 3"}
    ]


def test_grid_size_pattern_nxn() -> None:
    text = "Build a 17x17 assembly.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert any(g["rows"] == 17 and g["cols"] == 17 for g in res.result["grid_sizes"])


def test_grid_size_pattern_unicode_x() -> None:
    text = "Core has 3×3 layout.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert any(g["rows"] == 3 and g["cols"] == 3 for g in res.result["grid_sizes"])


def test_does_not_emit_model_scope_claim() -> None:
    """The tool must NOT directly claim model_scope='multi_assembly'; that
    is Facts Gate's job.  It only records scope-indicator presence.
    """
    text = "The full core has a 3x3 lattice of assemblies.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    scope_claims = find_claims(ld, predicate="model_scope")
    assert scope_claims == []
    # Scope-indicator claims ARE allowed.
    indicator_claims = find_claims(ld, predicate="scope_indicator_present")
    assert len(indicator_claims) >= 1


def test_grid_size_dedup() -> None:
    text = "3x3 mentioned twice. 3x3 again.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert len(res.result["grid_sizes"]) == 1


def test_no_indicators_for_unrelated_text() -> None:
    text = "Hello world.\nNothing relevant here.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert res.result["scope_indicators"] == []
    assert res.result["grid_sizes"] == []
    assert res.evidence_claim_ids == ()


def test_each_indicator_hit_has_source_ref() -> None:
    text = "Full core model.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    assert len(res.source_refs) >= 1
    # Each ref resolves against the index.
    for ref in res.source_refs:
        idx.validate_source_ref(ref)


def test_custom_keyword_groups_override() -> None:
    text = "Custom keyword demo.\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx, keyword_groups={"custom_indicator": ("custom keyword",)})
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "custom_indicator" in indicators


def test_chinese_requirement_text() -> None:
    text = "# 反应堆\n本模型为全堆芯（full core）模型。\n"
    idx, ld, ctx = _ctx(text)
    res = _run(ctx)
    # Even though the surrounding text is Chinese, the English phrase
    # "full core" is detectable.
    indicators = {i["indicator"] for i in res.result["scope_indicators"]}
    assert "full_core" in indicators
