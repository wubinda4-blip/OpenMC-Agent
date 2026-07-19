"""Phase 8A Step 6B — deterministic research router (Section 12).

Decides which :class:`PlanReviewAction` applies to each gate finding.
The router is pure Python — the LLM never decides:

* whether to research,
* the gate action,
* the owner patch,
* the invalidation scope.

Routing priority:

A. ``RETRIEVE_EVIDENCE`` — source coverage / unsupported inference
   findings whose target predicate can be constructed from the
   existing SourceIndex.
B. ``REVISE_CURRENT_PATCH`` — schema/format errors, or fields the
   owner patch omitted despite existing evidence.
C. ``RETRY_DEPENDENCY`` — known dependency owner with sufficient evidence.
D. ``ASK_HUMAN`` — engineering ambiguity, conflicting sources, or
   research that proved source absence.
E. ``FAIL_CLOSED`` — budget exhausted, no-progress, unknown owner,
   unverifiable target, or safety-policy violation.

Stable issue codes that ALWAYS route to ``RETRIEVE_EVIDENCE`` when the
target is constructable:

    inventory.source_claim_missing
    inventory.source_span_invalid
    inventory.component_unresolved
    facts.source_value_missing
    placement.source_binding_missing
    placement.source_coordinate_missing
    axial.source_region_missing
    axial.source_extent_missing
    axial.source_profile_missing
    axial.homogenization_method_missing
"""

from __future__ import annotations

from typing import Any, Iterable

from openmc_agent.plan_builder.closed_loop.models import (
    PlanFindingCategory,
    PlanReviewAction,
)

from .research_models import PlanResearchTarget

__all__ = [
    "route_findings_to_research",
    "RETRIEVE_EVIDENCE_CODES",
    "RETRIEVE_EVIDENCE_CATEGORIES",
    "RouteDecision",
]


# Categories that always prefer RETRIEVE_EVIDENCE when constructable.
RETRIEVE_EVIDENCE_CATEGORIES: frozenset[str] = frozenset({
    PlanFindingCategory.SOURCE_COVERAGE.value,
    PlanFindingCategory.UNSUPPORTED_INFERENCE.value,
})

# Stable issue codes that always prefer RETRIEVE_EVIDENCE.
RETRIEVE_EVIDENCE_CODES: frozenset[str] = frozenset({
    "inventory.source_claim_missing",
    "inventory.source_span_invalid",
    "inventory.component_unresolved",
    "facts.source_value_missing",
    "placement.source_binding_missing",
    "placement.source_coordinate_missing",
    "axial.source_region_missing",
    "axial.source_extent_missing",
    "axial.source_profile_missing",
    "axial.homogenization_method_missing",
    "inventory.material_role_uncovered",
    "inventory.fuel_variant_material_uncovered",
    "inventory.radial_profile_uncovered",
    "manifest.inventory_requirement_missing",
})

# Categories that always prefer REVISE_CURRENT_PATCH.
REVISE_CATEGORIES: frozenset[str] = frozenset({
    PlanFindingCategory.SCHEMA_OR_FORMAT.value,
    PlanFindingCategory.REPRESENTATION_ERROR.value,
})

# Categories that require human judgement.
HUMAN_CATEGORIES: frozenset[str] = frozenset({
    PlanFindingCategory.PHYSICAL_AMBIGUITY.value,
})


class RouteDecision:
    """One router decision (action + reason + targets).

    A simple dataclass-like container; we avoid pydantic here to keep
    the router fast and dependency-free.
    """

    __slots__ = ("action", "reason", "targets", "owner_patch_types", "finding_id")

    def __init__(
        self,
        *,
        action: PlanReviewAction,
        reason: str,
        targets: tuple[PlanResearchTarget, ...] = (),
        owner_patch_types: tuple[str, ...] = (),
        finding_id: str = "",
    ) -> None:
        self.action = action
        self.reason = reason
        self.targets = targets
        self.owner_patch_types = owner_patch_types
        self.finding_id = finding_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "target_count": len(self.targets),
            "owner_patch_types": list(self.owner_patch_types),
            "finding_id": self.finding_id,
        }


