"""Deterministic source indexing for plan investigation.

This module builds a :class:`SourceIndex` from raw user-supplied text.  The
index provides:

* Verbatim line access via :meth:`get_line` / :meth:`get_lines`.
* Section-aware navigation via :meth:`section_for_line` /
  :meth:`spans_for_section`.
* Hash-verified :meth:`make_span` that enforces excerpt integrity.
* Basic literal / keyword search primitives (not registered as LLM tools).

Step 1 only accepts ``user_requirement`` and ``attached_document`` sources.
Other :class:`SourceKind` values are reserved for later steps that add the
corresponding (read-only, sandboxed) tool surface.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from pydantic import ConfigDict, Field, PrivateAttr

from openmc_agent.schemas import AgentBaseModel

from .errors import (
    PlanInvestigationIssue,
    SOURCE_HASH_MISMATCH,
    SOURCE_REF_MISSING,
    SOURCE_SPAN_INVALID,
)
from .hashing import content_hash
from .models import (
    ALLOWED_STEP1_SOURCE_KINDS,
    EvidenceSourceRef,
    SourceDocument,
    SourceKind,
    SourceSection,
    SourceSpan,
    _compute_source_id,
    _compute_span_id,
    _normalize_title,
)

__all__ = [
    "normalize_source_text",
    "build_source_index",
    "SourceIndex",
    "LineRecord",
    "INDEX_VERSION",
]


INDEX_VERSION: str = "0.1"


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------


def normalize_source_text(text: str) -> str:
    """Canonical normalization of raw source text.

    Rules (deliberately minimal; see Step 1 design doc):

    1. UTF-8 BOM removal (``\\ufeff``).
    2. CRLF / CR -> LF.
    3. Unicode NFC.
    4. No whitespace folding; no per-line strip; no blank-line removal.
    5. Trailing-newline normalization: the canonical text always ends with
       exactly one ``\\n`` when non-empty.  This guarantees stable line
       numbering across "no trailing newline" / "one trailing newline" /
       "multiple trailing newlines" variants of the same body text.

    Empty input is preserved as ``""`` (``line_count == 0``).
    """

    if text is None:
        return ""
    # 1. BOM removal.  Also handle the case where the BOM survived as a
    # standalone character after a copy/paste.
    cleaned = text.replace("\ufeff", "")
    # 2. Line-ending normalization (CRLF and lone CR -> LF).
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # 3. Unicode NFC.
    cleaned = unicodedata.normalize("NFC", cleaned)
    # 4. (No interior transforms.)
    # 5. Trailing-newline normalization.
    if cleaned:
        cleaned = cleaned.rstrip("\n") + "\n"
    return cleaned


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


_ATX_HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))??(?:[ \t]+#+)?\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)")


def _parse_sections(lines: list[str]) -> list[SourceSection]:
    """Return a list of :class:`SourceSection` objects covering ``lines``.

    A synthetic root section is always created first.  ATX headings
    (``#``-prefixed, level 1..6) start new sections.  Lines inside fenced
    code blocks (``` or ~~~) are not interpreted as headings.
    """

    if not lines:
        # Single synthetic root section spanning an empty range.  Callers
        # that need to make spans against an empty document will fail at
        # span-validation time, which is the right outcome.
        return []

    root = SourceSection(
        section_id="root",
        source_id="",
        heading="",
        level=0,
        section_path=("",),
        start_line=1,
        end_line=len(lines),
        parent_section_id=None,
        content_hash="",
    )

    sections: list[SourceSection] = [root]
    # Active stack of heading section indices, by level.  Root is always at
    # position 0; children stack on top.
    stack: list[int] = [0]
    # Path components accumulated as we descend.
    path: list[str] = [""]
    # Track fence state so ``#`` inside code blocks is ignored.
    fence_marker: str | None = None

    # We'll patch section_id / source_id / content_hash later, once we know
    # the source.  For now record only the structural data.
    for line_no, line in enumerate(lines, start=1):
        if fence_marker is not None:
            if _FENCE_RE.match(line) and line.strip().startswith(fence_marker):
                fence_marker = None
            continue
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            fence_marker = fence_match.group(1)[0] * 3
            continue

        match = _ATX_HEADING_RE.match(line)
        if match is None:
            continue
        hashes, body = match.group(1), match.group(2)
        if body is None:
            # ATX heading with no body: treat as empty heading.
            body = ""
        # Per CommonMark: leading hashes are the level, then one space, then
        # the heading text.  Trailing hashes are decorative.
        heading_text = body.strip().rstrip("#").rstrip()
        level = len(hashes)

        # Pop the stack until we find a parent with strictly smaller level.
        while len(stack) > 1:
            parent_idx = stack[-1]
            parent_level = sections[parent_idx].level
            if parent_level < level:
                break
            # Closing the parent: set its end_line to the previous heading
            # line (line_no - 1).
            sections[parent_idx].end_line = line_no - 1  # type: ignore[assignment]
            stack.pop()
            path.pop()

        parent_idx = stack[-1]
        path.append(heading_text)
        section = SourceSection(
            section_id=f"sec_{len(sections)}",
            source_id="",
            heading=heading_text,
            level=level,
            section_path=tuple(path),
            start_line=line_no,
            end_line=len(lines),
            parent_section_id=sections[parent_idx].section_id,
            content_hash="",
        )
        sections.append(section)
        stack.append(len(sections) - 1)

    return sections


# ---------------------------------------------------------------------------
# LineRecord
# ---------------------------------------------------------------------------


class LineRecord(AgentBaseModel):
    """Per-line hash record.  Used in artifacts to prove line stability.

    Inherits from :class:`AgentBaseModel` but disables ``str_strip_whitespace``
    so verbatim line content (including any leading/trailing spaces that may
    matter for code blocks or tables) is preserved exactly.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    line_number: int
    text: str
    line_hash: str

    def __init__(self, *, line_number: int, text: str, line_hash: str | None = None) -> None:
        super().__init__(
            line_number=line_number,
            text=text,
            line_hash=line_hash if line_hash is not None else content_hash(text),
        )


# ---------------------------------------------------------------------------
# SourceIndex
# ---------------------------------------------------------------------------


class SourceIndex(AgentBaseModel):
    """Deterministic index over a single source document.

    Build via :func:`build_source_index`.  Once built, the index is immutable
    in practice (Pydantic validates assignment but does not freeze); callers
    MUST treat it as read-only.
    """

    document: SourceDocument
    sections: list[SourceSection] = Field(default_factory=list)
    line_records: list[LineRecord] = Field(default_factory=list)
    index_version: str = INDEX_VERSION
    index_hash: str = ""

    _lines_cache: list[str] | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _lines(self) -> list[str]:
        if self._lines_cache is None:
            # Reconstruct verbatim lines from line_records so we don't store
            # the full body twice on the hot path.
            self._lines_cache = [record.text for record in self.line_records]
        return self._lines_cache

    # ------------------------------------------------------------------
    # Line access
    # ------------------------------------------------------------------

    def get_line(self, line_number: int) -> str:
        """Return the verbatim text of ``line_number`` (1-indexed)."""
        if line_number < 1 or line_number > self.document.line_count:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "line_number out of range",
                details={
                    "source_id": self.document.source_id,
                    "line_number": line_number,
                    "line_count": self.document.line_count,
                },
            )
        return self._lines[line_number - 1]

    def get_lines(self, start_line: int, end_line: int) -> str:
        """Return verbatim text for ``[start_line, end_line]`` joined by ``\\n``.

        The returned value is exactly the bytes a reader would see in the
        original document for those line numbers, with no trailing newline
        (mirroring how spans are stored).
        """

        self._validate_range(start_line, end_line)
        return "\n".join(self._lines[start_line - 1 : end_line])

    # ------------------------------------------------------------------
    # Span construction / validation
    # ------------------------------------------------------------------

    def make_span(self, start_line: int, end_line: int) -> SourceSpan:
        """Build a hash-verified :class:`SourceSpan` for ``[start_line, end_line]``."""
        self._validate_range(start_line, end_line)
        excerpt = self.get_lines(start_line, end_line)
        excerpt_hash = content_hash(excerpt)
        section = self.section_for_line(start_line)
        return SourceSpan(
            span_id=_compute_span_id(
                source_id=self.document.source_id,
                start_line=start_line,
                end_line=end_line,
                excerpt_hash=excerpt_hash,
            ),
            source_id=self.document.source_id,
            start_line=start_line,
            end_line=end_line,
            section_id=section.section_id,
            section_path=section.section_path,
            excerpt=excerpt,
            excerpt_hash=excerpt_hash,
        )

    def validate_span(self, source_span: SourceSpan) -> None:
        """Raise if ``source_span`` does not belong to this index or its
        excerpt has been tampered with.
        """

        if source_span.source_id != self.document.source_id:
            raise PlanInvestigationIssue(
                SOURCE_REF_MISSING,
                "span references a foreign source_id",
                details={
                    "expected": self.document.source_id,
                    "actual": source_span.source_id,
                },
            )
        self._validate_range(source_span.start_line, source_span.end_line)
        expected = self.get_lines(source_span.start_line, source_span.end_line)
        if source_span.excerpt != expected:
            raise PlanInvestigationIssue(
                SOURCE_HASH_MISMATCH,
                "span excerpt does not match the indexed source text",
                details={
                    "source_id": self.document.source_id,
                    "start_line": source_span.start_line,
                    "end_line": source_span.end_line,
                },
            )
        expected_hash = content_hash(expected)
        if source_span.excerpt_hash != expected_hash:
            raise PlanInvestigationIssue(
                SOURCE_HASH_MISMATCH,
                "span excerpt_hash does not match the indexed source",
                details={
                    "expected": expected_hash,
                    "actual": source_span.excerpt_hash,
                },
            )

    def validate_source_ref(self, ref: EvidenceSourceRef) -> None:
        """Validate that ``ref`` points to a real span in this index.

        Validates the ref against the index by checking the excerpt hash
        against any span reconstructed from the same source/line range.
        Because :class:`EvidenceSourceRef` carries only ids (not line ranges),
        validation requires the span to have been registered first via
        :meth:`register_span` (or constructed via :meth:`make_span` and
        tracked externally).
        """

        if ref.source_id != self.document.source_id:
            raise PlanInvestigationIssue(
                SOURCE_REF_MISSING,
                "source_ref references a foreign source_id",
                details={"expected": self.document.source_id, "actual": ref.source_id},
            )
        record = self._registered_spans.get(ref.span_id)
        if record is None:
            raise PlanInvestigationIssue(
                SOURCE_REF_MISSING,
                "source_ref span_id is not registered in this index",
                details={"span_id": ref.span_id},
            )
        if record.excerpt_hash != ref.excerpt_hash:
            raise PlanInvestigationIssue(
                SOURCE_HASH_MISMATCH,
                "source_ref excerpt_hash does not match the indexed span",
                details={"expected": record.excerpt_hash, "actual": ref.excerpt_hash},
            )

    # ------------------------------------------------------------------
    # Section navigation
    # ------------------------------------------------------------------

    def section_for_line(self, line_number: int) -> SourceSection:
        """Return the innermost :class:`SourceSection` containing ``line_number``."""
        if line_number < 1 or line_number > self.document.line_count:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "line_number out of range",
                details={"line_number": line_number, "line_count": self.document.line_count},
            )
        # Find the deepest section whose [start_line, end_line] contains
        # line_number.  Ties broken by larger level (deeper).
        candidates = [
            s
            for s in self.sections
            if s.start_line <= line_number <= s.end_line and s.level > 0
        ]
        if not candidates:
            return self._root_section()
        return max(candidates, key=lambda s: (s.level, -s.start_line))

    def spans_for_section(self, section_id: str) -> list[SourceSpan]:
        """Return one span per line range that is fully inside ``section_id``.

        For convenience we return the *whole* section as a single span; if
        the section is empty (zero-width), return an empty list.
        """

        section = next((s for s in self.sections if s.section_id == section_id), None)
        if section is None:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "unknown section_id",
                details={"section_id": section_id},
            )
        if section.end_line < section.start_line or self.document.line_count == 0:
            return []
        return [self.make_span(section.start_line, section.end_line)]

    # ------------------------------------------------------------------
    # Basic search primitives (not registered as LLM tools)
    # ------------------------------------------------------------------

    def find_literal(self, query: str, *, max_hits: int = 50) -> list[SourceSpan]:
        """Case-sensitive substring search.  Returns up to ``max_hits`` spans.

        Each hit is a single-line span covering the match.  Overlapping
        matches on the same line are NOT merged (deterministic hit list).
        """

        if max_hits <= 0:
            return []
        hits: list[SourceSpan] = []
        for line_no, line in enumerate(self._lines, start=1):
            idx = line.find(query)
            while idx != -1:
                hits.append(self.make_span(line_no, line_no))
                if len(hits) >= max_hits:
                    return hits
                idx = line.find(query, idx + 1)
        return hits

    def find_keywords(self, keywords: Iterable[str], *, max_hits: int = 50) -> list[SourceSpan]:
        """Case-sensitive multi-keyword AND search across the document.

        A line qualifies only if EVERY keyword appears on it.  Useful for
        reactor-neutral searches like ``find_keywords(["assembly", "count"])``.
        """

        keys = [k for k in keywords if k]
        if not keys or max_hits <= 0:
            return []
        hits: list[SourceSpan] = []
        for line_no, line in enumerate(self._lines, start=1):
            if all(k in line for k in keys):
                hits.append(self.make_span(line_no, line_no))
                if len(hits) >= max_hits:
                    return hits
        return hits

    # ------------------------------------------------------------------
    # Span registration (used by claim validation)
    # ------------------------------------------------------------------

    _registered_spans: dict[str, SourceSpan] = PrivateAttr(default_factory=dict)

    def register_span(self, span: SourceSpan) -> None:
        """Register ``span`` as a known-good span for later source-ref checks."""
        self.validate_span(span)
        self._registered_spans[span.span_id] = span

    # ------------------------------------------------------------------
    # Internal validators
    # ------------------------------------------------------------------

    def _validate_range(self, start_line: int, end_line: int) -> None:
        if start_line < 1 or end_line < start_line or end_line > self.document.line_count:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "invalid line range",
                details={
                    "source_id": self.document.source_id,
                    "start_line": start_line,
                    "end_line": end_line,
                    "line_count": self.document.line_count,
                },
            )

    def _root_section(self) -> SourceSection:
        root = next((s for s in self.sections if s.level == 0), None)
        if root is None:
            # No sections at all (empty document).  Synthesize a root on the
            # fly so callers always get something sensible.
            return SourceSection(
                section_id="root",
                source_id=self.document.source_id,
                heading="",
                level=0,
                section_path=("",),
                start_line=1,
                end_line=max(self.document.line_count, 1),
                parent_section_id=None,
                content_hash="",
            )
        return root


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_source_index(
    *,
    text: str,
    title: str,
    source_kind: SourceKind,
    origin_label: str = "",
    metadata: dict[str, Any] | None = None,
) -> SourceIndex:
    """Build a :class:`SourceIndex` from raw source text.

    The ``source_kind`` MUST be one of :data:`ALLOWED_STEP1_SOURCE_KINDS` for
    Step 1.  Other kinds (``repository``, ``openmc_docs``, ``official_web``)
    are rejected because the tool surface that would populate them does not
    exist yet.
    """

    if source_kind.value not in ALLOWED_STEP1_SOURCE_KINDS:
        raise PlanInvestigationIssue(
            "plan_investigation.source_kind_not_allowed",
            "source_kind is reserved for a later step",
            details={"source_kind": source_kind.value, "allowed": sorted(ALLOWED_STEP1_SOURCE_KINDS)},
        )

    normalized = normalize_source_text(text)
    normalized_hash = content_hash(normalized)
    normalized_title = _normalize_title(title)

    # Split into lines WITHOUT the trailing newline.  The canonical form
    # guarantees exactly one trailing ``\n`` when non-empty, so the split is
    # deterministic.  An empty normalized document yields zero lines
    # (``line_count == 0``).
    lines: list[str] = normalized[:-1].split("\n") if normalized else []

    line_records: list[LineRecord] = [
        LineRecord(line_number=idx, text=line) for idx, line in enumerate(lines, start=1)
    ]
    char_count = len(normalized)

    sections = _parse_sections(lines)
    # Patch section source_id / section_id / content_hash now that we know
    # the source id.
    source_id = _compute_source_id(
        source_kind=source_kind.value,
        normalized_title=normalized_title,
        normalized_content_hash=normalized_hash,
    )
    patched_sections: list[SourceSection] = []
    for section in sections:
        # Re-hash section body for tamper evidence.
        if section.end_line >= section.start_line and lines:
            body = "\n".join(lines[section.start_line - 1 : section.end_line])
        else:
            body = ""
        body_hash = content_hash(body)
        # Make section_id deterministic and stable: combine source_id with
        # the structural locator (level + path + range).
        structural = {
            "src": source_id,
            "lvl": section.level,
            "path": list(section.section_path),
            "r": [section.start_line, section.end_line],
        }
        from .hashing import short_id

        sec_id = short_id("sec", structural)
        patched = section.model_copy(
            update={
                "source_id": source_id,
                "section_id": sec_id,
                "content_hash": body_hash,
            }
        )
        patched_sections.append(patched)

    document = SourceDocument(
        source_id=source_id,
        source_kind=source_kind,
        title=title,
        origin_label=origin_label,
        content_hash=normalized_hash,  # body hash and normalized hash coincide
        normalized_content_hash=normalized_hash,
        line_count=len(lines),
        char_count=char_count,
        section_count=len(patched_sections),
        metadata=dict(metadata or {}),
    )

    index_hash = content_hash(
        {
            "index_version": INDEX_VERSION,
            "document": document.model_dump(mode="json"),
            "sections": [s.model_dump(mode="json") for s in patched_sections],
            # Line hashes are part of the index hash so any byte-level edit
            # is reflected.  We do NOT include the raw line text in the hash
            # payload explicitly: line_hash already commits to it.
            "line_hashes": [record.line_hash for record in line_records],
        }
    )

    index = SourceIndex(
        document=document,
        sections=patched_sections,
        line_records=line_records,
        index_version=INDEX_VERSION,
        index_hash=index_hash,
    )
    return index
