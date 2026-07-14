"""Material semantics classification (read-only, never modifies input).

Identifies the *scientific meaning* of a material composition vector --
e.g. whether the numbers represent atom fractions, weight fractions,
stoichiometric ratios, ppm concentrations, or atom densities.

This module does **not** modify compositions.  It only classifies and
returns the detected basis, category, and any ambiguity signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.schemas import (
    CompositionValueBasis,
    ComplexMaterialSpec,
    MaterialSpec,
    NormalizationStatus,
    NuclideSpec,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_URANIUM_NUCLIDES = {"U233", "U234", "U235", "U236", "U238"}
_WATER_NUCLIDES = {"H1", "H2", "O16", "O17"}

MATERIAL_ROLE_FUEL = "fuel"
MATERIAL_ROLE_COOLANT = "coolant"
MATERIAL_ROLE_MODERATOR = "moderator"
MATERIAL_ROLE_POISON = "poison"
MATERIAL_ROLE_STRUCTURAL = "structural"
MATERIAL_ROLE_GAP_GAS = "gap_gas"
MATERIAL_ROLE_UNKNOWN = "unknown"

_NA_BARN_CM = 0.6022  # Avogadro number in atoms/(barn·cm) per (g/cm³)/(g/mol)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class MaterialClassification:
    """Result of classifying a material's semantic meaning."""

    material_id: str = ""
    material_name: str = ""
    declared_basis: CompositionValueBasis | None = None
    detected_basis: CompositionValueBasis = CompositionValueBasis.UNKNOWN
    material_role: str = MATERIAL_ROLE_UNKNOWN
    chemical_formula: str | None = None
    has_enrichment_vector: bool = False
    has_stoichiometric_pattern: bool = False
    has_ppm_pattern: bool = False
    is_water_like: bool = False
    is_uo2_like: bool = False
    ambiguity_reasons: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    composition_summary: dict[str, float] = field(default_factory=dict)


@dataclass
class MaterialSemanticIssue:
    """A semantic issue found during classification."""

    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    material_id: str = ""
    material_name: str = ""


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def classify_material_semantics(
    material: MaterialSpec | ComplexMaterialSpec | dict[str, Any],
) -> MaterialClassification:
    """Classify the semantic meaning of a material composition.

    This function is **read-only**: it never modifies the input material.

    Returns a :class:`MaterialClassification` with the detected basis,
    material role, and any ambiguity signals.
    """
    if isinstance(material, dict):
        if "composition" in material and "density_unit" in material:
            if material.get("macroscopic") or material.get("mixture_component_ids"):
                material = ComplexMaterialSpec.model_validate(material)
            else:
                material = MaterialSpec.model_validate(material)
        else:
            material = ComplexMaterialSpec.model_validate(material)

    mat_id = getattr(material, "id", material.name if hasattr(material, "name") else "")
    name = getattr(material, "name", "")
    composition = getattr(material, "composition", [])
    declared = getattr(material, "composition_basis", None)
    formula = getattr(material, "chemical_formula", None)
    enrichment_pct = getattr(material, "enrichment_percent", None)
    enrichment_target = getattr(material, "enrichment_target", None)

    result = MaterialClassification(
        material_id=mat_id,
        material_name=name,
        declared_basis=declared,
        chemical_formula=formula,
    )

    if not composition:
        result.detected_basis = CompositionValueBasis.UNKNOWN
        return result

    # Build composition summary.
    nuclide_map: dict[str, float] = {}
    for c in composition:
        nuclide_map[c.name] = c.percent
    result.composition_summary = dict(nuclide_map)

    # Detect enrichment vector.
    has_u = any(n in _URANIUM_NUCLIDES for n in nuclide_map)
    u_names = [n for n in nuclide_map if n in _URANIUM_NUCLIDES]
    u_sum = sum(nuclide_map[n] for n in u_names) if u_names else 0.0
    o16 = nuclide_map.get("O16", 0.0)
    result.has_enrichment_vector = bool(
        enrichment_pct is not None and enrichment_target is not None
    )

    # Detect water-like composition.
    h1 = nuclide_map.get("H1", 0.0)
    total_sum = sum(nuclide_map.values())
    if total_sum > 0:
        h1_frac = h1 / total_sum
        o16_frac = o16 / total_sum
        result.is_water_like = (
            0.55 < h1_frac < 0.72 and 0.20 < o16_frac < 0.40
        )

    # Detect UO2-like composition.
    result.is_uo2_like = has_u and o16 > 0

    # Stoichiometric pattern: U isotopes sum ~100, O16 ~2.
    if has_u and o16 > 0:
        if u_sum > 50 and o16 < 10:
            result.has_stoichiometric_pattern = True

    # PPM pattern: B10 fraction > 5e-4 in water.
    b10 = nuclide_map.get("B10", 0.0)
    if result.is_water_like and b10 > 0:
        b10_frac = b10 / total_sum if total_sum > 0 else 0.0
        if b10_frac > 5e-4:
            result.has_ppm_pattern = True

    # Detect material role.
    result.material_role = _detect_material_role(
        nuclide_map, result.is_water_like, result.is_uo2_like,
        formula, name,
    )

    # Determine detected basis.
    result.detected_basis = _detect_basis(
        declared, material, nuclide_map, total_sum,
        result,
    )

    # Detect ambiguity.
    _detect_ambiguity(result)

    return result


