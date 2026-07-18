"""Tests for source-text normalization and basic SourceIndex construction."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import SourceKind
from openmc_agent.plan_investigation.source_index import (
    normalize_source_text,
    build_source_index,
)


# ---------------------------------------------------------------------------
# A. Text normalization
# ---------------------------------------------------------------------------


def test_lf_crlf_cr_produce_same_normalized_hash() -> None:
    lf = "line one\nline two\nline three"
    crlf = "line one\r\nline two\r\nline three"
    cr = "line one\rline two\rline three"
    assert content_hash(normalize_source_text(lf)) == content_hash(normalize_source_text(lf))
    assert content_hash(normalize_source_text(lf)) == content_hash(normalize_source_text(crlf))
    assert content_hash(normalize_source_text(lf)) == content_hash(normalize_source_text(cr))


def test_utf8_bom_removed() -> None:
    bom_text = "\ufeffhello\nworld"
    cleaned = normalize_source_text(bom_text)
    assert "\ufeff" not in cleaned
    assert cleaned == "hello\nworld\n"


def test_chinese_text_stable() -> None:
    chinese = "# 反应堆芯\n全堆 3x3 布局。\n"
    normalized = normalize_source_text(chinese)
    assert "反应堆芯" in normalized
    assert "全堆" in normalized
    assert content_hash(normalized) == content_hash(normalize_source_text(chinese))


def test_blank_lines_preserved() -> None:
    text = "a\n\n\nb\n"
    normalized = normalize_source_text(text)
    assert normalized == "a\n\n\nb\n"
    # Three lines: 'a', '', 'b'... wait: text[:-1].split('\n') -> ['a','','','b'] -> 4 lines.
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    assert idx.document.line_count == 4
    assert idx.get_line(1) == "a"
    assert idx.get_line(2) == ""
    assert idx.get_line(3) == ""
    assert idx.get_line(4) == "b"


def test_trailing_newline_rule_is_stable() -> None:
    no_newline = "a\nb"
    one_newline = "a\nb\n"
    two_newlines = "a\nb\n\n"
    h1 = content_hash(normalize_source_text(no_newline))
    h2 = content_hash(normalize_source_text(one_newline))
    h3 = content_hash(normalize_source_text(two_newlines))
    assert h1 == h2 == h3, "trailing newline variants must produce identical hash"
    assert normalize_source_text(no_newline).endswith("\n")
    assert normalize_source_text(one_newline).endswith("\n")
    assert not normalize_source_text(one_newline).endswith("\n\n")


def test_no_whitespace_folding() -> None:
    text = "a    b\n\tc\n"
    normalized = normalize_source_text(text)
    assert "a    b" in normalized
    assert "\tc" in normalized


def test_empty_document() -> None:
    assert normalize_source_text("") == ""
    idx = build_source_index(text="", title="empty", source_kind=SourceKind.ATTACHED_DOCUMENT)
    assert idx.document.line_count == 0
    assert idx.document.char_count == 0


def test_single_line_document() -> None:
    idx = build_source_index(text="only line", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    assert idx.document.line_count == 1
    assert idx.get_line(1) == "only line"


def test_unrecognized_source_kind_rejected() -> None:
    with pytest.raises(Exception):
        build_source_index(text="x", title="t", source_kind=SourceKind.REPOSITORY)


def test_source_id_deterministic_across_rebuilds() -> None:
    a = build_source_index(text="abc\n", title="T", source_kind=SourceKind.USER_REQUIREMENT)
    b = build_source_index(text="abc\n", title="T", source_kind=SourceKind.USER_REQUIREMENT)
    assert a.document.source_id == b.document.source_id
    assert a.index_hash == b.index_hash


def test_source_id_changes_with_content() -> None:
    a = build_source_index(text="abc\n", title="T", source_kind=SourceKind.USER_REQUIREMENT)
    b = build_source_index(text="abd\n", title="T", source_kind=SourceKind.USER_REQUIREMENT)
    assert a.document.source_id != b.document.source_id


def test_origin_label_does_not_change_source_id() -> None:
    a = build_source_index(text="x\n", title="T", source_kind=SourceKind.USER_REQUIREMENT, origin_label="a")
    b = build_source_index(text="x\n", title="T", source_kind=SourceKind.USER_REQUIREMENT, origin_label="b")
    assert a.document.source_id == b.document.source_id
    assert a.document.origin_label == "a"
    assert b.document.origin_label == "b"
