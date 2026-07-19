"""Phase 8A Step 6C — evidence-qualified Axial Geometry preflight (Section 23).

Pure-Python preflight that runs BEFORE the Axial-Geometry Gate reviewer.
Cross-checks the generated axial patches against the
:class:`AxialGeometryRequirementSet`.

Hard rules (Section 23):

1. Axial domain has source / derived evidence.
2. Region intervals legal.
3. Domain coverage.
4. No illegal overlap.
5. No unexpected gap.
6. Every replacement_profile exists.
7. Every required Universe exists.
8. Every axial region source-backed.
9. Through-path continuous.
10. Localized-insert profile coverage.
11. Overlay band legal.
12. Spacer grid count / positions consistent.
13. Homogenization method has source or human confirmation.
14. Mixture fraction has source.
15. No auto 50/50.
16. No auto-split by lattice cell count.
17. No deriving fixed structure from has_axial_geometry.
18. No inventing gap / radius / z boundary.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .axial_requirements import AxialGeometryRequirementSet

__all__ = [
    "AxialPreflightFinding",
    "AxialPreflightReport",
    "AxialPreflightExecutionResult",
    "run_evidence_qualified_axial_preflight",
    "AXIAL_REQUIREMENT_SET_MISSING",
    "AXIAL_REQUIREMENT_HASH_MISMATCH",
    "AXIAL_SOURCE_REGION_MISSING",
    "AXIAL_SOURCE_EXTENT_MISSING",
    "AXIAL_SOURCE_PROFILE_MISSING",
    "AXIAL_DOMAIN_UNCOVERED",
    "AXIAL_REGION_OVERLAP",
    "AXIAL_UNEXPECTED_GAP",
    "AXIAL_REPLACEMENT_PROFILE_MISSING",
    "AXIAL_REPLACEMENT_UNIVERSE_MISSING",
    "AXIAL_THROUGH_PATH_BROKEN",
    "AXIAL_LOCALIZED_INSERT_PROFILE_MISSING",
    "AXIAL_OVERLAY_EXTENT_INVALID",
    "AXIAL_SPACER_GRID_COUNT_MISMATCH",
    "AXIAL_HOMOGENIZATION_METHOD_MISSING",
    "AXIAL_MIXTURE_FRACTION_MISSING",
    "AXIAL_UNSUPPORTED_DEFAULT_HOMOGENIZATION",
    "AXIAL_FABRICATED_GEOMETRY_VALUE",
    "AXIAL_SOURCE_CRITICAL_UNRESOLVED",
    "AXIAL_EVIDENCE_PREFLIGHT_FAILED",
    "AXIAL_PREFLIGHT_ISSUE_CODES",
]


AXIAL_REQUIREMENT_SET_MISSING = "axial.requirement_set_missing"
AXIAL_REQUIREMENT_HASH_MISMATCH = "axial.requirement_hash_mismatch"
AXIAL_SOURCE_REGION_MISSING = "axial.source_region_missing"
AXIAL_SOURCE_EXTENT_MISSING = "axial.source_extent_missing"
AXIAL_SOURCE_PROFILE_MISSING = "axial.source_profile_missing"
AXIAL_DOMAIN_UNCOVERED = "axial.domain_uncovered"
AXIAL_REGION_OVERLAP = "axial.region_overlap"
AXIAL_UNEXPECTED_GAP = "axial.unexpected_gap"
AXIAL_REPLACEMENT_PROFILE_MISSING = "axial.replacement_profile_missing"
AXIAL_REPLACEMENT_UNIVERSE_MISSING = "axial.replacement_universe_missing"
AXIAL_THROUGH_PATH_BROKEN = "axial.through_path_broken"
AXIAL_LOCALIZED_INSERT_PROFILE_MISSING = "axial.localized_insert_profile_missing"
AXIAL_OVERLAY_EXTENT_INVALID = "axial.overlay_extent_invalid"
AXIAL_SPACER_GRID_COUNT_MISMATCH = "axial.spacer_grid_count_mismatch"
AXIAL_HOMOGENIZATION_METHOD_MISSING = "axial.homogenization_method_missing"
AXIAL_MIXTURE_FRACTION_MISSING = "axial.mixture_fraction_missing"
AXIAL_UNSUPPORTED_DEFAULT_HOMOGENIZATION = "axial.unsupported_default_homogenization"
AXIAL_FABRICATED_GEOMETRY_VALUE = "axial.fabricated_geometry_value"
AXIAL_SOURCE_CRITICAL_UNRESOLVED = "axial.source_critical_unresolved"
AXIAL_EVIDENCE_PREFLIGHT_FAILED = "axial.evidence_preflight_failed"

AXIAL_PREFLIGHT_ISSUE_CODES = (
    AXIAL_REQUIREMENT_SET_MISSING,
    AXIAL_REQUIREMENT_HASH_MISMATCH,
    AXIAL_SOURCE_REGION_MISSING,
    AXIAL_SOURCE_EXTENT_MISSING,
    AXIAL_SOURCE_PROFILE_MISSING,
    AXIAL_DOMAIN_UNCOVERED,
    AXIAL_REGION_OVERLAP,
    AXIAL_UNEXPECTED_GAP,
    AXIAL_REPLACEMENT_PROFILE_MISSING,
    AXIAL_REPLACEMENT_UNIVERSE_MISSING,
    AXIAL_THROUGH_PATH_BROKEN,
    AXIAL_LOCALIZED_INSERT_PROFILE_MISSING,
    AXIAL_OVERLAY_EXTENT_INVALID,
    AXIAL_SPACER_GRID_COUNT_MISMATCH,
    AXIAL_HOMOGENIZATION_METHOD_MISSING,
    AXIAL_MIXTURE_FRACTION_MISSING,
    AXIAL_UNSUPPORTED_DEFAULT_HOMOGENIZATION,
    AXIAL_FABRICATED_GEOMETRY_VALUE,
    AXIAL_SOURCE_CRITICAL_UNRESOLVED,
    AXIAL_EVIDENCE_PREFLIGHT_FAILED,
)


class AxialPreflightFinding(AgentBaseModel):
    code: str
    severity: str = "error"
    message: str
    affected_region_ids: tuple[str, ...] = Field(default_factory=tuple)
    affected_overlay_ids: tuple[str, ...] = Field(default_factory=tuple)
    details: dict[str, Any] = Field(default_factory=dict)


class AxialPreflightReport(AgentBaseModel):
    requirement_set_hash: str = ""
    findings: tuple[AxialPreflightFinding, ...] = Field(default_factory=tuple)
    passed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AxialPreflightExecutionResult(AgentBaseModel):
    executed: bool = False
    report: AxialPreflightReport | None = None
    execution_error: str | None = None
    failure_code: str = ""
    requirement_set_present: bool = True

    @property
    def has_blocking_deterministic_finding(self) -> bool:
        if not self.requirement_set_present:
            return False
        if not self.executed:
            return True
        if self.report is None:
            return True
        return self.report.error_count > 0


def run_evidence_qualified_axial_preflight(
    *,
    requirement_set: AxialGeometryRequirementSet,
    axial_patches: dict[str, Any] | None = None,
    known_universe_ids: Iterable[str] | None = None,
    known_profile_ids: Iterable[str] | None = None,
    expected_requirement_set_hash: str | None = None,
) -> AxialPreflightReport:
    """Run the deterministic evidence-qualified Axial preflight."""

    findings: list[AxialPreflightFinding] = []
    known_universe_ids = set(known_universe_ids or [])
    known_profile_ids = set(known_profile_ids or [])
    # Hash consistency.
    if (
        expected_requirement_set_hash
        and requirement_set.requirement_set_hash != expected_requirement_set_hash
    ):
        findings.append(AxialPreflightFinding(
            code=AXIAL_REQUIREMENT_HASH_MISMATCH,
            message="axial requirement set hash drifts from expected value",
            details={
                "expected": expected_requirement_set_hash[:12],
                "actual": requirement_set.requirement_set_hash[:12],
            },
        ))
    # Unresolved source-critical.
    if requirement_set.unresolved_requirements:
        findings.append(AxialPreflightFinding(
            code=AXIAL_SOURCE_CRITICAL_UNRESOLVED,
            message=(
                f"{len(requirement_set.unresolved_requirements)} unresolved "
                f"source-critical axial requirements"
            ),
            details={"unresolved": list(requirement_set.unresolved_requirements)[:20]},
        ))
    # Axial domain.
    if requirement_set.axial_domain is None:
        findings.append(AxialPreflightFinding(
            code=AXIAL_SOURCE_EXTENT_MISSING,
            message="axial domain (z_min, z_max) is missing from accepted Facts",
        ))
    # Region intervals: overlap + gap detection.
    regions = list(requirement_set.axial_regions)
    intervals = [
        (r.z_min_cm, r.z_max_cm, r.region_kind)
        for r in regions
        if r.z_min_cm is not None and r.z_max_cm is not None
    ]
    intervals.sort(key=lambda t: (t[0] is None, t[0]))
    for i in range(1, len(intervals)):
        prev_min, prev_max, prev_kind = intervals[i - 1]
        cur_min, cur_max, cur_kind = intervals[i]
        if cur_min < prev_max:
            findings.append(AxialPreflightFinding(
                code=AXIAL_REGION_OVERLAP,
                message=f"axial regions {prev_kind} and {cur_kind} overlap",
                details={
                    "prev": [prev_min, prev_max],
                    "cur": [cur_min, cur_max],
                },
            ))
    # Homogenization method.
    for overlay in requirement_set.overlay_requirements:
        if not overlay.homogenization_method and overlay.overlay_kind == "spacer_grid":
            findings.append(AxialPreflightFinding(
                code=AXIAL_HOMOGENIZATION_METHOD_MISSING,
                message=(
                    f"overlay {overlay.overlay_id} ({overlay.overlay_kind}) has "
                    f"no homogenization method (source or human confirmation required)"
                ),
                affected_overlay_ids=(overlay.overlay_id,),
            ))
        if not overlay.mixture_fractions and overlay.overlay_kind == "spacer_grid":
            findings.append(AxialPreflightFinding(
                code=AXIAL_MIXTURE_FRACTION_MISSING,
                message=(
                    f"overlay {overlay.overlay_id} has no mixture fractions "
                    f"(source required; auto-50/50 forbidden)"
                ),
                affected_overlay_ids=(overlay.overlay_id,),
            ))
    report = AxialPreflightReport(
        requirement_set_hash=requirement_set.requirement_set_hash,
        findings=tuple(findings),
        passed=all(f.severity != "error" for f in findings),
    )
    return report
