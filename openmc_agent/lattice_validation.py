"""Shared lattice validation helpers.

These helpers keep IR validation and renderer capability checks aligned. In
particular, benchmark pin counts in ``LatticeSpec.expected_counts`` are hard
constraints transcribed from the input document; a mismatch must block export
for assembly and core renderers alike.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.schemas import LatticeSpec, ValidationIssue


def lattice_actual_counts(lattice: LatticeSpec) -> Counter[str]:
    """Return universe occurrence counts in an expanded lattice pattern."""
    return Counter(universe_id for row in lattice.universe_pattern for universe_id in row)


def lattice_pin_count_issues(
    lattices: Iterable[LatticeSpec],
    *,
    message_style: str = "validator",
) -> list[ValidationIssue]:
    """Check expanded ``universe_pattern`` values against ``expected_counts``.

    Lattices without ``expected_counts`` or without an expanded pattern are
    skipped, preserving legacy plans and allowing the separate missing-pattern
    diagnostics to report absent maps.
    """
    issues: list[ValidationIssue] = []
    for lattice in lattices:
        expected = lattice.expected_counts
        pattern = lattice.universe_pattern
        if not expected or not pattern:
            continue
        actual = lattice_actual_counts(lattice)
        total_cells = sum(len(row) for row in pattern)
        expected_total = sum(expected.values())

        # Classify diffs: a universe the LLM omitted from expected_counts
        # (actual>0 but not listed) vs a real value mismatch (expected listed
        # a count that differs from the pattern).
        missing_universes: list[str] = []
        value_mismatches: list[str] = []
        diff_parts: list[str] = []
        for universe_id in sorted(set(actual) | set(expected)):
            actual_count = actual.get(universe_id, 0)
            expected_count = expected.get(universe_id, 0)
            if actual_count == expected_count:
                continue
            if actual_count > 0 and universe_id not in expected:
                missing_universes.append(universe_id)
            else:
                value_mismatches.append(universe_id)
            if message_style == "renderer":
                diff_parts.append(
                    f"{universe_id}: expected {expected_count}, got {actual_count}"
                )
            else:
                diff_parts.append(
                    f"{universe_id}: actual={actual_count} "
                    f"expected={expected_count} "
                    f"(diff {actual_count - expected_count:+d})"
                )
        if not diff_parts:
            continue

        schema_path = f"complex_model.lattices.{lattice.id}.universe_pattern"

        # Incomplete expected_counts (omitted universes) with a self-consistent
        # pattern is downgraded to a warning: the LLM often forgets to list
        # every universe, but the pattern itself is the ground truth and must
        # not block rendering. A real value mismatch or a non-self-consistent
        # pattern stays an error.
        pattern_self_consistent = bool(actual) and sum(actual.values()) == total_cells
        if missing_universes and not value_mismatches and pattern_self_consistent:
            missing_note = ", ".join(missing_universes)
            issues.append(
                issue_from_catalog(
                    "lattice.expected_counts_incomplete",
                    message=(
                        f"lattice {lattice.id!r} expected_counts omits universes "
                        f"present in the pattern: {missing_note}. Pattern is "
                        f"self-consistent (sum {total_cells} == rows*cols); "
                        f"accepted with warning. Consider adding the missing "
                        f"counts to expected_counts for explicit cross-check."
                    ),
                    schema_path=schema_path,
                    route_hint="auto_repair",
                )
            )
            continue

        shape_note = ""
        if expected_total != total_cells:
            shape_note = (
                f" (expected_counts sum {expected_total} != rows*cols {total_cells})"
            )
        if message_style == "renderer":
            message = (
                f"lattice {lattice.id!r} pin counts do not match expected_counts: "
                + "; ".join(diff_parts)
                + shape_note
            )
        else:
            message = (
                f"lattice {lattice.id!r} pin counts mismatch -- "
                + "; ".join(diff_parts)
                + shape_note
                + ". Re-read the input document's per-ring/per-region pin "
                "description and correct universe_pattern, or fix expected_counts "
                "only if the pattern is already correct."
            )
        issues.append(
            issue_from_catalog(
                "lattice.pin_count_mismatch",
                message=message,
                schema_path=schema_path,
                route_hint="reflect_plan",
            )
        )
    return issues


# Pin-map repair localization -------------------------------------------------

# Keywords that bind a canonical pin map section to a lattice id, checked in
# priority order. The first keyword that appears in the lattice id selects the
# matching section when the requirement carries more than one map.
_PIN_MAP_KEYWORDS: tuple[str, ...] = ("mox", "uo2", "triso")

# A single ASCII letter, optionally followed by one digit: A, B, C, G, F, U ...
_PIN_SYMBOL_RE = re.compile(r"[A-Za-z]\d?")


@dataclass
class CanonicalPinMap:
    """A canonical pin map parsed from the input document.

    ``rows`` carries the symbol grid already mapped to universe ids via
    ``symbol_map`` so callers can compare it directly against a lattice's
    ``universe_pattern``. ``raw_text`` preserves the original ``R01..R17`` block
    so it can be echoed back to the LLM verbatim.
    """

    rows: list[list[str]]
    symbol_map: dict[str, str]
    raw_text: str


def _parse_pin_map_rows(body: str) -> list[list[str]]:
    """Return symbol tokens for each ``R01: A B C`` line in ``body`` (empty if none)."""
    rows: list[list[str]] = []
    for line in body.splitlines():
        match = re.match(r"\s*R\s*(\d+)\s*[:：]\s*(.*)", line)
        if not match:
            continue
        tokens = match.group(2).split()
        if tokens:
            rows.append(tokens)
    return rows


def _parse_symbol_table(context: str) -> dict[str, str]:
    """Parse ``| A | ... | `universe_id` |`` rows into a symbol -> universe map."""
    symbols: dict[str, str] = {}
    for line in context.splitlines():
        if "|" not in line:
            continue
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        if len(cells) < 2:
            continue
        symbol = cells[0]
        if not _PIN_SYMBOL_RE.fullmatch(symbol):
            continue
        # Prefer a backtick-quoted universe id; fall back to the last cell.
        quoted = re.search(r"`([^`]+)`", line)
        universe = quoted.group(1) if quoted else cells[-1]
        universe = universe.strip().strip("`")
        if universe and not set(universe) <= {"-"}:
            symbols[symbol] = universe
    return symbols


def _detect_keyword(*texts: str) -> str | None:
    combined = " ".join(texts).lower()
    for keyword in _PIN_MAP_KEYWORDS:
        if keyword in combined:
            return keyword
    return None


def _select_pin_map_section(
    candidates: list[dict[str, object]],
    lattice_id: str,
) -> dict[str, object] | None:
    target = _detect_keyword(lattice_id)
    if target is not None:
        for candidate in candidates:
            if candidate["keyword"] == target:
                return candidate
    # No keyword hint or no matching section: use the only (or first) candidate.
    return candidates[0] if candidates else None


def extract_canonical_pin_map(
    requirement: str,
    lattice_id: str,
) -> CanonicalPinMap | None:
    """Parse the lattice's canonical pin map from the input document.

    Looks for fenced ```` ``` ```` code blocks whose body is ``R01: ...`` rows,
    then reads the nearest preceding symbol table to map symbols to universe
    ids. When the document carries multiple maps (e.g. UO2 + MOX), the lattice
    id selects the matching section. Returns ``None`` when no map is found.
    """
    candidates: list[dict[str, object]] = []
    for block in re.finditer(r"```(?:text)?\s*\n(.*?)```", requirement, re.DOTALL):
        body = block.group(1)
        symbol_rows = _parse_pin_map_rows(body)
        if not symbol_rows:
            continue
        context = requirement[: block.start()][-1200:]
        symbols = _parse_symbol_table(context)
        # The section keyword (mox/uo2/...) is read from the universe ids in the
        # symbol table, not from surrounding prose: a paragraph that mentions
        # 'MOX' next to the UO2 map must not re-bind the UO2 section.
        keyword = _detect_keyword(*symbols.values()) or _detect_keyword(context)
        candidates.append(
            {"rows": symbol_rows, "symbols": symbols, "keyword": keyword, "raw": body}
        )
    selected = _select_pin_map_section(candidates, lattice_id)
    if selected is None:
        return None
    symbols = dict(selected["symbols"])  # type: ignore[arg-type]
    mapped_rows = [
        [symbols.get(token, token) for token in row]
        for row in selected["rows"]  # type: ignore[index]
    ]
    return CanonicalPinMap(
        rows=mapped_rows,
        symbol_map=symbols,
        raw_text=str(selected["raw"]),
    )


def lattice_cell_mismatches(
    actual: list[list[str]],
    canonical: list[list[str]],
) -> list[tuple[int, int, str, str]]:
    """Compare an expanded lattice pattern against the canonical map cell by cell.

    Returns ``(row_1indexed, col_1indexed, expected, actual)`` for each cell
    where the universe id differs from the canonical map. Returns an empty list
    when the grids have different shapes (the count diff already flags that).
    """
    if len(actual) != len(canonical) or not actual:
        return []
    for actual_row, canonical_row in zip(actual, canonical):
        if len(actual_row) != len(canonical_row):
            return []
    diffs: list[tuple[int, int, str, str]] = []
    for row_index, (actual_row, canonical_row) in enumerate(zip(actual, canonical), start=1):
        for col_index, (actual_cell, expected_cell) in enumerate(
            zip(actual_row, canonical_row), start=1
        ):
            if actual_cell != expected_cell:
                diffs.append((row_index, col_index, expected_cell, actual_cell))
    return diffs


def lattice_id_from_schema_path(schema_path: str | None) -> str | None:
    """Extract the lattice id from ``complex_model.lattices.<id>.universe_pattern``."""
    if not schema_path:
        return None
    parts = schema_path.split(".")
    try:
        return parts[parts.index("lattices") + 1]
    except (ValueError, IndexError):
        return None


def canonical_pin_map_rows(
    lattice: LatticeSpec,
    requirement: str,
) -> list[list[str]] | None:
    """Return the canonical universe grid to overwrite ``lattice``, or None.

    Deterministic repair source: when the input document carries a canonical
    pin map for this lattice, the parsed grid is the ground truth and replaces
    the LLM's expanded ``universe_pattern`` directly. The LLM cannot reliably
    hand-edit a dense matrix even with cell-level coordinates -- repeated
    reflections return a byte-identical wrong pattern -- so for pin-count
    mismatches the canonical map is applied as a JSON Patch by the caller
    (``auto_repair_lattice_structure``) instead of asking the LLM again.

    Returns None when there is no requirement, no parseable canonical map, the
    canonical shape does not match the lattice, or the pattern already equals
    the canonical grid (nothing to patch).
    """
    if not requirement or not lattice.universe_pattern:
        return None
    canonical = extract_canonical_pin_map(requirement, lattice.id)
    if canonical is None:
        return None
    if len(canonical.rows) != len(lattice.universe_pattern):
        return None
    for canonical_row, pattern_row in zip(canonical.rows, lattice.universe_pattern):
        if len(canonical_row) != len(pattern_row):
            return None
    target = [list(row) for row in canonical.rows]
    if target == [list(row) for row in lattice.universe_pattern]:
        return None
    return target


# Substrings that mark a ``requires_human_confirmation`` / capability reason as
# describing an agent-fixable structural defect rather than a physics question.
STRUCTURAL_ERROR_CONFIRMATION_MARKERS: tuple[str, ...] = (
    "pin count",
    "mismatch",
    "expected_counts",
    "references empty",
    "references missing",
    "missing universe",
    "universe_pattern",
)


def is_structural_error_confirmation(text: str) -> bool:
    """Return True when ``text`` describes a structural defect, not a physics question.

    LLMs sometimes write structural errors (pin-count mismatches, missing
    universe references) into ``requires_human_confirmation``. Those are
    agent-fixable defects, not facts an expert can supply, so they must not be
    surfaced as expert questions.
    """
    lowered = (text or "").lower()
    return any(marker in lowered for marker in STRUCTURAL_ERROR_CONFIRMATION_MARKERS)
