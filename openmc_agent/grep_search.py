"""Safe grep-style evidence retrieval for structured diagnostics.

This module is intentionally deterministic: callers pass structured patterns,
not shell commands, and every searched path is constrained to an allowed root.
The output is evidence for reflection/repair prompts, not a source of physical
truth.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel, ValidationIssue


GrepTrigger = Literal[
    "validation_issue",
    "runtime_issue",
    "export_xml_issue",
    "hex_lattice_issue",
    "expert_feedback",
    "manual",
]


DEFAULT_GREP_SEARCH_ROOTS = [
    "openmc_agent",
    "tests",
    "examples",
    "docs",
    "openmc_docs",
]
DEFAULT_GREP_INCLUDE_GLOBS = [
    "*.py",
    "*.md",
    "*.rst",
    "*.txt",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
]
DEFAULT_GREP_EXCLUDE_GLOBS = [
    ".git/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
    "*.pyc",
    "*.h5",
    "statepoint.*.h5",
]
DEFAULT_GREP_MAX_MATCHES = 50
DEFAULT_GREP_CONTEXT_LINES = 3
DEFAULT_GREP_MAX_FILE_BYTES = 512_000
DEFAULT_GREP_MAX_TOTAL_CHARS = 80_000
DEFAULT_GREP_MAX_PATTERNS = 32

_MIN_PATTERN_LENGTH = 2
_STOP_PATTERNS = {
    "id",
    "the",
    "and",
    "or",
    "to",
    "in",
    "of",
    "is",
    "none",
    "null",
    "true",
    "false",
    "error",
    "warning",
}
_ISSUE_ROUTES_FOR_GREP = {
    "auto_repair",
    "reflect_plan",
    "retrieval",
    "manual_review",
}


class GrepSearchRequest(AgentBaseModel):
    trigger: GrepTrigger
    issue_code: str | None = None
    schema_path: str | None = None
    concept_id: str | None = None
    patterns: list[str]
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    search_roots: list[str] = Field(default_factory=list)
    context_lines: int = DEFAULT_GREP_CONTEXT_LINES
    max_matches: int = DEFAULT_GREP_MAX_MATCHES
    case_sensitive: bool = False
    use_regex: bool = False


class GrepMatch(AgentBaseModel):
    source_type: Literal[
        "project_code",
        "test",
        "example",
        "project_doc",
        "openmc_doc",
        "unknown",
    ]
    path: str
    line_start: int
    line_end: int
    matched_pattern: str
    text: str
    symbol_hint: str | None = None
    score: float | None = None


class GrepSearchResult(AgentBaseModel):
    request: GrepSearchRequest
    matches: list[GrepMatch] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class RetrievedEvidence(AgentBaseModel):
    source_type: Literal["grep", "graph", "rag", "runtime", "validator"]
    locator: str
    text: str
    issue_code: str | None = None
    schema_path: str | None = None
    concept_id: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def grep_search(request: GrepSearchRequest) -> GrepSearchResult:
    """Run a safe bounded text search.

    ``request.patterns`` are treated as literal strings by default. Regex mode
    is supported for catalog-controlled patterns, but still runs inside Python
    or parameterized ``rg`` without shell interpolation.
    """
    normalized = _normalize_request(request)
    roots, root_warnings = _resolve_search_roots(normalized.search_roots)
    warnings = list(root_warnings)
    if not roots:
        return GrepSearchResult(
            request=normalized,
            matches=[],
            warnings=["no allowed search roots exist", *warnings],
        )

    matches: list[GrepMatch]
    truncated = False
    rg_path = shutil.which("rg")
    if rg_path:
        try:
            matches, truncated = _grep_with_rg(normalized, roots, rg_path)
        except Exception as exc:
            warnings.append(f"rg search failed; used Python fallback: {exc}")
            matches, truncated = _grep_with_python(normalized, roots)
    else:
        warnings.append("rg not found; used Python fallback")
        matches, truncated = _grep_with_python(normalized, roots)

    matches = _dedupe_matches(matches)
    matches = _sort_matches(matches, normalized)
    if len(matches) > normalized.max_matches:
        matches = matches[: normalized.max_matches]
        truncated = True
    return GrepSearchResult(
        request=normalized,
        matches=matches,
        truncated=truncated,
        warnings=warnings,
    )


def grep_request_from_issue(issue: ValidationIssue) -> GrepSearchRequest:
    """Create a bounded grep request from a structured validation issue."""
    patterns: list[str] = []
    patterns.extend(issue.grep_patterns)
    patterns.append(issue.code)
    patterns.extend(_code_tokens(issue.code))
    if issue.schema_path:
        patterns.extend(_schema_path_tokens(issue.schema_path))
    if issue.concept_id:
        patterns.append(issue.concept_id.rsplit(".", 1)[-1])
    patterns.extend(_message_tokens(issue.message))
    for hint in issue.repair_hints:
        patterns.extend(_schema_path_tokens(hint.target_path or ""))
        patterns.extend(_message_tokens(hint.message))
        if isinstance(hint.example_patch, dict):
            patterns.extend(_patch_tokens(hint.example_patch))

    if issue.code.startswith("export_xml.dangling_"):
        patterns.extend(["LatticeSpec", "fill_id", "universe_pattern", "outer_universe_id"])
    if issue.code.startswith("runtime."):
        patterns.extend(_code_tokens(issue.code.removeprefix("runtime.")))
    if issue.code.startswith("lattice.hex."):
        patterns.extend(
            ["HexLattice", "LatticeSpec", "rings", "outer_universe_id", "hexagonal_prism"]
        )

    return GrepSearchRequest(
        trigger=_trigger_for_issue(issue),
        issue_code=issue.code,
        schema_path=issue.schema_path,
        concept_id=issue.concept_id,
        patterns=_normalize_patterns(patterns),
    )


def issue_should_run_grep(issue: ValidationIssue) -> bool:
    """Return whether an issue should automatically gather grep evidence."""
    if not issue.grep_patterns:
        return False
    if issue.route_hint in _ISSUE_ROUTES_FOR_GREP:
        return True
    return issue.code.startswith(("runtime.", "export_xml.", "lattice.hex."))


def grep_result_to_evidence(result: GrepSearchResult) -> list[RetrievedEvidence]:
    """Convert grep matches to reusable retrieval evidence."""
    evidence: list[RetrievedEvidence] = []
    seen: set[tuple[str, int]] = set()
    for match in _sort_matches(_dedupe_matches(result.matches), result.request):
        key = (match.path, match.line_start // 3)
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            RetrievedEvidence(
                source_type="grep",
                locator=f"{match.path}:{match.line_start}-{match.line_end}",
                text=match.text,
                issue_code=result.request.issue_code,
                schema_path=result.request.schema_path,
                concept_id=result.request.concept_id,
                score=match.score,
                metadata={
                    "matched_pattern": match.matched_pattern,
                    "source_type": match.source_type,
                    "symbol_hint": match.symbol_hint,
                    "issue_code": result.request.issue_code,
                    "schema_path": result.request.schema_path,
                    "concept_id": result.request.concept_id,
                    "truncated": result.truncated,
                    "warnings": result.warnings,
                },
            )
        )
    return evidence


def gather_grep_evidence_for_issues(
    issues: list[ValidationIssue],
    *,
    max_issues: int = 6,
    max_evidence: int = 12,
) -> list[RetrievedEvidence]:
    """Collect bounded grep evidence for a list of issues."""
    evidence: list[RetrievedEvidence] = []
    for issue in issues[:max_issues]:
        if not issue_should_run_grep(issue):
            continue
        result = grep_search(grep_request_from_issue(issue))
        evidence.extend(grep_result_to_evidence(result))
        if len(evidence) >= max_evidence:
            return evidence[:max_evidence]
    return evidence


def format_grep_evidence(evidence: list[RetrievedEvidence], *, limit: int = 12) -> str:
    """Render evidence for an LLM prompt."""
    if not evidence:
        return ""
    lines = [
        "\n[Grep Evidence]",
        "Grep evidence is locator context only; it is not a final physical fact.",
    ]
    for item in evidence[:limit]:
        matched = item.metadata.get("matched_pattern")
        lines.append(f"- source: {item.locator}")
        if item.issue_code:
            lines.append(f"  issue_code: {item.issue_code}")
        if matched:
            lines.append(f"  matched: {matched}")
        text = item.text.rstrip()
        if text:
            lines.append("  text:")
            lines.extend(f"    {line}" for line in text.splitlines()[:12])
    return "\n".join(lines) + "\n"


def _normalize_request(request: GrepSearchRequest) -> GrepSearchRequest:
    context_lines = max(0, min(request.context_lines, 8))
    max_matches = max(1, min(request.max_matches, 200))
    return request.model_copy(
        update={
            "patterns": _normalize_patterns(request.patterns),
            "include_globs": request.include_globs or DEFAULT_GREP_INCLUDE_GLOBS,
            "exclude_globs": [*DEFAULT_GREP_EXCLUDE_GLOBS, *request.exclude_globs],
            "search_roots": request.search_roots or DEFAULT_GREP_SEARCH_ROOTS,
            "context_lines": context_lines,
            "max_matches": max_matches,
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
        if not candidate.exists() or not candidate.is_dir():
            continue
        if not any(_is_relative_to(candidate, base) for base in allowed_bases):
            warnings.append(f"skipped disallowed search root: {raw_root}")
            continue
        if candidate not in seen:
            seen.add(candidate)
            roots.append(candidate)
    return roots, warnings


def _grep_with_python(
    request: GrepSearchRequest,
    roots: list[Path],
) -> tuple[list[GrepMatch], bool]:
    regexes = [(pattern, _compile_pattern(pattern, request)) for pattern in request.patterns]
    matches: list[GrepMatch] = []
    total_chars = 0
    truncated = False
    for file_path in _iter_text_files(roots, request):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if stat.st_size > DEFAULT_GREP_MAX_FILE_BYTES:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for pattern, regex in regexes:
                if not regex.search(line):
                    continue
                match = _match_from_line(file_path, idx + 1, lines, pattern, request)
                matches.append(match)
                total_chars += len(match.text)
                if len(matches) >= request.max_matches or total_chars >= DEFAULT_GREP_MAX_TOTAL_CHARS:
                    return matches, True
                break
    return matches, truncated


def _grep_with_rg(
    request: GrepSearchRequest,
    roots: list[Path],
    rg_path: str,
) -> tuple[list[GrepMatch], bool]:
    matches: list[GrepMatch] = []
    seen: set[tuple[Path, int, str]] = set()
    for pattern in request.patterns:
        args = [
            rg_path,
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-filesize",
            str(DEFAULT_GREP_MAX_FILE_BYTES),
            "--max-count",
            str(request.max_matches),
        ]
        if not request.case_sensitive:
            args.append("--ignore-case")
        if not request.use_regex:
            args.append("--fixed-strings")
        for glob in request.include_globs:
            args.extend(["--glob", glob])
        for glob in request.exclude_globs:
            args.extend(["--glob", f"!{glob}"])
        args.append(pattern)
        args.extend(str(root) for root in roots)
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError(completed.stderr.strip() or f"rg exited {completed.returncode}")
        for raw_line in completed.stdout.splitlines():
            parsed = _parse_rg_line(raw_line)
            if parsed is None:
                continue
            path, line_number = parsed
            if _is_excluded(_safe_relative(path), request.exclude_globs):
                continue
            key = (path, line_number, pattern)
            if key in seen:
                continue
            seen.add(key)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            matches.append(_match_from_line(path, line_number, lines, pattern, request))
            if len(matches) >= request.max_matches:
                return matches, True
    return matches, False


def _parse_rg_line(raw_line: str) -> tuple[Path, int] | None:
    parts = raw_line.split(":", 2)
    if len(parts) < 3:
        return None
    try:
        return Path(parts[0]), int(parts[1])
    except ValueError:
        return None


def _iter_text_files(roots: list[Path], request: GrepSearchRequest) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path in seen:
                continue
            relative = _safe_relative(file_path)
            if _is_excluded(relative, request.exclude_globs):
                continue
            if not _matches_any(relative, request.include_globs):
                continue
            seen.add(file_path)
            files.append(file_path)
    return files


def _match_from_line(
    file_path: Path,
    line_number: int,
    lines: list[str],
    pattern: str,
    request: GrepSearchRequest,
) -> GrepMatch:
    start = max(1, line_number - request.context_lines)
    end = min(len(lines), line_number + request.context_lines)
    snippet = "\n".join(
        f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1)
    )
    path = _safe_relative(file_path)
    return GrepMatch(
        source_type=_source_type_for_path(path),
        path=path,
        line_start=start,
        line_end=end,
        matched_pattern=pattern,
        text=snippet,
        symbol_hint=_symbol_hint(lines, line_number),
        score=_match_score(path, pattern, request),
    )


def _compile_pattern(pattern: str, request: GrepSearchRequest) -> re.Pattern[str]:
    flags = 0 if request.case_sensitive else re.IGNORECASE
    raw = pattern if request.use_regex else re.escape(pattern)
    try:
        return re.compile(raw, flags)
    except re.error:
        return re.compile(re.escape(pattern), flags)


def _normalize_patterns(patterns: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for part in _split_pattern(pattern):
            candidate = part.strip()
            if not _pattern_allowed(candidate):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
            if len(normalized) >= DEFAULT_GREP_MAX_PATTERNS:
                return normalized
    return normalized


def _split_pattern(pattern: str) -> list[str]:
    if not pattern:
        return []
    parts = [pattern]
    if " " in pattern or "\n" in pattern or "\t" in pattern:
        parts.extend(re.split(r"[\s,:;()\[\]{}]+", pattern))
    return parts


def _pattern_allowed(pattern: str) -> bool:
    if len(pattern) < _MIN_PATTERN_LENGTH:
        return False
    if pattern.casefold() in _STOP_PATTERNS:
        return False
    if pattern.isdigit() and len(pattern) < 2:
        return False
    return True


def _code_tokens(code: str) -> list[str]:
    tokens = [code]
    tail = code.rsplit(".", 1)[-1]
    tokens.append(tail)
    tokens.extend(part for part in re.split(r"[._-]+", tail) if part)
    return tokens


def _schema_path_tokens(schema_path: str) -> list[str]:
    if not schema_path:
        return []
    return [part for part in re.split(r"[.\[\]/]+", schema_path) if part]


def _message_tokens(message: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|\b\d{2,}\b", message)
        if token.casefold() not in _STOP_PATTERNS
    ][:10]


def _patch_tokens(patch: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key in ("path", "from", "value"):
        value = patch.get(key)
        if isinstance(value, str):
            tokens.extend(_schema_path_tokens(value))
            tokens.append(value)
        elif isinstance(value, (int, float)):
            tokens.append(str(value))
    return tokens


def _trigger_for_issue(issue: ValidationIssue) -> GrepTrigger:
    if issue.code.startswith("runtime."):
        return "runtime_issue"
    if issue.code.startswith("export_xml."):
        return "export_xml_issue"
    if issue.code.startswith("lattice.hex."):
        return "hex_lattice_issue"
    return "validation_issue"


def _dedupe_matches(matches: list[GrepMatch]) -> list[GrepMatch]:
    deduped: list[GrepMatch] = []
    seen: set[tuple[str, int, int]] = set()
    for match in matches:
        key = (match.path, match.line_start, match.line_end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)
    return deduped


def _sort_matches(matches: list[GrepMatch], request: GrepSearchRequest) -> list[GrepMatch]:
    schema_tokens = set(_schema_path_tokens(request.schema_path or ""))
    return sorted(
        matches,
        key=lambda match: (
            0 if match.matched_pattern in schema_tokens else 1,
            _source_rank(match.source_type),
            -(match.score or 0.0),
            match.path,
            match.line_start,
        ),
    )


def _source_rank(source_type: str) -> int:
    return {
        "project_code": 0,
        "test": 1,
        "example": 2,
        "project_doc": 3,
        "openmc_doc": 4,
        "unknown": 5,
    }.get(source_type, 5)


def _source_type_for_path(path: str) -> str:
    normalized = path.replace(os.sep, "/")
    if normalized.startswith("openmc_agent/"):
        return "project_code"
    if normalized.startswith("tests/"):
        return "test"
    if normalized.startswith("examples/"):
        return "example"
    if normalized.startswith("docs/"):
        return "project_doc"
    if normalized.startswith("openmc_docs/"):
        return "openmc_doc"
    return "unknown"


def _match_score(path: str, pattern: str, request: GrepSearchRequest) -> float:
    score = 1.0
    if request.schema_path and pattern in _schema_path_tokens(request.schema_path):
        score += 2.0
    if request.issue_code and request.issue_code in pattern:
        score += 1.0
    if _source_type_for_path(path) == "project_code":
        score += 0.5
    return score


def _symbol_hint(lines: list[str], line_number: int) -> str | None:
    start = max(0, line_number - 20)
    for idx in range(line_number - 1, start - 1, -1):
        stripped = lines[idx].strip()
        if stripped.startswith(("def ", "class ")):
            return stripped.split(":", 1)[0]
    return None


def _matches_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in globs)


def _is_excluded(path: str, globs: list[str]) -> bool:
    normalized = path.replace(os.sep, "/")
    parts = set(normalized.split("/"))
    if {".git", ".venv", "venv", "__pycache__", ".pytest_cache"} & parts:
        return True
    return _matches_any(normalized, globs)


def _safe_relative(path: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(Path.cwd().resolve(strict=False)))
    except ValueError:
        return str(path.resolve(strict=False))


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
