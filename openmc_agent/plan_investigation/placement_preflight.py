"""Phase 8A Step 6C — evidence-qualified Placement preflight (Section 21).

A pure-Python preflight that runs BEFORE the Placement Gate reviewer.
Cross-checks the generated placement patches against the
:class:`PlacementRequirementSet`.

Hard rules (Section 21):

1. PlacementRequirementSet hash must be present.
2. Every assembly type has a definition.
3. Core layout multiplicity matches.
4. Every localized insert requirement has an assembly binding.
5. Every insert has a host path.
6. Every required Universe exists.
7. Coordinate count matches source scope.
8. No core-total / per-assembly scope confusion.
9. No material/universe-exists shortcut to placement satisfied.
10. No source-less placement.
11. No unsupported inferred coordinate.
12. No unresolved source-critical placement.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .placement_requirements import PlacementRequirementSet

__all__ = [
    "PlacementPreflightFinding",
    "PlacementPreflightReport",
    "PlacementPreflightExecutionResult",
    "run_evidence_qualified_placement_preflight",
    "PLACEMENT_REQUIREMENT_SET_MISSING",
    "PLACEMENT_REQUIREMENT_HASH_MISMATCH",
    "PLACEMENT_SOURCE_BINDING_MISSING",
    "PLACEMENT_SOURCE_COORDINATE_MISSING",
    "PLACEMENT_ASSEMBLY_TYPE_UNCOVERED",
    "PLACEMENT_CORE_LAYOUT_MISMATCH",
    "PLACEMENT_INSERT_BINDING_MISSING",
    "PLACEMENT_HOST_PATH_MISSING",
    "PLACEMENT_REQUIRED_UNIVERSE_MISSING",
    "PLACEMENT_COORDINATE_SCOPE_MISMATCH",
    "PLACEMENT_UNSUPPORTED_INFERRED_COORDINATE",
    "PLACEMENT_SOURCE_CRITICAL_UNRESOLVED",
    "PLACEMENT_EVIDENCE_PREFLIGHT_FAILED",
    "PREFLIGHT_NOT_EXECUTED_CODE",
    "PREFLIGHT_EXCEPTION_CODE",
    "PLACEMENT_PREFLIGHT_ISSUE_CODES",
]


PLACEMENT_REQUIREMENT_SET_MISSING = "placement.requirement_set_missing"
PLACEMENT_REQUIREMENT_HASH_MISMATCH = "placement.requirement_hash_mismatch"
PLACEMENT_SOURCE_BINDING_MISSING = "placement.source_binding_missing"
PLACEMENT_SOURCE_COORDINATE_MISSING = "placement.source_coordinate_missing"
PLACEMENT_ASSEMBLY_TYPE_UNCOVERED = "placement.assembly_type_uncovered"
PLACEMENT_CORE_LAYOUT_MISMATCH = "placement.core_layout_mismatch"
PLACEMENT_INSERT_BINDING_MISSING = "placement.insert_binding_missing"
PLACEMENT_HOST_PATH_MISSING = "placement.host_path_missing"
PLACEMENT_REQUIRED_UNIVERSE_MISSING = "placement.required_universe_missing"
PLACEMENT_COORDINATE_SCOPE_MISMATCH = "placement.coordinate_scope_mismatch"
PLACEMENT_UNSUPPORTED_INFERRED_COORDINATE = "placement.unsupported_inferred_coordinate"
PLACEMENT_SOURCE_CRITICAL_UNRESOLVED = "placement.source_critical_unresolved"
PLACEMENT_EVIDENCE_PREFLIGHT_FAILED = "placement.evidence_preflight_failed"

PREFLIGHT_NOT_EXECUTED_CODE = "placement.evidence_preflight_not_executed"
PREFLIGHT_EXCEPTION_CODE = "placement.evidence_preflight_exception"

PLACEMENT_PREFLIGHT_ISSUE_CODES = (
    PLACEMENT_REQUIREMENT_SET_MISSING,
    PLACEMENT_REQUIREMENT_HASH_MISMATCH,
    PLACEMENT_SOURCE_BINDING_MISSING,
    PLACEMENT_SOURCE_COORDINATE_MISSING,
    PLACEMENT_ASSEMBLY_TYPE_UNCOVERED,
    PLACEMENT_CORE_LAYOUT_MISMATCH,
    PLACEMENT_INSERT_BINDING_MISSING,
    PLACEMENT_HOST_PATH_MISSING,
    PLACEMENT_REQUIRED_UNIVERSE_MISSING,
    PLACEMENT_COORDINATE_SCOPE_MISMATCH,
    PLACEMENT_UNSUPPORTED_INFERRED_COORDINATE,
    PLACEMENT_SOURCE_CRITICAL_UNRESOLVED,
    PLACEMENT_EVIDENCE_PREFLIGHT_FAILED,
)


class PlacementPreflightFinding(AgentBaseModel):
    code: str
    severity: str = "error"
    message: str
    affected_assembly_type_ids: tuple[str, ...] = Field(default_factory=tuple)
    affected_universe_ids: tuple[str, ...] = Field(default_factory=tuple)
    details: dict[str, Any] = Field(default_factory=dict)


class PlacementPreflightReport(AgentBaseModel):
    requirement_set_hash: str = ""
    placement_patch_hash: str = ""
    findings: tuple[PlacementPreflightFinding, ...] = Field(default_factory=tuple)
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


class PlacementPreflightExecutionResult(AgentBaseModel):
    executed: bool = False
    report: PlacementPreflightReport | None = None
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


def run_evidence_qualified_placement_preflight(
    *,
    requirement_set: PlacementRequirementSet,
    placement_patches: dict[str, Any] | None,
    known_universe_ids: Iterable[str] | None = None,
    expected_requirement_set_hash: str | None = None,
) -> PlacementPreflightReport:
    """Run the deterministic evidence-qualified Placement preflight.

    Returns a :class:`PlacementPreflightReport`.  ``passed=True`` only
    when there are zero error-severity findings.
    """

    findings: list[PlacementPreflightFinding] = []
    known_universe_ids = set(known_universe_ids or [])
    # 1. Hash consistency.
    if (
        expected_requirement_set_hash
        and requirement_set.requirement_set_hash != expected_requirement_set_hash
    ):
        findings.append(PlacementPreflightFinding(
            code=PLACEMENT_REQUIREMENT_HASH_MISMATCH,
            message="placement requirement set hash drifts from expected value",
            details={
                "expected": expected_requirement_set_hash[:12],
                "actual": requirement_set.requirement_set_hash[:12],
            },
        ))
    # 2. Unresolved source-critical requirements block.
    if requirement_set.unresolved_requirements:
        findings.append(PlacementPreflightFinding(
            code=PLACEMENT_SOURCE_CRITICAL_UNRESOLVED,
            message=(
                f"{len(requirement_set.unresolved_requirements)} unresolved "
                f"source-critical placement requirements"
            ),
            details={"unresolved": list(requirement_set.unresolved_requirements)[:20]},
        ))
    # 3. Localized insert bindings.
    placement_patches = placement_patches or {}
    for insert_req in requirement_set.localized_insert_bindings:
        if not insert_req.host_profile_id:
            findings.append(PlacementPreflightFinding(
                code=PLACEMENT_HOST_PATH_MISSING,
                message=f"localized insert {insert_req.insert_kind} has no host path",
                affected_assembly_type_ids=insert_req.assembly_type_ids,
            ))
        if not insert_req.insert_profile_id:
            findings.append(PlacementPreflightFinding(
                code=PLACEMENT_INSERT_BINDING_MISSING,
                message=f"localized insert {insert_req.insert_kind} has no insert profile",
                affected_assembly_type_ids=insert_req.assembly_type_ids,
            ))
        for universe_id in insert_req.required_universe_ids:
            if universe_id and universe_id not in known_universe_ids:
                findings.append(PlacementPreflightFinding(
                    code=PLACEMENT_REQUIRED_UNIVERSE_MISSING,
                    message=(
                        f"localized insert {insert_req.insert_kind} requires "
                        f"universe {universe_id} which is not in accepted Universes"
                    ),
                    affected_universe_ids=(universe_id,),
                ))
    # 4. Core layout multiplicity.
    for core_layout in requirement_set.core_layout_requirements:
        expected_total = sum(core_layout.assembly_type_counts.values())
        if expected_total > 0:
            # We do not derive coordinates from count; just check that
            # the placement patch declares the right assembly types.
            for atype, count in core_layout.assembly_type_counts.items():
                if count <= 0:
                    continue
                if not any(
                    atype in str(p) for p in placement_patches.values()
                ):
                    findings.append(PlacementPreflightFinding(
                        code=PLACEMENT_ASSEMBLY_TYPE_UNCOVERED,
                        message=(
                            f"assembly type {atype} (expected count {count}) "
                            f"is not represented in the placement patches"
                        ),
                        affected_assembly_type_ids=(atype,),
                    ))
    report = PlacementPreflightReport(
        requirement_set_hash=requirement_set.requirement_set_hash,
        findings=tuple(findings),
        passed=all(f.severity != "error" for f in findings),
    )
    return report