def validate_material_semantics(
    material: MaterialSpec | ComplexMaterialSpec | dict[str, Any],
) -> list[MaterialSemanticIssue]:
    """Check material semantics for issues without modifying anything.

    Returns a list of :class:`MaterialSemanticIssue` objects.
    """
    classification = classify_material_semantics(material)
    issues: list[MaterialSemanticIssue] = []

    mat_id = classification.material_id
    mat_name = classification.material_name

    # Check for ambiguous basis.
    if classification.detected_basis == CompositionValueBasis.UNKNOWN:
        if not classification.declared_basis:
            issues.append(MaterialSemanticIssue(
                code="material.basis_not_declared",
                severity="warning",
                message=(
                    f"Material '{mat_name}' has no declared composition_basis; "
                    "the executor cannot safely determine the meaning of the values."
                ),
                material_id=mat_id,
                material_name=mat_name,
            ))

    # Check for stoichiometric ambiguity.
    if classification.has_stoichiometric_pattern:
        if classification.declared_basis not in (
            CompositionValueBasis.STOICHIOMETRIC_RATIO,
            CompositionValueBasis.ATOM_FRACTION,
        ):
            issues.append(MaterialSemanticIssue(
                code="material.uo2_stoichiometric_ambiguous",
                severity="error" if not classification.declared_basis else "warning",
                message=(
                    f"Material '{mat_name}' shows a stoichiometric pattern "
                    "(U sum ≈ 100, O16 ≈ 2) but basis is "
                    f"{classification.declared_basis or 'undeclared'}. "
                    "If O/U=2 is intended, declare composition_basis='stoichiometric_ratio'. "
                    "If these are real atom fractions, declare composition_basis='atom_fraction'."
                ),
                material_id=mat_id,
                material_name=mat_name,
            ))

    # Check for boron ppm ambiguity.
    if classification.has_ppm_pattern:
        if classification.declared_basis not in (
            CompositionValueBasis.PPM_BY_WEIGHT,
            CompositionValueBasis.PPM_BY_ATOM,
            CompositionValueBasis.ATOM_FRACTION,
        ):
            issues.append(MaterialSemanticIssue(
                code="material.boron_ppm_ambiguous",
                severity="error" if not classification.declared_basis else "warning",
                message=(
                    f"Material '{mat_name}' shows a boron ppm pattern "
                    f"(B10 atom fraction > 5e-4 in water) but basis is "
                    f"{classification.declared_basis or 'undeclared'}. "
                    "If ppm is intended, declare composition_basis='ppm_by_weight'. "
                    "If this is a real atom fraction, declare composition_basis='atom_fraction'."
                ),
                material_id=mat_id,
                material_name=mat_name,
            ))

    # Check for enrichment/atom-fraction mixing.
    if classification.has_enrichment_vector and classification.is_uo2_like:
        if classification.declared_basis == CompositionValueBasis.ATOM_FRACTION:
            issues.append(MaterialSemanticIssue(
                code="material.enrichment_atom_fraction_conflict",
                severity="warning",
                message=(
                    f"Material '{mat_name}' declares atom_fraction but also has "
                    "an enrichment vector. Enrichment is typically used with "
                    "elemental or stoichiometric representations."
                ),
                material_id=mat_id,
                material_name=mat_name,
            ))

    return issues


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _detect_material_role(
    nuclide_map: dict[str, float],
    is_water_like: bool,
    is_uo2_like: bool,
    formula: str | None,
    name: str,
) -> str:
    name_lower = (name or "").lower()
    formula_lower = (formula or "").lower()

    if "uo2" in formula_lower or (is_uo2_like and "fuel" in name_lower):
        return MATERIAL_ROLE_FUEL
    if "helium" in name_lower or "he" == formula_lower:
        return MATERIAL_ROLE_GAP_GAS
    if is_water_like:
        if "bor" in name_lower or "B10" in nuclide_map:
            return MATERIAL_ROLE_POISON
        return MATERIAL_ROLE_COOLANT
    if "zirc" in name_lower or "zr" in formula_lower:
        return MATERIAL_ROLE_STRUCTURAL
    if "ss" in name_lower or "steel" in name_lower:
        return MATERIAL_ROLE_STRUCTURAL
    if is_uo2_like:
        return MATERIAL_ROLE_FUEL
    return MATERIAL_ROLE_UNKNOWN


