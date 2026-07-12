"""Shared reactor-neutral validator for concentric radial pin profiles.

Checks that cylinder/annulus/background cells within a single universe form
a continuous, non-overlapping radial profile from r=0 outward.  This catches
missing helium gaps, wrong clad inner radii, and similar structural defects
that individual cell checks cannot detect.

Called from both patch-level validation and the renderer's assembly3d guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Tolerance for radial continuity (cm).  Smaller than any realistic gap
# between physical layers but large enough to absorb float representation.
RADIAL_TOLERANCE_CM = 1e-7


@dataclass(frozen=True)
class RadialProfileIssue:
    code: str
    severity: str
    message: str
    path: str
    universe_id: str
    details: dict[str, Any] | None = None


def _radial_cells(cells: Iterable[Any]) -> list[Any]:
    """Return cells that participate in the radial profile with explicit radii.

    Only cells that have ``region_kind`` of cylinder/annulus WITH at least one
    explicit radius (``r_min_cm`` or ``r_max_cm``) are included.  Cells that
    use ``region_id`` references (surfaces defined elsewhere) are skipped
    because their radial bounds are not inline.
    """
    result = []
    for c in cells:
        kind = getattr(c, "region_kind", None)
        if kind == "background":
            result.append(c)
        elif kind in ("cylinder", "annulus"):
            r_min = getattr(c, "r_min_cm", None)
            r_max = getattr(c, "r_max_cm", None)
            if r_min is not None or r_max is not None:
                result.append(c)
    return result


def validate_concentric_radial_profile(
    universe_id: str,
    cells: list[Any],
    *,
    tolerance: float = RADIAL_TOLERANCE_CM,
) -> list[RadialProfileIssue]:
    """Validate that cells form a continuous concentric radial profile.

    A valid profile:
    - Starts at r=0 (first cylinder or annulus cell).
    - Each subsequent annulus has r_min == previous r_max (within tolerance).
    - r_min < r_max for every annulus.
    - At most one background cell, and it is the last cell.
    - The background is preceded by at least one finite-radius cell.

    Returns a list of structured issues (empty if valid).
    """
    issues: list[RadialProfileIssue] = []

    radial = _radial_cells(cells)
    if not radial:
        return issues

    # Separate background from finite-radius cells (only those with explicit radii).
    backgrounds = [c for c in radial if getattr(c, "region_kind", None) == "background"]
    finite = [c for c in radial if getattr(c, "region_kind", None) in ("cylinder", "annulus")]

    if not finite:
        return issues  # Nothing to check (e.g., all-background universe).

    # Background must be outermost.
    if backgrounds:
        bg_idx = radial.index(backgrounds[0])
        non_bg_after_bg = [
            c for c in radial[bg_idx + 1:]
            if getattr(c, "region_kind", None) in ("cylinder", "annulus")
        ]
        if non_bg_after_bg:
            issues.append(RadialProfileIssue(
                code="geometry.radial_profile.background_not_outermost",
                severity="error",
                message=(
                    f"universe {universe_id!r}: background cell is followed by "
                    f"finite-radius cell(s) {[getattr(c, 'id', '?') for c in non_bg_after_bg]}"
                ),
                path=f"universes[{universe_id}].cells",
                universe_id=universe_id,
            ))

    if len(backgrounds) > 1:
        issues.append(RadialProfileIssue(
            code="geometry.radial_profile.multiple_backgrounds",
            severity="warning",
            message=(
                f"universe {universe_id!r}: {len(backgrounds)} background cells "
                f"(expected at most 1)"
            ),
            path=f"universes[{universe_id}].cells",
            universe_id=universe_id,
        ))

    # Build the expected-continuous chain from r=0.
    expected_r = 0.0
    for i, cell in enumerate(finite):
        cid = getattr(cell, "id", f"cell[{i}]")
        kind = getattr(cell, "region_kind", "unknown")
        r_min = getattr(cell, "r_min_cm", None)
        r_max = getattr(cell, "r_max_cm", None)

        if kind == "cylinder":
            if r_max is None:
                issues.append(RadialProfileIssue(
                    code="geometry.radial_profile.cylinder_missing_r_max",
                    severity="error",
                    message=f"cell {cid!r} in {universe_id!r}: cylinder has no r_max_cm",
                    path=f"universes[{universe_id}].cells[{cid}].r_max_cm",
                    universe_id=universe_id,
                ))
                continue
            # Check continuity from expected_r.
            if r_max <= 0:
                issues.append(RadialProfileIssue(
                    code="geometry.radial_profile.radius_non_positive",
                    severity="error",
                    message=f"cell {cid!r} in {universe_id!r}: r_max_cm={r_max} <= 0",
                    path=f"universes[{universe_id}].cells[{cid}].r_max_cm",
                    universe_id=universe_id,
                ))
                continue
            if i == 0 and r_max > 0:
                expected_r = r_max  # First cylinder defines the start.
            elif i > 0:
                # A cylinder after finite cells is unusual — it should be an annulus.
                issues.append(RadialProfileIssue(
                    code="geometry.radial_profile.radius_order_invalid",
                    severity="warning",
                    message=(
                        f"cell {cid!r} in {universe_id!r}: cylinder after "
                        f"finite-radius cells (expected annulus)"
                    ),
                    path=f"universes[{universe_id}].cells[{cid}]",
                    universe_id=universe_id,
                ))
            expected_r = r_max

        elif kind == "annulus":
            if r_min is None or r_max is None:
                issues.append(RadialProfileIssue(
                    code="geometry.radial_profile.annulus_missing_bounds",
                    severity="error",
                    message=(
                        f"cell {cid!r} in {universe_id!r}: annulus missing "
                        f"r_min_cm or r_max_cm"
                    ),
                    path=f"universes[{universe_id}].cells[{cid}]",
                    universe_id=universe_id,
                    details={"r_min_cm": r_min, "r_max_cm": r_max},
                ))
                continue
            if r_min >= r_max:
                issues.append(RadialProfileIssue(
                    code="geometry.radial_profile.radius_order_invalid",
                    severity="error",
                    message=(
                        f"cell {cid!r} in {universe_id!r}: "
                        f"r_min_cm={r_min} >= r_max_cm={r_max}"
                    ),
                    path=f"universes[{universe_id}].cells[{cid}]",
                    universe_id=universe_id,
                ))
                continue
            # Check continuity: r_min should match expected_r.
            gap = abs(r_min - expected_r)
            if gap > tolerance:
                if r_min > expected_r:
                    issues.append(RadialProfileIssue(
                        code="geometry.radial_profile.gap",
                        severity="error",
                        message=(
                            f"cell {cid!r} in {universe_id!r}: radial gap of "
                            f"{gap:.6f} cm between previous r={expected_r:.6f} "
                            f"and this r_min={r_min:.6f}"
                        ),
                        path=f"universes[{universe_id}].cells[{cid}].r_min_cm",
                        universe_id=universe_id,
                        details={
                            "gap_cm": round(gap, 8),
                            "expected_r_min": expected_r,
                            "actual_r_min": r_min,
                        },
                    ))
                else:
                    issues.append(RadialProfileIssue(
                        code="geometry.radial_profile.overlap",
                        severity="error",
                        message=(
                            f"cell {cid!r} in {universe_id!r}: radial overlap — "
                            f"r_min={r_min:.6f} < previous r={expected_r:.6f}"
                        ),
                        path=f"universes[{universe_id}].cells[{cid}].r_min_cm",
                        universe_id=universe_id,
                        details={
                            "overlap_cm": round(expected_r - r_min, 8),
                            "expected_r_min": expected_r,
                            "actual_r_min": r_min,
                        },
                    ))
                continue
            expected_r = r_max

    return issues


def radial_profile_structural_issues(
    universes: list[Any],
) -> list[RadialProfileIssue]:
    """Check all universes for radial profile continuity.

    Convenience wrapper that iterates over a list of universe objects
    (either UniverseSpecPatch or UniverseSpec) and collects issues.
    """
    all_issues: list[RadialProfileIssue] = []
    for univ in universes:
        uid = getattr(univ, "universe_id", None) or getattr(univ, "id", "?")
        cells = getattr(univ, "cells", [])
        if not cells:
            continue
        # Only check universes that have radial cells.
        radial = _radial_cells(cells)
        if len(radial) < 2:
            continue  # Single-cell universes (e.g., water_cell) need no check.
        issues = validate_concentric_radial_profile(uid, cells)
        all_issues.extend(issues)
    return all_issues
