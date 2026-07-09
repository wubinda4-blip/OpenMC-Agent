"""Requirement reference resolver.

When a user requirement only references a local input file (e.g.
"Build the VERA3 3A model described in Input/VERA3_problem.md"), the
incremental feature detector cannot see the file content and under-detects
structural features (17x17 lattice, guide tubes, spacer grids, axial layers).
This causes the task planner to omit ``pin_map`` / ``axial_overlays`` patches,
which then makes the assembler fail with ``assembly.missing_patch``.

This module resolves such references by inlining the content of *local* files
with an allow-listed suffix into a resolved requirement string. It never reads
remote URLs, never reads files with disallowed suffixes, and truncates overly
large files.

Safety boundary
---------------
- Only local files under the working tree are read.
- Only ``.md`` / ``.txt`` / ``.json`` suffixes are read by default.
- File content is truncated to ``max_file_chars`` to bound prompt size.
- Missing / disallowed / oversized files produce warnings, never exceptions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openmc_agent.schemas import AgentBaseModel


class ResolvedRequirement(AgentBaseModel):
    """Result of resolving file references in a requirement string."""

    original_requirement: str
    resolved_requirement: str
    referenced_files: list[str] = []
    file_excerpt_by_path: dict[str, str] = {}
    warnings: list[str] = []


_DEFAULT_ALLOWED_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".json")
_DEFAULT_MAX_FILE_CHARS: int = 20_000

_URL_SCHEME_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"]+", re.IGNORECASE)
# Match filesystem-like paths ending in an allowed suffix. We deliberately
# keep this permissive (word chars, dots, dashes, slashes). URLs are stripped
# from the text before matching so the resolver never attempts network reads.
_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"                          # left boundary
    r"(?:\.{1,2}/|/)?"                           # optional ./ ../ or leading /
    r"(?:[\w.\-]+/)*"                            # optional directory components
    r"[\w.\-]+"                                  # final path component
    r"\.(?:md|txt|json|py|csv|xml|yaml|yml|pdf|tex|h5|hdf5)\b",  # broad suffixes
    re.IGNORECASE,
)
# Suffixes that are actually read and inlined. Others produce a warning.
_READABLE_SUFFIXES = {".md", ".txt", ".json"}


def _extract_candidate_paths(text: str) -> list[str]:
    """Extract candidate local file paths from ``text``.

    Returns paths in order of appearance, deduplicated. URL-like tokens are
    stripped from the text before matching so the resolver never tries to read
    remote resources.
    """
    # Remove URL-like tokens first so their path tails don't match.
    sanitized = _URL_SCHEME_RE.sub(" ", text)
    seen: set[str] = set()
    out: list[str] = []
    for match in _PATH_TOKEN_RE.finditer(sanitized):
        # Only strip trailing punctuation; leading "./" and "../" are valid
        # path prefixes and must not be stripped.
        token = match.group(0).rstrip(",.;:()[]{}\"'")
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _read_file_excerpt(
    path: Path,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    """Read up to ``max_chars`` characters from ``path``.

    Returns ``(content, truncated)``.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    if len(raw) > max_chars:
        return raw[:max_chars], True
    return raw, False


def resolve_requirement_references(
    requirement: str,
    *,
    base_dir: str | Path | None = None,
    max_file_chars: int = _DEFAULT_MAX_FILE_CHARS,
    allowed_suffixes: tuple[str, ...] = _DEFAULT_ALLOWED_SUFFIXES,
) -> ResolvedRequirement:
    """Resolve local file references in ``requirement``.

    Parameters
    ----------
    requirement
        The original requirement text, which may reference local files.
    base_dir
        Base directory for resolving relative paths. Defaults to the current
        working directory.
    max_file_chars
        Maximum number of characters to inline from each referenced file.
        Files exceeding this are truncated and a warning is recorded.
    allowed_suffixes
        File suffixes that may be read. Defaults to ``(".md", ".txt", ".json")``.

    Returns
    -------
    ResolvedRequirement
        The resolved requirement with inlined file content, the list of
        referenced files, per-path excerpts, and any warnings.
    """
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    candidates = _extract_candidate_paths(requirement or "")

    referenced_files: list[str] = []
    excerpts: dict[str, str] = {}
    warnings: list[str] = []
    blocks: list[str] = []

    # Determine which suffixes are readable. If the caller passes a custom
    # allowed_suffixes, use that; otherwise use the module default.
    readable_suffixes = set(
        s.lower() for s in (allowed_suffixes or _DEFAULT_ALLOWED_SUFFIXES)
    )

    for candidate in candidates:
        candidate_path = Path(candidate)
        resolved_path = candidate_path if candidate_path.is_absolute() else base / candidate_path

        suffix = resolved_path.suffix.lower()
        if suffix not in readable_suffixes:
            warnings.append(
                f"requirement_reference.suffix_not_allowed: {candidate} "
                f"(suffix {suffix!r} not readable; allowed={sorted(readable_suffixes)})"
            )
            continue

        if not resolved_path.exists():
            warnings.append(
                f"requirement_reference.file_not_found: {candidate} "
                f"(resolved to {resolved_path})"
            )
            continue

        try:
            content, truncated = _read_file_excerpt(resolved_path, max_chars=max_file_chars)
        except Exception as exc:
            warnings.append(
                f"requirement_reference.read_failed: {candidate} ({type(exc).__name__}: {exc})"
            )
            continue

        referenced_files.append(candidate)
        excerpts[candidate] = content
        if truncated:
            warnings.append(
                f"requirement_reference.file_truncated: {candidate} "
                f"(>{max_file_chars} chars, truncated)"
            )

        blocks.append(
            f"\n--- Referenced file: {candidate} ---\n{content}\n--- End referenced file ---"
        )

    if blocks:
        resolved = (requirement or "").rstrip() + "\n" + "\n".join(blocks)
    else:
        resolved = requirement or ""

    return ResolvedRequirement(
        original_requirement=requirement or "",
        resolved_requirement=resolved,
        referenced_files=referenced_files,
        file_excerpt_by_path=excerpts,
        warnings=warnings,
    )


def resolved_requirement_summary(resolved: ResolvedRequirement) -> dict[str, Any]:
    """Return a compact, trace-safe summary of a :class:`ResolvedRequirement`.

    The full resolved text is intentionally excluded so traces stay small;
    callers that need the full text should use ``resolved.resolved_requirement``
    directly.
    """
    return {
        "original_requirement_chars": len(resolved.original_requirement),
        "resolved_requirement_chars": len(resolved.resolved_requirement),
        "referenced_files": list(resolved.referenced_files),
        "requirement_resolution_warnings": list(resolved.warnings),
    }


__all__ = [
    "ResolvedRequirement",
    "resolve_requirement_references",
    "resolved_requirement_summary",
]