def route_findings_to_research(
    *,
    gate_id: str,
    findings: Iterable[Any],
    inventory: Any = None,
    ledger: Any = None,
    enable_research: bool = True,
) -> list[RouteDecision]:
    """Route gate findings to deterministic actions.

    Returns one :class:`RouteDecision` per finding.  The caller (the
    closed-loop controller) takes the highest-priority action across
    all decisions:

        FAIL_CLOSED > ASK_HUMAN > RETRIEVE_EVIDENCE >
        RETRY_DEPENDENCY > REVISE_CURRENT_PATCH > APPROVE

    Findings with no actionable code route to ``FAIL_CLOSED`` (fail
    closed for unknown codes) so the gate never silently accepts an
    unknown finding.
    """

    decisions: list[RouteDecision] = []
    for f in findings:
        decisions.append(_route_one_finding(
            gate_id=gate_id, finding=f, inventory=inventory,
            ledger=ledger, enable_research=enable_research,
        ))
    return decisions


def _route_one_finding(
    *,
    gate_id: str,
    finding: Any,
    inventory: Any,
    ledger: Any,
    enable_research: bool,
) -> RouteDecision:
    """Decide the action for one finding."""

    code = str(getattr(finding, "code", "") or (f.get("code") if isinstance(finding, dict) else ""))
    raw_category = (
        getattr(finding, "category", None)
        if not isinstance(finding, dict)
        else finding.get("category")
    )
    # Normalise the category to its string value (``str(enum)`` returns
    # the repr name, not the value).  Plain strings pass through.
    if raw_category is None:
        category = ""
    elif hasattr(raw_category, "value"):
        category = str(raw_category.value)
    else:
        category = str(raw_category)
    finding_id = str(
        getattr(finding, "finding_id", "")
        or (finding.get("finding_id") if isinstance(finding, dict) else "")
    )
    affected = (
        list(getattr(finding, "affected_patch_types", []) or [])
        if not isinstance(finding, dict)
        else list(finding.get("affected_patch_types", []) or [])
    )
    # A. RETRIEVE_EVIDENCE for source-coverage / known codes.
    if enable_research:
        if (
            category in RETRIEVE_EVIDENCE_CATEGORIES
            or code in RETRIEVE_EVIDENCE_CODES
        ):
            target = _build_research_target(
                gate_id=gate_id, finding=finding, code=code,
                inventory=inventory,
            )
            if target is not None:
                return RouteDecision(
                    action=PlanReviewAction.RETRIEVE_EVIDENCE,
                    reason=f"source-coverage finding {code}; target={target.target_id}",
                    targets=(target,),
                    owner_patch_types=tuple(affected),
                    finding_id=finding_id,
                )
            # Target not constructable → fall through to other routes.
    # B. REVISE_CURRENT_PATCH for schema / representation errors.
    if category in REVISE_CATEGORIES:
        return RouteDecision(
            action=PlanReviewAction.REVISE_CURRENT_PATCH,
            reason=f"schema/representation finding {code}",
            owner_patch_types=tuple(affected),
            finding_id=finding_id,
        )
    # D. ASK_HUMAN for physical ambiguity.
    if category in HUMAN_CATEGORIES:
        return RouteDecision(
            action=PlanReviewAction.ASK_HUMAN,
            reason=f"physical ambiguity finding {code}",
            owner_patch_types=tuple(affected),
            finding_id=finding_id,
        )
    # C. RETRY_DEPENDENCY when affected = a different patch than owner.
    # (We heuristically treat single-element affected lists as owner;
    # multi-element lists imply cross-patch mismatch → RETRY_DEPENDENCY
    # is not selected here because the closed-loop controller already
    # handles cross-patch retry via the typed dependency graph.)
    # E. FAIL_CLOSED for unknown / unverifiable codes (default).
    return RouteDecision(
        action=PlanReviewAction.FAIL_CLOSED,
        reason=f"unknown or unverifiable finding code={code} category={category}",
        owner_patch_types=tuple(affected),
        finding_id=finding_id,
    )


