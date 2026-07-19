"""Phase 8A Step 6B — research router tests (Section 12, 34).

Verifies the deterministic router routes by category + code, never
lets the LLM pick the action, and fails closed for unknown codes.
"""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.closed_loop.models import (
    PlanFindingCategory,
    PlanGateId,
    PlanReviewAction,
    PlanReviewFinding,
    PlanFindingSeverity,
)
from openmc_agent.plan_investigation.research_router import (
    RETRIEVE_EVIDENCE_CATEGORIES,
    RETRIEVE_EVIDENCE_CODES,
    aggregate_action,
    route_findings_to_research,
)


def _finding(
    *,
    code: str = "x",
    category: str = PlanFindingCategory.SOURCE_COVERAGE.value,
    severity: str = "error",
    affected: list[str] | None = None,
    metadata: dict | None = None,
) -> PlanReviewFinding:
    return PlanReviewFinding(
        gate_id=PlanGateId.MATERIAL_UNIVERSE,
        code=code,
        severity=PlanFindingSeverity(severity),
        category=category,
        message="test finding",
        confidence=1.0,
        affected_patch_types=affected or ["materials"],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# RETRIEVE_EVIDENCE routing (Section 34: 13, 14)
# ---------------------------------------------------------------------------


def test_source_coverage_category_routes_to_retrieve_evidence() -> None:
    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(code="inventory.material_role_uncovered")],
    )
    assert len(decisions) == 1
    assert decisions[0].action is PlanReviewAction.RETRIEVE_EVIDENCE
    assert len(decisions[0].targets) == 1
    assert decisions[0].targets[0].claim_predicates  # non-empty


def test_unsupported_inference_category_routes_to_retrieve_evidence() -> None:
    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="some.inference",
            category=PlanFindingCategory.UNSUPPORTED_INFERENCE.value,
        )],
    )
    assert decisions[0].action is PlanReviewAction.RETRIEVE_EVIDENCE


def test_stable_inventory_codes_route_to_retrieve_evidence() -> None:
    """All stable codes in RETRIEVE_EVIDENCE_CODES route correctly.

    Uses ``cross_patch_mismatch`` category (NOT in
    RETRIEVE_EVIDENCE_CATEGORIES) to verify the code alone triggers
    RETRIEVE_EVIDENCE.
    """

    for code in RETRIEVE_EVIDENCE_CODES:
        decisions = route_findings_to_research(
            gate_id="material_universe",
            findings=[_finding(code=code, category="cross_patch_mismatch")],
        )
        assert decisions[0].action is PlanReviewAction.RETRIEVE_EVIDENCE, (
            f"code {code} should route to RETRIEVE_EVIDENCE"
        )


# ---------------------------------------------------------------------------
# REVISE_CURRENT_PATCH (Section 34: 14)
# ---------------------------------------------------------------------------


def test_schema_or_format_routes_to_revise_current_patch() -> None:
    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="schema.bad_format",
            category=PlanFindingCategory.SCHEMA_OR_FORMAT.value,
        )],
    )
    assert decisions[0].action is PlanReviewAction.REVISE_CURRENT_PATCH


def test_representation_error_routes_to_revise_current_patch() -> None:
    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="repr.bad_id",
            category=PlanFindingCategory.REPRESENTATION_ERROR.value,
        )],
    )
    assert decisions[0].action is PlanReviewAction.REVISE_CURRENT_PATCH


# ---------------------------------------------------------------------------
# ASK_HUMAN (Section 34: 16)
# ---------------------------------------------------------------------------


def test_physical_ambiguity_routes_to_ask_human() -> None:
    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="ambiguity.homogenization",
            category=PlanFindingCategory.PHYSICAL_AMBIGUITY.value,
        )],
    )
    assert decisions[0].action is PlanReviewAction.ASK_HUMAN


# ---------------------------------------------------------------------------
# FAIL_CLOSED (Section 34: 17)
# ---------------------------------------------------------------------------


def test_unknown_code_routes_to_fail_closed() -> None:
    """Unknown codes route to FAIL_CLOSED (no silent accept)."""

    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="totally_unknown",
            category="cross_patch_mismatch",  # not in RETRIEVE_EVIDENCE_CATEGORIES
        )],
    )
    assert decisions[0].action is PlanReviewAction.FAIL_CLOSED