def _detect_basis(
    declared: CompositionValueBasis | None,
    material: MaterialSpec | ComplexMaterialSpec,
    nuclide_map: dict[str, float],
    total_sum: float,
    classification: MaterialClassification,
) -> CompositionValueBasis:
    """Determine the detected (effective) basis."""

    # If explicitly declared, use it.
    if declared is not None and declared != CompositionValueBasis.UNKNOWN:
        return declared

    # Infer from density_unit.
    density_unit = getattr(material, "density_unit", None)
    if density_unit == "atom/b-cm":
        return CompositionValueBasis.ATOM_DENSITY_BARN_CM
    if density_unit == "sum":
        return CompositionValueBasis.ATOM_DENSITY_BARN_CM

    # Check percent_type.
    percent_types = set()
    for c in getattr(material, "composition", []):
        percent_types.add(c.percent_type)
    if percent_types == {"wo"}:
        return CompositionValueBasis.WEIGHT_FRACTION

    # Check for chemical formula with enrichment.
    formula = getattr(material, "chemical_formula", None)
    enrichment_pct = getattr(material, "enrichment_percent", None)
    if formula and enrichment_pct is not None:
        return CompositionValueBasis.ELEMENTAL_ENRICHMENT

    # Check for stoichiometric pattern (U sum ~100, O ~2).
    if classification.has_stoichiometric_pattern:
        return CompositionValueBasis.UNKNOWN  # ambiguous without explicit declaration

    # Check for ppm pattern.
    if classification.has_ppm_pattern:
        return CompositionValueBasis.UNKNOWN  # ambiguous without explicit declaration

    # Check if values look like proper atom fractions.
    if percent_types == {"ao"}:
        if total_sum > 0.5:
            return CompositionValueBasis.ATOM_FRACTION
        if total_sum > 0 and total_sum < 0.1:
            return CompositionValueBasis.ATOM_FRACTION

    return CompositionValueBasis.UNKNOWN


def _detect_ambiguity(classification: MaterialClassification) -> None:
    """Populate ambiguity reasons if the basis cannot be determined."""
    if classification.detected_basis != CompositionValueBasis.UNKNOWN:
        return

    if classification.has_stoichiometric_pattern:
        classification.ambiguity_reasons.append(
            "stoichiometric_pattern_without_basis: "
            "U isotopes sum ≈ 100 with O16 ≈ 2 could be "
            "stoichiometric_ratio (O/U=2) or raw atom_fraction"
        )
    if classification.has_ppm_pattern:
        classification.ambiguity_reasons.append(
            "boron_ppm_pattern_without_basis: "
            "B10 fraction > 5e-4 in water could be "
            "ppm_by_weight or raw atom_fraction"
        )
    if not classification.ambiguity_reasons:
        classification.ambiguity_reasons.append(
            "basis_not_inferable: no declared basis and "
            "no unambiguous pattern detected"
        )
