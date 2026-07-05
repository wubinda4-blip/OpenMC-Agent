"""Local lexical RAG over project and downloaded OpenMC documentation.

The RAG layer is deliberately lightweight: it chunks local files, scores them
with deterministic lexical signals, and returns evidence for repair prompts.
It does not call the network, use embeddings, or confirm physical facts.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.schemas import AgentBaseModel, ValidationIssue


RagSourceType = Literal[
    "project_doc",
    "project_example",
    "openmc_doc",
    "openmc_api_doc",
    "openmc_example",
    "internal_note",
    "unknown",
]

RagTrigger = Literal[
    "validation_issue",
    "runtime_issue",
    "export_xml_issue",
    "hex_lattice_issue",
    "graph_context",
    "manual",
]

DEFAULT_RAG_SEARCH_ROOTS = [
    "docs",
    "examples",
    "openmc_docs",
    "openmc_examples",
    "README.md",
]
DEFAULT_RAG_INCLUDE_GLOBS = ["*.md", "*.rst", "*.txt", "*.py", "*.json", "*.yaml", "*.yml", "*.toml"]
DEFAULT_RAG_EXCLUDE_GLOBS = [
    ".git/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
    "*.pyc",
    "*.h5",
    "statepoint.*.h5",
]
DEFAULT_RAG_MAX_FILE_BYTES = 512_000
DEFAULT_RAG_MAX_CHUNKS = 1500
DEFAULT_RAG_MAX_QUERIES = 12

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*|\d+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$|^([A-Za-z0-9][^\n]{1,120})\n[-=]{3,}\s*$", re.M)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

_KEYWORD_REFS: list[tuple[re.Pattern[str], dict[str, list[str]]]] = [
    (
        re.compile(r"\bHexLattice\b|hex[_ -]?lattice|outer_universe_id|\brings\b", re.I),
        {
            "api_refs": ["openmc.api.HexLattice", "openmc.HexLattice"],
            "doc_refs": ["openmc.usersguide.geometry"],
            "concept_ids": ["openmc.geometry.hex_lattice"],
            "schema_paths": ["LatticeSpec.rings", "LatticeSpec.outer_universe_id"],
        },
    ),
    (
        re.compile(r"Material\.set_density|set_density|density_unit", re.I),
        {
            "api_refs": ["openmc.api.Material.set_density", "openmc.Material.set_density"],
            "doc_refs": ["openmc.usersguide.materials"],
            "concept_ids": ["openmc.material.density_unit"],
            "schema_paths": ["MaterialSpec.density_unit", "ComplexMaterialSpec.density_unit"],
        },
    ),
    (
        re.compile(r"add_s_alpha_beta|thermal scattering|s_alpha_beta", re.I),
        {
            "api_refs": ["openmc.api.Material.add_s_alpha_beta"],
            "doc_refs": ["openmc.usersguide.materials"],
            "concept_ids": ["openmc.material.thermal_scattering"],
        },
    ),
    (
        re.compile(r"OPENMC_CROSS_SECTIONS|cross_sections\.xml|cross sections", re.I),
        {
            "doc_refs": ["openmc.usersguide.cross_sections"],
            "concept_ids": ["openmc.data.cross_sections"],
            "schema_paths": ["runtime.cross_sections"],
        },
    ),
    (
        re.compile(r"geometry overlap|lost particle|surface|region", re.I),
        {
            "doc_refs": ["openmc.usersguide.geometry", "openmc.usersguide.troubleshoot"],
            "concept_ids": ["openmc.geometry.region_boolean_expression", "openmc.geometry.surface"],
        },
    ),
]


class DocumentChunk(AgentBaseModel):
    chunk_id: str
    source_id: str
    source_type: RagSourceType
    path: str
    title: str = ""
    section: str = ""
    text: str
    start_line: int | None = None
    end_line: int | None = None
    doc_refs: list[str] = Field(default_factory=list)
    api_refs: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    schema_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagSearchRequest(AgentBaseModel):
    trigger: RagTrigger
    issue_codes: list[str] = Field(default_factory=list)
    schema_paths: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    doc_refs: list[str] = Field(default_factory=list)
    api_refs: list[str] = Field(default_factory=list)
    example_refs: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    search_roots: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    top_k: int = 6
    max_chunk_chars: int = 1600


class RagSearchResult(AgentBaseModel):
    request: RagSearchRequest
    chunks: list[DocumentChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


def build_local_rag_index(search_roots: list[str] | None = None) -> list[DocumentChunk]:
    """Build an in-memory chunk index from allowed local documentation roots."""
    roots, warnings = _resolve_search_roots(search_roots or DEFAULT_RAG_SEARCH_ROOTS)
    del warnings
    chunks: list[DocumentChunk] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in paths:
            if len(chunks) >= DEFAULT_RAG_MAX_CHUNKS:
                return chunks
            if not _should_index_file(path):
                continue
            try:
                if path.stat().st_size > DEFAULT_RAG_MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            chunks.extend(_chunk_file(path, text))
            if len(chunks) >= DEFAULT_RAG_MAX_CHUNKS:
                return chunks[:DEFAULT_RAG_MAX_CHUNKS]
    return chunks


def rag_search(request: RagSearchRequest) -> RagSearchResult:
    """Search local documentation chunks with deterministic lexical scoring."""
    normalized = _normalize_request(request)
    roots, root_warnings = _resolve_search_roots(normalized.search_roots)
    warnings = list(root_warnings)
    if not roots:
        return RagSearchResult(
            request=normalized,
            warnings=["no allowed RAG search roots exist", *warnings],
        )

    chunks = build_local_rag_index([str(root) for root in roots])
    if normalized.source_types:
        allowed = set(normalized.source_types)
        chunks = [chunk for chunk in chunks if chunk.source_type in allowed]

    scored: list[tuple[float, DocumentChunk]] = []
    for chunk in chunks:
        score = _score_chunk(chunk, normalized)
        if score <= 0:
            continue
        scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line or 0))
    deduped = _dedupe_scored_chunks(scored)
    top = [
        _chunk_with_score(_truncate_chunk(chunk, normalized.max_chunk_chars), score)
        for score, chunk in deduped[: normalized.top_k]
    ]
    if not top:
        warnings.append("no RAG document chunks matched the request")
    return RagSearchResult(
        request=normalized,
        chunks=top,
        warnings=warnings,
        truncated=len(deduped) > len(top),
    )


def rag_request_from_graph_context(
    graph_context: GraphContext,
    issues: list[ValidationIssue] | None = None,
) -> RagSearchRequest:
    """Build a graph-guided RAG request from GraphContext and issues."""
    issue_codes: list[str] = []
    schema_paths: list[str] = list(graph_context.related_schema_paths)
    concept_ids: list[str] = list(graph_context.related_concept_ids)
    doc_refs: list[str] = list(graph_context.related_doc_refs)
    api_refs: list[str] = list(graph_context.related_api_refs)
    example_refs: list[str] = list(graph_context.related_example_refs)
    queries: list[str] = list(graph_context.retrieval_hints)

    for issue in issues or []:
        issue_codes.append(issue.code)
        if issue.schema_path:
            schema_paths.append(issue.schema_path)
        if issue.concept_id:
            concept_ids.append(issue.concept_id)
        queries.extend(issue.grep_patterns)
        queries.extend(_message_tokens(issue.message, max_tokens=8))
        for ref in issue.knowledge_refs:
            if ref.source_type in {"openmc_docs", "openmc_developer_docs", "project_rule"}:
                doc_refs.append(ref.ref_id)
            elif ref.source_type == "example":
                example_refs.append(ref.ref_id)
            elif ref.source_type == "retrieval_query":
                queries.append(ref.ref_id)
            if ref.ref_id.startswith("openmc.api.") or ref.ref_id.startswith("openmc."):
                api_refs.append(ref.ref_id)
            if ref.retrieval_query:
                queries.append(ref.retrieval_query)
            if ref.concept_id:
                concept_ids.append(ref.concept_id)
        if issue.code.startswith("runtime.cross_sections"):
            queries.append("OpenMC cross_sections.xml OPENMC_CROSS_SECTIONS configuration")
        if issue.code.startswith("runtime.geometry_overlap"):
            queries.append("OpenMC geometry overlap region surface boundary")
        if issue.code.startswith("lattice.hex."):
            queries.append("OpenMC HexLattice rings outer universe orientation")

    trigger: RagTrigger = "graph_context"
    if any(code.startswith("runtime.") for code in issue_codes):
        trigger = "runtime_issue"
    if any(code.startswith("export_xml.") for code in issue_codes):
        trigger = "export_xml_issue"
    if any(code.startswith("lattice.hex.") for code in issue_codes):
        trigger = "hex_lattice_issue"
    elif issue_codes:
        trigger = "validation_issue"

    return RagSearchRequest(
        trigger=trigger,
        issue_codes=_dedupe(issue_codes),
        schema_paths=_dedupe(schema_paths),
        concept_ids=_dedupe(concept_ids),
        doc_refs=_dedupe(doc_refs),
        api_refs=_dedupe(api_refs),
        example_refs=_dedupe(example_refs),
        queries=_normalize_queries(queries),
        top_k=6,
    )


def issue_should_run_rag(issue: ValidationIssue) -> bool:
    """Return whether an issue should trigger document evidence retrieval."""
    if issue.requires_retrieval or issue.route_hint == "retrieval":
        return True
    if issue.code.startswith(("lattice.hex.", "runtime.geometry_overlap", "runtime.lost_particle")):
        return True
    if issue.code.startswith("runtime.") and "unknown" in issue.code:
        return True
    return False


def gather_rag_evidence_for_issues(
    issues: list[ValidationIssue],
    graph_context: GraphContext,
    *,
    max_evidence: int = 6,
) -> list[RetrievedEvidence]:
    """Collect bounded RAG evidence when issues or graph hints call for it."""
    if not _should_run_rag_for_context(issues, graph_context):
        return []
    request = rag_request_from_graph_context(graph_context, issues)
    result = rag_search(request)
    return rag_result_to_evidence(result)[:max_evidence]


def rag_result_to_evidence(result: RagSearchResult) -> list[RetrievedEvidence]:
    """Convert RAG chunks to prompt-ready RetrievedEvidence records."""
    evidence: list[RetrievedEvidence] = []
    issue_code = result.request.issue_codes[0] if result.request.issue_codes else None
    schema_path = result.request.schema_paths[0] if result.request.schema_paths else None
    concept_id = result.request.concept_ids[0] if result.request.concept_ids else None
    for chunk in result.chunks:
        locator = chunk.path
        if chunk.start_line is not None and chunk.end_line is not None:
            locator = f"{locator}:{chunk.start_line}-{chunk.end_line}"
        section = chunk.section or chunk.title
        if section:
            locator = f"{locator} ({section})"
        evidence.append(
            RetrievedEvidence(
                source_type="rag",
                locator=locator,
                text=_truncate_text(chunk.text, result.request.max_chunk_chars),
                issue_code=issue_code,
                schema_path=schema_path,
                concept_id=concept_id,
                score=chunk.metadata.get("score"),
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "source_type": chunk.source_type,
                    "doc_refs": chunk.doc_refs,
                    "api_refs": chunk.api_refs,
                    "concept_ids": chunk.concept_ids,
                    "schema_paths": chunk.schema_paths,
                    "score": chunk.metadata.get("score"),
                    "requires_human_confirmation": any(
                        "cross_sections" in code for code in result.request.issue_codes
                    ),
                    "warnings": result.warnings,
                },
            )
        )
    return evidence


def format_rag_evidence(evidence: list[RetrievedEvidence], *, limit: int = 6) -> str:
    """Render RAG evidence for reflection prompts."""
    if not evidence:
        return ""
    lines = [
        "\n[RAG Evidence]",
        "RAG evidence is local documentation context only; it is not a final physical fact.",
    ]
    for item in evidence[:limit]:
        lines.append(f"- source: {item.locator}")
        doc_refs = item.metadata.get("doc_refs") or []
        api_refs = item.metadata.get("api_refs") or []
        concepts = item.metadata.get("concept_ids") or []
        if doc_refs:
            lines.append(f"  doc_refs: {', '.join(doc_refs[:4])}")
        if api_refs:
            lines.append(f"  api_refs: {', '.join(api_refs[:4])}")
        if concepts:
            lines.append(f"  concepts: {', '.join(concepts[:4])}")
        lines.append("  text:")
        lines.extend(f"    {line}" for line in item.text.rstrip().splitlines()[:12])
    return "\n".join(lines) + "\n"


def merge_retrieved_evidence(
    grep_evidence: list[RetrievedEvidence],
    graph_evidence: list[RetrievedEvidence],
    rag_evidence: list[RetrievedEvidence],
    max_items: int = 12,
) -> list[RetrievedEvidence]:
    """Merge evidence in prompt priority order without letting RAG crowd out grep."""
    merged: list[RetrievedEvidence] = []
    seen: set[tuple[str, str]] = set()
    for group in (grep_evidence, graph_evidence, rag_evidence):
        for item in group:
            key = _evidence_key(item)
            if key in seen or _similar_to_existing(item.text, merged):
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max_items:
                return merged
    return merged


def _normalize_request(request: RagSearchRequest) -> RagSearchRequest:
    return request.model_copy(
        update={
            "issue_codes": _dedupe(request.issue_codes),
            "schema_paths": _dedupe(request.schema_paths),
            "concept_ids": _dedupe(request.concept_ids),
            "doc_refs": _dedupe(request.doc_refs),
            "api_refs": _dedupe(_expand_api_refs(request.api_refs)),
            "example_refs": _dedupe(request.example_refs),
            "queries": _normalize_queries(request.queries),
            "search_roots": request.search_roots or DEFAULT_RAG_SEARCH_ROOTS,
            "top_k": max(1, min(request.top_k, 20)),
            "max_chunk_chars": max(300, min(request.max_chunk_chars, 4000)),
        }
    )


def _resolve_search_roots(raw_roots: list[str]) -> tuple[list[Path], list[str]]:
    cwd = Path.cwd().resolve(strict=False)
    allowed_bases = [cwd, Path("/tmp").resolve(strict=False)]
    roots: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()
    for raw_root in raw_roots:
        raw = Path(raw_root)
        candidate = (cwd / raw if not raw.is_absolute() else raw).resolve(strict=False)
        if not candidate.exists():
            continue
        if not any(_is_relative_to(candidate, base) for base in allowed_bases):
            warnings.append(f"skipped disallowed RAG root: {raw_root}")
            continue
        if candidate not in seen:
            seen.add(candidate)
            roots.append(candidate)
    return roots, warnings


def _should_index_file(path: Path) -> bool:
    rel = _rel_path(path)
    if any(fnmatch.fnmatch(rel, pattern) for pattern in DEFAULT_RAG_EXCLUDE_GLOBS):
        return False
    if any(part in {".git", ".venv", "venv", "__pycache__", ".pytest_cache"} for part in path.parts):
        return False
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in DEFAULT_RAG_INCLUDE_GLOBS)


def _chunk_file(path: Path, text: str) -> list[DocumentChunk]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".rst"}:
        raw_chunks = _chunk_heading_text(text)
    elif suffix == ".py":
        raw_chunks = _chunk_python_text(text)
    else:
        raw_chunks = _chunk_plain_text(text)
    source_type = _source_type_for_path(path)
    chunks: list[DocumentChunk] = []
    for index, raw in enumerate(raw_chunks):
        chunk_text = raw["text"].strip()
        if not chunk_text:
            continue
        refs = _extract_metadata(path, chunk_text)
        chunk_id = f"{_rel_path(path)}#{raw['start_line']}-{raw['end_line']}-{index}"
        chunks.append(
            DocumentChunk(
                chunk_id=chunk_id,
                source_id=_rel_path(path),
                source_type=source_type,
                path=_rel_path(path),
                title=raw.get("title", ""),
                section=raw.get("section", ""),
                text=chunk_text,
                start_line=raw["start_line"],
                end_line=raw["end_line"],
                doc_refs=refs["doc_refs"],
                api_refs=refs["api_refs"],
                concept_ids=refs["concept_ids"],
                schema_paths=refs["schema_paths"],
                metadata={"file_suffix": suffix},
            )
        )
    return chunks


def _chunk_heading_text(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            headings.append((idx, match.group(2).strip()))
            continue
        if idx < len(lines) and re.match(r"^[-=]{3,}\s*$", lines[idx]):
            headings.append((idx, line.strip()))
    if not headings:
        return _chunk_plain_text(text)

    chunks: list[dict[str, Any]] = []
    for pos, (start, heading) in enumerate(headings):
        end = headings[pos + 1][0] - 1 if pos + 1 < len(headings) else len(lines)
        section_lines = lines[start - 1 : end]
        chunks.extend(_split_lines(section_lines, start, title=headings[0][1], section=heading))
    return chunks


def _chunk_python_text(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts = [1]
    for idx, line in enumerate(lines, start=1):
        if re.match(r"^(def|class)\s+\w+", line):
            starts.append(idx)
    starts = sorted(set(starts))
    chunks: list[dict[str, Any]] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] - 1 if pos + 1 < len(starts) else len(lines)
        block = lines[start - 1 : end]
        heading = ""
        for line in block[:5]:
            match = re.match(r"^(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match:
                heading = match.group(2)
                break
        chunks.extend(_split_lines(block, start, title=Path("").name, section=heading))
    return chunks


def _chunk_plain_text(text: str) -> list[dict[str, Any]]:
    return _split_lines(text.splitlines(), 1, title="", section="")


def _split_lines(
    lines: list[str],
    start_line: int,
    *,
    title: str,
    section: str,
    max_chars: int = 1800,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_start = start_line
    current_chars = 0
    for offset, line in enumerate(lines):
        next_len = len(line) + 1
        if current and current_chars + next_len > max_chars:
            chunks.append(
                {
                    "text": "\n".join(current),
                    "start_line": current_start,
                    "end_line": start_line + offset - 1,
                    "title": title,
                    "section": section,
                }
            )
            current = []
            current_start = start_line + offset
            current_chars = 0
        current.append(line)
        current_chars += next_len
    if current:
        chunks.append(
            {
                "text": "\n".join(current),
                "start_line": current_start,
                "end_line": start_line + len(lines) - 1,
                "title": title,
                "section": section,
            }
        )
    return chunks


def _extract_metadata(path: Path, text: str) -> dict[str, list[str]]:
    refs = {"doc_refs": [], "api_refs": [], "concept_ids": [], "schema_paths": []}
    haystack = f"{_rel_path(path)}\n{text}"
    for pattern, values in _KEYWORD_REFS:
        if not pattern.search(haystack):
            continue
        for key, refs_list in values.items():
            refs[key].extend(refs_list)
    for match in re.findall(r"\bopenmc\.(?:api\.)?[A-Za-z_][A-Za-z0-9_\.]*", haystack):
        if any(part[:1].isupper() for part in match.split(".")):
            refs["api_refs"].append(match)
        elif "usersguide" in match:
            refs["doc_refs"].append(match)
        else:
            refs["concept_ids"].append(match)
    return {key: _dedupe(values) for key, values in refs.items()}


def _score_chunk(chunk: DocumentChunk, request: RagSearchRequest) -> float:
    score = 0.0
    text = chunk.text.lower()
    title = f"{chunk.title} {chunk.section}".lower()
    path = chunk.path.lower()
    for ref in request.doc_refs:
        if _ref_matches(ref, chunk.doc_refs) or ref.lower() in text or ref.lower() in path:
            score += 12.0
    for ref in request.api_refs:
        if _ref_matches(ref, chunk.api_refs) or _api_variants(ref.lower()) & set(_tokens(text)):
            score += 10.0
    for ref in request.example_refs:
        if ref.lower() in path or ref.lower() in text:
            score += 8.0
    for concept in request.concept_ids:
        if _ref_matches(concept, chunk.concept_ids) or concept.lower() in text:
            score += 7.0
    for schema_path in request.schema_paths:
        if _ref_matches(schema_path, chunk.schema_paths) or schema_path.lower() in text:
            score += 4.0
    for code in request.issue_codes:
        for token in _code_tokens(code):
            if token in title:
                score += 2.0
            elif token in text:
                score += 0.8
    for query in request.queries:
        terms = _query_terms(query)
        if not terms:
            continue
        hits = sum(1 for term in terms if term in text or term in path)
        title_hits = sum(1 for term in terms if term in title)
        if hits:
            score += hits / len(terms) * 5.0
        score += title_hits * 2.0
    return score


def _dedupe_scored_chunks(scored: list[tuple[float, DocumentChunk]]) -> list[tuple[float, DocumentChunk]]:
    deduped: list[tuple[float, DocumentChunk]] = []
    seen: set[tuple[str, int]] = set()
    for score, chunk in scored:
        line_bucket = (chunk.start_line or 0) // 12
        key = (chunk.path, line_bucket)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((score, chunk))
    return deduped


def _chunk_with_score(chunk: DocumentChunk, score: float) -> DocumentChunk:
    metadata = dict(chunk.metadata)
    metadata["score"] = round(score, 3)
    return chunk.model_copy(update={"metadata": metadata})


def _truncate_chunk(chunk: DocumentChunk, max_chars: int) -> DocumentChunk:
    if len(chunk.text) <= max_chars:
        return chunk
    return chunk.model_copy(update={"text": _truncate_text(chunk.text, max_chars)})


def _should_run_rag_for_context(issues: list[ValidationIssue], graph_context: GraphContext) -> bool:
    if any(issue_should_run_rag(issue) for issue in issues):
        return True
    return bool(
        graph_context.related_doc_refs
        or graph_context.related_api_refs
        or graph_context.retrieval_hints
    )


def _source_type_for_path(path: Path) -> RagSourceType:
    rel = _rel_path(path)
    parts = set(Path(rel).parts)
    if "openmc_examples" in parts:
        return "openmc_example"
    if "examples" in parts:
        return "project_example" if "openmc" not in rel.lower() else "openmc_example"
    if "openmc_docs" in parts:
        return "openmc_api_doc" if "api" in rel.lower() else "openmc_doc"
    if rel.startswith("docs/") or path.name.lower().startswith("readme"):
        return "project_doc"
    return "unknown"


def _message_tokens(message: str, *, max_tokens: int) -> list[str]:
    return _query_terms(message)[:max_tokens]


def _normalize_queries(queries: list[str]) -> list[str]:
    normalized: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query).split())
        if len(cleaned) < 3:
            continue
        normalized.append(cleaned)
        if len(normalized) >= DEFAULT_RAG_MAX_QUERIES:
            break
    return _dedupe(normalized)


def _query_terms(query: str) -> list[str]:
    return [
        token
        for token in _tokens(query)
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _code_tokens(code: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9_]+", code.lower()) if token]


def _expand_api_refs(api_refs: list[str]) -> list[str]:
    expanded: list[str] = []
    for ref in api_refs:
        expanded.append(ref)
        if ref.startswith("openmc.api."):
            expanded.append("openmc." + ref.removeprefix("openmc.api."))
        elif ref.startswith("openmc.") and not ref.startswith("openmc.api."):
            expanded.append("openmc.api." + ref.removeprefix("openmc."))
    return expanded


def _api_variants(ref: str) -> set[str]:
    return set(_tokens(ref)) | set(_tokens(ref.replace("openmc.api.", "openmc.")))


def _ref_matches(ref: str, values: list[str]) -> bool:
    ref_norm = ref.lower().replace("openmc.api.", "openmc.")
    for value in values:
        value_norm = value.lower().replace("openmc.api.", "openmc.")
        if ref_norm == value_norm or ref_norm in value_norm or value_norm in ref_norm:
            return True
    return False


def _evidence_key(item: RetrievedEvidence) -> tuple[str, str]:
    locator = re.sub(r":\d+-\d+", "", item.locator)
    return item.source_type, locator


def _similar_to_existing(text: str, items: list[RetrievedEvidence]) -> bool:
    tokens = set(_query_terms(text)) or set(_tokens(text))
    if not tokens:
        return False
    for item in items:
        other = set(_query_terms(item.text)) or set(_tokens(item.text))
        if not other:
            continue
        if len(tokens & other) / max(len(tokens), len(other)) > 0.92:
            return True
    return False


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 14)].rstrip() + "\n...[truncated]"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _rel_path(path: Path) -> str:
    cwd = Path.cwd().resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(cwd).as_posix()
    except ValueError:
        return resolved.as_posix()


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