def test_disabled_research_does_not_route_to_retrieve_evidence() -> None:
    """When enable_research=False, no RETRIEVE_EVIDENCE is returned."""

    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(code="inventory.material_role_uncovered")],
        enable_research=False,
    )
    assert all(d.action is not PlanReviewAction.RETRIEVE_EVIDENCE for d in decisions)


# ---------------------------------------------------------------------------
# Aggregate action (priority)
# ---------------------------------------------------------------------------


def test_aggregate_returns_highest_priority_action() -> None:
    """Priority: FAIL_CLOSED > ASK_HUMAN > RETRIEVE_EVIDENCE > others."""

    findings = [
        _finding(code="inventory.material_role_uncovered"),  # RETRIEVE
        _finding(code="ambiguity.x", category=PlanFindingCategory.PHYSICAL_AMBIGUITY.value),  # ASK_HUMAN
    ]
    decisions = route_findings_to_research(
        gate_id="material_universe", findings=findings,
    )
    agg = aggregate_action(decisions)
    assert agg is PlanReviewAction.ASK_HUMAN


def test_aggregate_fail_closed_dominates() -> None:
    findings = [
        _finding(code="inventory.material_role_uncovered"),  # RETRIEVE
        _finding(code="totally.unknown", category="cross_patch_mismatch"),  # FAIL_CLOSED
    ]
    decisions = route_findings_to_research(
        gate_id="material_universe", findings=findings,
    )
    agg = aggregate_action(decisions)
    assert agg is PlanReviewAction.FAIL_CLOSED


def test_aggregate_empty_returns_approve() -> None:
    assert aggregate_action([]).value == "approve"


# ---------------------------------------------------------------------------
# Targets cover findings (Section 34: 18)
# ---------------------------------------------------------------------------


def test_every_retrievable_finding_gets_a_target() -> None:
    """Each RETRIEVE_EVIDENCE decision carries at least one target."""

    findings = [
        _finding(code=f"inventory.{c}")
        for c in [
            "material_role_uncovered",
            "fuel_variant_material_uncovered",
            "radial_profile_uncovered",
        ]
    ]
    decisions = route_findings_to_research(
        gate_id="material_universe", findings=findings,
    )
    for d in decisions:
        assert d.action is PlanReviewAction.RETRIEVE_EVIDENCE
        assert len(d.targets) >= 1
        assert d.targets[0].target_id
        assert d.targets[0].target_hash


# ---------------------------------------------------------------------------
# Target construction quality
# ---------------------------------------------------------------------------


def test_material_role_finding_has_density_predicate() -> None:
    """A material-role finding yields material.density predicate."""

    decisions = route_findings_to_research(
        gate_id="material_universe",
        findings=[_finding(
            code="inventory.material_role_uncovered",
            metadata={"role": "fuel"},
        )],
    )
    target = decisions[0].targets[0]
    assert "material.role_required" in target.claim_predicates
    assert "material.density" in target.claim_predicates
    # Search terms include the role.
    assert any("fuel" in t for t in target.suggested_search_terms)


def test_placement_finding_has_coordinate_predicate() -> None:
    decisions = route_findings_to_research(
        gate_id="placement",
        findings=[_finding(
            code="placement.source_coordinate_missing",
            affected=["pin_map", "core_layout"],
        )],
    )
    target = decisions[0].targets[0]
    assert "placement.coordinate_required" in target.claim_predicates


def test_axial_finding_has_region_predicate() -> None:
    decisions = route_findings_to_research(
        gate_id="axial_geometry",
        findings=[_finding(code="axial.source_region_missing")],
    )
    target = decisions[0].targets[0]
    assert any("axial" in p for p in target.claim_predicates)


# ---------------------------------------------------------------------------
# Reactor-neutrality
# ---------------------------------------------------------------------------


def test_router_has_no_reactor_specific_branches() -> None:
    """No VERA/PWR/BWR/etc strings in the router production source."""

    from pathlib import Path
    import openmc_agent.plan_investigation.research_router as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("vera3", "vera4", "pwr", "bwr", "vver", "htgr", "sfr", "candu"):
        assert forbidden not in src.lower(), (
            f"reactor-specific term {forbidden!r} found in router source"
        )
