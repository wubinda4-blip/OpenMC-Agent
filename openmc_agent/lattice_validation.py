"""Shared lattice validation helpers.

These helpers keep IR validation and renderer capability checks aligned. In
particular, benchmark pin counts in ``LatticeSpec.expected_counts`` are hard
constraints transcribed from the input document; a mismatch must block export
for assembly and core renderers alike.
"""

from __future__ import annotations

from collections import Counter
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
        diff_parts: list[str] = []
        for universe_id in sorted(set(actual) | set(expected)):
            actual_count = actual.get(universe_id, 0)
            expected_count = expected.get(universe_id, 0)
            if actual_count != expected_count:
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
        total_cells = sum(len(row) for row in pattern)
        expected_total = sum(expected.values())
        shape_note = ""
        if expected_total != total_cells:
            shape_note = (
                f" (expected_counts sum {expected_total} != rows*cols {total_cells})"
            )
        schema_path = f"complex_model.lattices.{lattice.id}.universe_pattern"
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
