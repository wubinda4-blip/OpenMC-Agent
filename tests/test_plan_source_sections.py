"""Tests for Markdown section indexing."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import build_source_index


def _sections(idx):
    return [(s.heading, s.level, s.start_line, s.end_line) for s in idx.sections]


def test_atx_heading_hierarchy() -> None:
    text = "# Top\nintro\n## Child\nbody\n### Grandchild\ndeep\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    headings = [(s.heading, s.level) for s in idx.sections if s.level > 0]
    assert ("Top", 1) in headings
    assert ("Child", 2) in headings
    assert ("Grandchild", 3) in headings


def test_section_for_line_returns_innermost() -> None:
    text = "# A\nx\n## A1\ny\n# B\nz\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    # Line 4 ('y') belongs to section A1.
    section = idx.section_for_line(4)
    assert section.heading == "A1"
    assert section.level == 2
    # Line 6 ('z') belongs to section B.
    section_b = idx.section_for_line(6)
    assert section_b.heading == "B"
    assert section_b.level == 1


def test_repeated_headings_get_distinct_section_ids() -> None:
    text = "# X\na\n# X\nb\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    same_heading = [s for s in idx.sections if s.heading == "X"]
    assert len(same_heading) == 2
    # Different line ranges => different section_ids.
    assert same_heading[0].section_id != same_heading[1].section_id
    assert same_heading[0].start_line == 1
    assert same_heading[1].start_line == 3


def test_fence_blocks_hash_headings() -> None:
    text = "# Real\n\n```\n# not a heading\n## also not\n```\n\n## After\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    headings = [s.heading for s in idx.sections if s.level > 0]
    assert "Real" in headings
    assert "After" in headings
    assert "not a heading" not in headings
    assert "also not" not in headings


def test_tilde_fence_also_blocks() -> None:
    text = "# H\n~~~\n# inside tilde\n~~~\n# Outside\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    headings = [s.heading for s in idx.sections if s.level > 0]
    assert "H" in headings
    assert "Outside" in headings
    assert "inside tilde" not in headings


def test_no_heading_yields_synthetic_root_only() -> None:
    text = "para one\npara two\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    non_root = [s for s in idx.sections if s.level > 0]
    assert non_root == []
    roots = [s for s in idx.sections if s.level == 0]
    assert len(roots) == 1
    assert roots[0].start_line == 1
    assert roots[0].end_line == 2


def test_markdown_table_preserves_line_numbers() -> None:
    text = "# H\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    assert idx.document.line_count == 4
    assert idx.get_line(2) == "| a | b |"
    assert idx.get_line(4) == "| 1 | 2 |"
    # Section H spans the table.
    h_section = next(s for s in idx.sections if s.heading == "H")
    assert h_section.start_line == 1
    assert h_section.end_line == 4


def test_chinese_heading() -> None:
    text = "# 反应堆描述\n全堆芯模型\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    headings = [s.heading for s in idx.sections if s.level > 0]
    assert "反应堆描述" in headings


def test_empty_atx_heading() -> None:
    # Lone hash with no text is treated as an empty heading rather than
    # crashing.
    text = "#\nbody\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    headings = [s for s in idx.sections if s.level > 0]
    assert any(h.heading == "" for h in headings)


def test_section_path_includes_root_sentinel() -> None:
    text = "# A\n## A1\nbody\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    a1 = next(s for s in idx.sections if s.heading == "A1")
    assert a1.section_path[0] == ""
    assert "A" in a1.section_path
    assert "A1" in a1.section_path


def test_section_content_hash_changes_with_body() -> None:
    text_a = "# H\nbody one\n"
    text_b = "# H\nbody two\n"
    idx_a = build_source_index(text=text_a, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    idx_b = build_source_index(text=text_b, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    sa = next(s for s in idx_a.sections if s.heading == "H")
    sb = next(s for s in idx_b.sections if s.heading == "H")
    assert sa.content_hash != sb.content_hash


def test_spans_for_section_returns_full_range() -> None:
    text = "# H\na\nb\nc\n"
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    h_section = next(s for s in idx.sections if s.heading == "H")
    spans = idx.spans_for_section(h_section.section_id)
    assert len(spans) == 1
    assert spans[0].start_line == 1
    assert spans[0].end_line == idx.document.line_count
    assert "a" in spans[0].excerpt
    assert "c" in spans[0].excerpt


def test_synthetic_root_on_empty_document() -> None:
    idx = build_source_index(text="", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    root = idx._root_section()
    assert root.level == 0
    # Spans on an empty document are rejected via _validate_range.
    with pytest.raises(Exception):
        idx.make_span(1, 1)
