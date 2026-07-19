"""Tests for ComponentEvidenceProposal + synthesis models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.component_evidence import (
    APPLICABILITY_SCOPES,
    COMPONENT_KINDS,
    EVIDENCE_PREDICATES,
    PROFILE_KINDS,
    SUPPORTED_UNITS,
    ComponentApplicability,
    ComponentEvidenceProposal,
    ComponentEvidenceSynthesisResult,
    ComponentKind,
    ProfileKind,
    UnitConversion,
    normalize_unit,
)
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue


_MODEL_FAILURE = (ValidationError, PlanInvestigationIssue)


# ---------------------------------------------------------------------------
# Ontology coverage
# ---------------------------------------------------------------------------


def test_component_ontology_is_reactor_neutral() -> None:
    """No reactor-specific names; covers generic component kinds."""
    forbidden = ("vera", "pwr", "bwr", "vver", "htgr", "sfr", "candu", "mox")
    for kind in COMPONENT_KINDS:
        for term in forbidden:
            assert term not in kind.lower()
    # Sanity: the ontology includes the documented kinds.
    assert "fuel_pin" in COMPONENT_KINDS
    assert "guide_tube" in COMPONENT_KINDS
    assert "control_rod" in COMPONENT_KINDS
    assert "pyrex_rod" in COMPONENT_KINDS
    assert "end_plug" in COMPONENT_KINDS
    assert "gas_gap" in COMPONENT_KINDS
    assert "water_pin" in COMPONENT_KINDS
    assert "spacer_grid" in COMPONENT_KINDS
    assert "support_plate" in COMPONENT_KINDS
    assert "nozzle" in COMPONENT_KINDS
    assert "dashpot" in COMPONENT_KINDS


def test_profile_ontology_covers_documented_profiles() -> None:
    for kind in (
        "active_fuel_pin",
        "fuel_rod_end_plug",
        "fuel_rod_plenum",
        "guide_tube",
        "instrument_tube",
        "control_rod",
        "poison_rod",
        "plug_in_guide_tube",
        "moderator_only",
        "structural_coolant_homogenized",
        "solid_structural",
    ):
        assert kind in PROFILE_KINDS


def test_evidence_predicates_are_stable() -> None:
    """The documented predicate set must be present."""
    for predicate in (
        "geometry.component_present",
        "geometry.profile_required",
        "geometry.profile_layer_order",
        "geometry.profile_radius_boundary",
        "geometry.axial_region_present",
        "geometry.axial_region_extent",
        "geometry.axial_region_replacement_profile",
        "geometry.through_path_required",
        "geometry.homogenized_component_required",
        "material.role_required",
        "material.identity_present",
        "material.density_present",
        "material.temperature_present",
        "material.composition_present",
        "material.composition_incomplete",
        "placement.host_component",
        "placement.applicable_assembly_type",
    ):
        assert predicate in EVIDENCE_PREDICATES


# ---------------------------------------------------------------------------
# Proposal construction
# ---------------------------------------------------------------------------


def test_valid_proposal_construction() -> None:
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        profile_kind="active_fuel_pin",
        subject="fuel_pin",
        predicate="geometry.profile_required",
        value={"layers": ["fuel", "gap", "clad", "coolant"]},
        source_span_ids=("span_abc",),
        material_roles=("fuel", "cladding"),
        cell_roles=("fuel", "gap"),
    )
    assert proposal.proposal_id.startswith("prop_")


def test_invalid_component_kind_rejected() -> None:
    with pytest.raises(_MODEL_FAILURE):
        ComponentEvidenceProposal(
            proposal_id="",
            component_kind="pwr_fuel_pin",  # not in ontology
            subject="x",
            predicate="geometry.component_present",
        )


def test_invalid_profile_kind_rejected() -> None:
    with pytest.raises(_MODEL_FAILURE):
        ComponentEvidenceProposal(
            proposal_id="",
            component_kind="fuel_pin",
            profile_kind="pwr_active_pin",  # not in ontology
            subject="x",
            predicate="geometry.profile_required",
        )


def test_invalid_predicate_rejected() -> None:
    with pytest.raises(_MODEL_FAILURE):
        ComponentEvidenceProposal(
            proposal_id="",
            component_kind="fuel_pin",
            subject="x",
            predicate="custom.unsupported_predicate",
        )


def test_invalid_applicability_rejected() -> None:
    with pytest.raises(_MODEL_FAILURE):
        ComponentEvidenceProposal(
            proposal_id="",
            component_kind="fuel_pin",
            subject="x",
            predicate="geometry.component_present",
            applicability="global_and_local",  # not in ontology
        )


def test_proposal_id_deterministic() -> None:
    a = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.component_present",
    )
    b = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.component_present",
    )
    assert a.proposal_id == b.proposal_id


def test_proposal_id_changes_with_value() -> None:
    a = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.profile_radius_boundary",
        value=0.5,
    )
    b = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.profile_radius_boundary",
        value=0.6,
    )
    assert a.proposal_id != b.proposal_id


def test_semantic_key_collision_possible() -> None:
    """Two proposals with the same semantic fields but different value
    share a semantic key (so conflict detection works)."""
    a = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.profile_radius_boundary",
        value=0.5,
    )
    b = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.profile_radius_boundary",
        value=0.6,
    )
    assert a.semantic_key() == b.semantic_key()


# ---------------------------------------------------------------------------
# Synthesis result
# ---------------------------------------------------------------------------


def test_synthesis_result_hash_deterministic() -> None:
    proposal = ComponentEvidenceProposal(
        proposal_id="",
        component_kind="fuel_pin",
        subject="x",
        predicate="geometry.component_present",
    )
    a = ComponentEvidenceSynthesisResult(
        patch_type="facts",
        proposals=(proposal,),
    )
    b = ComponentEvidenceSynthesisResult(
        patch_type="facts",
        proposals=(proposal,),
    )
    assert a.synthesis_hash == b.synthesis_hash


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


def test_normalize_unit_length_mm_to_cm() -> None:
    value, unit, op = normalize_unit(5.0, "mm")
    assert value == 0.5
    assert unit == "cm"
    assert op == "multiply_by_0.1"


def test_normalize_unit_length_m_to_cm() -> None:
    value, unit, _ = normalize_unit(2.0, "m")
    assert value == 200.0
    assert unit == "cm"


def test_normalize_unit_density_kgm3_to_gcm3() -> None:
    value, unit, _ = normalize_unit(1000.0, "kg/m3")
    assert value == 1.0
    assert unit == "g/cm3"


def test_normalize_unit_canonical_unchanged() -> None:
    value, unit, op = normalize_unit(10.0, "cm")
    assert value == 10.0
    assert unit == "cm"
    assert op == "identity"


def test_normalize_unit_unsupported_unchanged() -> None:
    value, unit, op = normalize_unit(3.5, "wt%")
    assert value == 3.5
    assert unit == "wt%"
    assert op == "identity"


def test_unit_conversion_record() -> None:
    conv = UnitConversion(
        source_value=5.0,
        source_unit="mm",
        normalized_value=0.5,
        normalized_unit="cm",
        conversion_operation="multiply_by_0.1",
        source_claim_id="claim_x",
    )
    assert conv.normalized_unit in SUPPORTED_UNITS


def test_unit_conversion_rejects_unsupported_unit() -> None:
    with pytest.raises(_MODEL_FAILURE):
        UnitConversion(
            source_value=1.0,
            source_unit="inches",  # not supported
            normalized_value=2.54,
            normalized_unit="cm",
            conversion_operation="multiply_by_2.54",
        )
