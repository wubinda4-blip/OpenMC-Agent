"""Typed component evidence ontology + proposal models (Phase 8A Step 5).

Defines the reactor-neutral vocabulary that the evidence synthesis LLM
is allowed to use when proposing component-level evidence.  The LLM
never invents source ids, line numbers, or numerical values: it picks
from this ontology and references already-validated SourceSpans.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .hashing import content_hash, short_id

__all__ = [
    "ComponentKind",
    "ProfileKind",
    "ComponentApplicability",
    "COMPONENT_KINDS",
    "PROFILE_KINDS",
    "APPLICABILITY_SCOPES",
    "EVIDENCE_PREDICATES",
    "ComponentEvidenceProposal",
    "UnresolvedQuestion",
    "ComponentEvidenceSynthesisResult",
    "UnitConversion",
    "SUPPORTED_UNITS",
    "normalize_unit",
]


# ---------------------------------------------------------------------------
# Ontology enums
# ---------------------------------------------------------------------------


class ComponentKind(str, Enum):
    """Reactor-neutral component ontology.

    No benchmark names; covers PWR / BWR / VVER / HTGR / SFR / CANDU /
    MOX problem statements without privileging any one design.
    """

    FUEL = "fuel"
    FUEL_PIN = "fuel_pin"
    GUIDE_TUBE = "guide_tube"
    INSTRUMENT_TUBE = "instrument_tube"
    CONTROL_ROD = "control_rod"
    ABSORBER_INSERT = "absorber_insert"
    POISON_INSERT = "poison_insert"
    PYREX_ROD = "pyrex_rod"
    THIMBLE_PLUG = "thimble_plug"
    END_PLUG = "end_plug"
    PLENUM = "plenum"
    GAS_GAP = "gas_gap"
    WATER_PIN = "water_pin"
    MODERATOR_REGION = "moderator_region"
    SPACER_GRID = "spacer_grid"
    SUPPORT_PLATE = "support_plate"
    NOZZLE = "nozzle"
    CORE_PLATE = "core_plate"
    DASHPOT = "dashpot"
    REFLECTOR = "reflector"
    VESSEL_OR_BOUNDARY = "vessel_or_boundary"
    CUSTOM = "custom"


class ProfileKind(str, Enum):
    """Radial-profile taxonomy for universes."""

    ACTIVE_FUEL_PIN = "active_fuel_pin"
    FUEL_ROD_END_PLUG = "fuel_rod_end_plug"
    FUEL_ROD_PLENUM = "fuel_rod_plenum"
    GUIDE_TUBE = "guide_tube"
    INSTRUMENT_TUBE = "instrument_tube"
    CONTROL_ROD = "control_rod"
    POISON_ROD = "poison_rod"
    PLUG_IN_GUIDE_TUBE = "plug_in_guide_tube"
    MODERATOR_ONLY = "moderator_only"
    STRUCTURAL_COOLANT_HOMOGENIZED = "structural_coolant_homogenized"
    SOLID_STRUCTURAL = "solid_structural"
    CUSTOM = "custom"


class ComponentApplicability(str, Enum):
    """Where a component / profile applies."""

    GLOBAL = "global"
    ASSEMBLY_TYPE = "assembly_type"
    ASSEMBLY_INSTANCE = "assembly_instance"
    PIN_ROLE = "pin_role"
    AXIAL_REGION = "axial_region"
    LOCALIZED_INSERT = "localized_insert"
    UNRESOLVED = "unresolved"


COMPONENT_KINDS: frozenset[str] = frozenset(k.value for k in ComponentKind)
PROFILE_KINDS: frozenset[str] = frozenset(k.value for k in ProfileKind)
APPLICABILITY_SCOPES: frozenset[str] = frozenset(a.value for a in ComponentApplicability)


# ---------------------------------------------------------------------------
# Stable evidence predicates
# ---------------------------------------------------------------------------


EVIDENCE_PREDICATES: frozenset[str] = frozenset(
    {
        # Geometry predicates.
        "geometry.component_present",
        "geometry.profile_required",
        "geometry.profile_layer_order",
        "geometry.profile_radius_boundary",
        "geometry.axial_region_present",
        "geometry.axial_region_extent",
        "geometry.axial_region_replacement_profile",
        "geometry.through_path_required",
        "geometry.homogenized_component_required",
        # Material predicates.
        "material.role_required",
        "material.identity_present",
        "material.density_present",
        "material.temperature_present",
        "material.composition_present",
        "material.composition_incomplete",
        # Placement predicates.
        "placement.host_component",
        "placement.applicable_assembly_type",
    }
)


# ---------------------------------------------------------------------------
# Unit conversion (deterministic, no chemistry)
# ---------------------------------------------------------------------------


SUPPORTED_UNITS: frozenset[str] = frozenset(
    {"cm", "mm", "m", "g/cm3", "kg/m3", "K", "ppm", "wt%", "at%"}
)


_LENGTH_TO_CM = {"cm": 1.0, "mm": 0.1, "m": 100.0}
_DENSITY_TO_GCM3 = {"g/cm3": 1.0, "kg/m3": 0.001}


class UnitConversion(AgentBaseModel):
    """Records a deterministic unit conversion for audit.

    Stores the raw source value, source unit, normalized value, the
    conversion operation, and the input claim id.  No chemistry; only
    length / density / temperature scaling.
    """

    source_value: float
    source_unit: str
    normalized_value: float
    normalized_unit: str
    conversion_operation: str
    source_claim_id: str = ""

    @field_validator("source_unit", "normalized_unit")
    @classmethod
    def _unit_supported(cls, value: str) -> str:
        if value not in SUPPORTED_UNITS:
            raise PlanInvestigationIssue(
                "plan_investigation.unit_not_supported",
                f"unit '{value}' is not in the supported set",
                details={"supported": sorted(SUPPORTED_UNITS)},
            )
        return value


def normalize_unit(value: float, unit: str) -> tuple[float, str, str]:
    """Return (normalized_value, normalized_unit, conversion_operation).

    Lengths normalize to cm.  Densities normalize to g/cm3.  Temperatures
    stay in K.  Fractions (ppm / wt% / at%) are unchanged.

    Returns ``(value, unit, "identity")`` for unsupported / already
    canonical units.
    """

    if unit in _LENGTH_TO_CM and unit != "cm":
        factor = _LENGTH_TO_CM[unit]
        return value * factor, "cm", f"multiply_by_{factor}"
    if unit in _DENSITY_TO_GCM3 and unit != "g/cm3":
        factor = _DENSITY_TO_GCM3[unit]
        return value * factor, "g/cm3", f"multiply_by_{factor}"
    return value, unit, "identity"


# ---------------------------------------------------------------------------
# Proposal models
# ---------------------------------------------------------------------------


class ComponentEvidenceProposal(AgentBaseModel):
    """One typed proposal from the synthesis LLM.

    The proposal references already-validated ``source_span_ids`` and
    declares its component/profile kind from the reactor-neutral
    ontology.  Numerical values must be either:
    * Token-for-token recoverable from a referenced span's excerpt, OR
    * A deterministic derivation from another accepted claim.

    Anything that fails verification lands in ``unresolved_fields``
    rather than becoming an accepted EvidenceClaim.
    """

    proposal_id: str
    component_kind: str
    profile_kind: str | None = None
    subject: str
    predicate: str
    value: Any = None
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    applicability: str = ComponentApplicability.GLOBAL.value
    material_roles: tuple[str, ...] = Field(default_factory=tuple)
    cell_roles: tuple[str, ...] = Field(default_factory=tuple)
    axial_region_kind: str | None = None
    host_component_kind: str | None = None
    source_label: str = ""
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""

    @field_validator("component_kind")
    @classmethod
    def _component_in_ontology(cls, value: str) -> str:
        if value not in COMPONENT_KINDS:
            raise PlanInvestigationIssue(
                "plan_investigation.component_kind_not_in_ontology",
                f"component_kind '{value}' is not in the reactor-neutral ontology",
                details={"allowed": sorted(COMPONENT_KINDS)},
            )
        return value

    @field_validator("profile_kind")
    @classmethod
    def _profile_in_ontology(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in PROFILE_KINDS:
            raise PlanInvestigationIssue(
                "plan_investigation.profile_kind_not_in_ontology",
                f"profile_kind '{value}' is not in the reactor-neutral ontology",
                details={"allowed": sorted(PROFILE_KINDS)},
            )
        return value

    @field_validator("predicate")
    @classmethod
    def _predicate_in_ontology(cls, value: str) -> str:
        if value not in EVIDENCE_PREDICATES:
            raise PlanInvestigationIssue(
                "plan_investigation.predicate_not_in_ontology",
                f"predicate '{value}' is not in the evidence predicate set",
                details={"allowed": sorted(EVIDENCE_PREDICATES)},
            )
        return value

    @field_validator("applicability")
    @classmethod
    def _applicability_in_ontology(cls, value: str) -> str:
        if value not in APPLICABILITY_SCOPES:
            raise PlanInvestigationIssue(
                "plan_investigation.applicability_not_in_ontology",
                f"applicability '{value}' is not in the scope set",
                details={"allowed": sorted(APPLICABILITY_SCOPES)},
            )
        return value

    @model_validator(mode="after")
    def _compute_proposal_id(self) -> "ComponentEvidenceProposal":
        expected = short_id(
            "prop",
            {
                "c": self.component_kind,
                "p": self.profile_kind,
                "s": self.subject,
                "pr": self.predicate,
                "v": self.value,
                "sp": list(self.source_span_ids),
            },
        )
        if not self.proposal_id:
            object.__setattr__(self, "proposal_id", expected)
        elif self.proposal_id != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.proposal_id_mismatch",
                "proposal_id does not match the deterministic value",
                details={"expected": expected, "actual": self.proposal_id},
            )
        return self

    def semantic_key(self) -> str:
        """Stable key for conflict detection."""

        return content_hash(
            {
                "c": self.component_kind,
                "p": self.profile_kind,
                "s": self.subject,
                "pr": self.predicate,
                "ap": self.applicability,
                "mr": sorted(self.material_roles),
                "cr": sorted(self.cell_roles),
            }
        )


class UnresolvedQuestion(AgentBaseModel):
    """A typed unresolved question the synthesis could not answer."""

    question_id: str
    subject: str
    predicate: str
    blocking_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    suggested_research_terms: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""


class ComponentEvidenceSynthesisResult(AgentBaseModel):
    """Wrapper around the synthesis LLM's parsed output.

    The result carries the raw proposals + unresolved questions plus
    a deterministic ``synthesis_hash`` that the caller can persist for
    audit.
    """

    patch_type: str
    proposals: tuple[ComponentEvidenceProposal, ...] = Field(default_factory=tuple)
    unresolved_questions: tuple[UnresolvedQuestion, ...] = Field(default_factory=tuple)
    summary: str = ""
    synthesis_hash: str = ""

    @model_validator(mode="after")
    def _compute_synthesis_hash(self) -> "ComponentEvidenceSynthesisResult":
        expected = content_hash(
            {
                "p": self.patch_type,
                "pr": [p.model_dump(mode="json") for p in self.proposals],
                "u": [q.model_dump(mode="json") for q in self.unresolved_questions],
            }
        )
        if not self.synthesis_hash:
            object.__setattr__(self, "synthesis_hash", expected)
        elif self.synthesis_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.synthesis_hash_mismatch",
                "synthesis_hash does not match the recomputed value",
                details={"expected": expected, "actual": self.synthesis_hash},
            )
        return self