def _build_research_target(
    *,
    gate_id: str,
    finding: Any,
    code: str,
    inventory: Any,
) -> PlanResearchTarget | None:
    """Construct a :class:`PlanResearchTarget` from a finding.

    Returns ``None`` when no target can be constructed (the router
    then falls through to other actions).
    """

    # Build suggested search terms from the finding message + code.
    message = str(
        getattr(finding, "message", "")
        or (finding.get("message") if isinstance(finding, dict) else "")
    )
    metadata = (
        getattr(finding, "metadata", {})
        if not isinstance(finding, dict)
        else finding.get("metadata", {})
    ) or {}
    # Stable code → predicate mapping.
    claim_predicates: tuple[str, ...]
    suggested_search_terms: tuple[str, ...]
    if code in {
        "inventory.material_role_uncovered",
        "inventory.fuel_variant_material_uncovered",
        "facts.source_value_missing",
    }:
        role = metadata.get("role") or "fuel"
        claim_predicates = ("material.role_required", "material.density")
        suggested_search_terms = (
            f"{role} material",
            f"{role} density",
            f"{role} composition",
        )
    elif code in {
        "inventory.radial_profile_uncovered",
        "manifest.inventory_requirement_missing",
        "inventory.component_unresolved",
    }:
        claim_predicates = ("geometry.profile_required",)
        suggested_search_terms = (
            "fuel pin geometry",
            "guide tube geometry",
            "radial profile",
        )
    elif code in {
        "placement.source_binding_missing",
        "placement.source_coordinate_missing",
    }:
        claim_predicates = ("placement.coordinate_required",)
        suggested_search_terms = ("core layout", "assembly location", "lattice position")
    elif code in {
        "axial.source_region_missing",
        "axial.source_extent_missing",
        "axial.source_profile_missing",
        "axial.homogenization_method_missing",
    }:
        claim_predicates = ("axial.region_required", "axial.extent_required")
        suggested_search_terms = ("axial region", "axial extent", "plenum", "gas gap")
    else:
        # Generic: use the message itself.
        claim_predicates = ("source.value_required",)
        suggested_search_terms = (message[:60],) if message else ()
    target = PlanResearchTarget(
        claim_predicates=claim_predicates,
        target_json_paths=tuple(metadata.get("target_json_paths", []) or ()),
        target_component_ids=tuple(metadata.get("target_component_ids", []) or ()),
        target_profile_ids=tuple(metadata.get("target_profile_ids", []) or ()),
        target_requirement_ids=tuple(metadata.get("target_requirement_ids", []) or ()),
        expected_value_kind=metadata.get("expected_value_kind", ""),
        preferred_source_sections=tuple(metadata.get("preferred_source_sections", []) or ()),
        suggested_search_terms=suggested_search_terms,
        blocking_patch_types=tuple(metadata.get("blocking_patch_types", []) or ()),
        requires_human_if_absent=bool(metadata.get("requires_human_if_absent", False)),
    )
    return target


def aggregate_action(decisions: list[RouteDecision]) -> PlanReviewAction:
    """Pick the highest-priority action across a list of decisions.

    Priority (highest first):

        FAIL_CLOSED > ASK_HUMAN > RETRIEVE_EVIDENCE >
        RETRY_DEPENDENCY > REVISE_CURRENT_PATCH > APPROVE
    """

    if not decisions:
        return PlanReviewAction.APPROVE
    priority = {
        PlanReviewAction.FAIL_CLOSED: 6,
        PlanReviewAction.ASK_HUMAN: 5,
        PlanReviewAction.RETRIEVE_EVIDENCE: 4,
        PlanReviewAction.RETRY_DEPENDENCY: 3,
        PlanReviewAction.REVISE_CURRENT_PATCH: 2,
        PlanReviewAction.APPROVE: 1,
    }
    best = decisions[0]
    for d in decisions[1:]:
        if priority[d.action] > priority[best.action]:
            best = d
    return best.action
