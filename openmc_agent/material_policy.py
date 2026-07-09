"""Material composition policy and resolver integration.

Decides whether a material's composition should be:

- preserved exactly as the plan/patch provided (``PRESERVE_PLAN``),
- replaced by the controlled alloy library when the plan reduced a known
  structural alloy to its base pure element (``APPLY_ALLOY_LIBRARY``), or
- left untouched unless explicitly marked as library-substitutable
  (``STRICT_CONFIRMED_ONLY``).

The default policy is :data:`DEFAULT_MATERIAL_POLICY` = ``APPLY_ALLOY_LIBRARY``,
which restores minor constituents (Sn/Cr/Ni/Nb/Mo/...) for known structural
alloys that were reduced to pure Zr / Fe / Ni, while leaving fuel, water,
helium, pyrex, and unknown materials untouched.

This module never fabricates densities, isotopic expansions, or benchmark
constants. It only substitutes *publicly documented nominal alloy compositions*
and records every substitution in a :class:`MaterialCompositionReport`.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import Field

from openmc_agent.material_library import (
    AlloyComposition,
    canonical_alloy_id,
    get_alloy_composition,
)
from openmc_agent.schemas import AgentBaseModel, ComplexMaterialSpec, NuclideSpec

if TYPE_CHECKING:
    # Imported lazily at runtime inside functions that need it, to avoid a
    # circular import: the plan_builder package imports this module.
    from openmc_agent.plan_builder.patches import MaterialSpecPatch


class MaterialCompositionPolicy(str, Enum):
    """How to treat material compositions during assembly/rendering."""

    PRESERVE_PLAN = "preserve_plan"
    APPLY_ALLOY_LIBRARY = "apply_alloy_library"
    STRICT_CONFIRMED_ONLY = "strict_confirmed_only"


DEFAULT_MATERIAL_POLICY = MaterialCompositionPolicy.APPLY_ALLOY_LIBRARY


# Base elements that the plan typically reduces each alloy to. A material is
# considered a "pure-element approximation" of an alloy when:
#   1. its name/id canonicalizes to a known alloy, AND
#   2. its composition is a single entry whose name equals the base element
#      (or is empty / marked needs_library / approximate).
_ALLOY_BASE_ELEMENT: dict[str, str] = {
    "zircaloy4": "Zr",
    "ss304": "Fe",
    "inconel718": "Ni",
}


# Material ids/roles that should NEVER be replaced by the alloy library, even
# under APPLY_ALLOY_LIBRARY. Fuel, moderator, poison, helium and pyrex are
# composition-sensitive and must stay as the plan/patch specified.
_PROTECTED_ROLES = {
    "fuel",
    "moderator",
    "coolant",
    "poison",
    "burnable_poison",
    "burnable-poison",
    "gas",
    "gap_gas",
    "helium",
    "pyrex",
}

_PROTECTED_ID_SUBSTRINGS = (
    "fuel",
    "water",
    "moderator",
    "coolant",
    "borated",
    "pyrex",
    "helium",
    "he_",
    "he4",
)


class MaterialCompositionEntryReport(AgentBaseModel):
    """Per-material outcome of applying the composition policy."""

    material_id: str
    name: str
    alloy_library_applied: bool = False
    alloy_id: str | None = None
    basis: str = "weight_frac"
    elements: dict[str, float] = Field(default_factory=dict)
    original_composition_status: str | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class MaterialCompositionReport(AgentBaseModel):
    """Structured report describing how materials were resolved."""

    composition_policy: str
    materials: list[MaterialCompositionEntryReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MaterialPolicyDecision(AgentBaseModel):
    """Outcome of evaluating one material against the policy."""

    apply_library: bool = False
    alloy_id: str | None = None
    alloy: AlloyComposition | None = None
    reason: str
    issue_code: str | None = None


def _is_protected_material(material_id: str, role: str) -> bool:
    """True for fuel/water/helium/pyrex and similar composition-sensitive mats."""
    mid = (material_id or "").lower()
    r = (role or "").lower()
    if r in _PROTECTED_ROLES:
        return True
    return any(sub in mid for sub in _PROTECTED_ID_SUBSTRINGS)


def _composition_is_pure_base(
    composition: dict[str, float] | list[NuclideSpec] | None,
    base_element: str,
) -> bool:
    """True if the composition is exactly the single base pure element."""
    if composition is None:
        return True
    if isinstance(composition, dict):
        names = [n for n, v in composition.items() if v and v > 0]
        if len(names) == 0:
            return True
        if len(names) == 1:
            return names[0] == base_element
        return False
    # list of NuclideSpec
    names = [c.name for c in composition if c.percent and c.percent > 0]
    if len(names) == 0:
        return True
    if len(names) == 1:
        return names[0] == base_element
    return False


def evaluate_material_policy(
    material_id: str,
    name: str,
    role: str,
    composition: dict[str, float] | list[NuclideSpec] | None,
    composition_status: str | None,
    policy: MaterialCompositionPolicy,
) -> MaterialPolicyDecision:
    """Decide whether the alloy library should replace this material's composition.

    Returns a :class:`MaterialPolicyDecision` with the resolved alloy (if any)
    and an issue code suitable for the trace/capability report:

    - ``materials.alloy_library_applied``: library composition was substituted.
    - ``materials.alloy_library_skipped_protected``: fuel/water/... preserved.
    - ``materials.alloy_library_missing``: name suggests an alloy but no entry.
    - ``materials.alloy_library_preserve_plan``: policy asked to preserve.
    """
    if policy == MaterialCompositionPolicy.PRESERVE_PLAN:
        return MaterialPolicyDecision(
            apply_library=False,
            reason="policy=preserve_plan; keeping plan composition as-is",
            issue_code="materials.alloy_library_preserve_plan",
        )

    if _is_protected_material(material_id, role):
        return MaterialPolicyDecision(
            apply_library=False,
            reason=f"material {material_id!r} is protected (fuel/moderator/gas/pyrex)",
            issue_code="materials.alloy_library_skipped_protected",
        )

    alloy_id = canonical_alloy_id(material_id) or canonical_alloy_id(name)
    if alloy_id is None:
        return MaterialPolicyDecision(
            apply_library=False,
            reason=f"no alloy library entry for {material_id!r}",
        )
    alloy = get_alloy_composition(alloy_id)
    if alloy is None:
        return MaterialPolicyDecision(
            apply_library=False,
            reason=f"alloy id {alloy_id!r} resolved but no composition available",
            issue_code="materials.alloy_library_missing",
        )

    base = _ALLOY_BASE_ELEMENT.get(alloy_id)
    if base is None:
        return MaterialPolicyDecision(
            apply_library=False,
            reason=f"alloy {alloy_id!r} has no base-element heuristic",
        )

    is_pure_base = _composition_is_pure_base(composition, base)

    if policy == MaterialCompositionPolicy.STRICT_CONFIRMED_ONLY:
        # Only substitute when the plan explicitly asked for it.
        if composition_status not in {"needs_library", "approximate"}:
            return MaterialPolicyDecision(
                apply_library=False,
                reason=(
                    f"policy=strict_confirmed_only and status={composition_status!r} "
                    f"did not request library substitution"
                ),
                issue_code="materials.alloy_library_preserve_plan",
            )

    if not is_pure_base:
        # The plan already provides a richer composition; do not overwrite it.
        return MaterialPolicyDecision(
            apply_library=False,
            alloy_id=alloy_id,
            reason=(
                f"material {material_id!r} already has a multi-element composition; "
                f"library not applied to avoid clobbering plan-provided composition"
            ),
            issue_code="materials.alloy_library_skipped_rich",
        )

    return MaterialPolicyDecision(
        apply_library=True,
        alloy_id=alloy_id,
        alloy=alloy,
        reason=(
            f"material {material_id!r} canonicalizes to {alloy_id!r} and is currently "
            f"pure {base}; substituting nominal alloy composition"
        ),
        issue_code="materials.alloy_library_applied",
    )


# ---------------------------------------------------------------------------
# Patch-level and ComplexMaterialSpec-level application helpers
# ---------------------------------------------------------------------------


def apply_policy_to_material_patch(
    mat: MaterialSpecPatch,
    policy: MaterialCompositionPolicy = DEFAULT_MATERIAL_POLICY,
) -> tuple[MaterialSpecPatch, MaterialPolicyDecision]:
    """Return a (possibly rewritten) :class:`MaterialSpecPatch` and decision.

    The returned patch is a new object; the input is not mutated.
    """
    decision = evaluate_material_policy(
        material_id=mat.material_id,
        name=mat.name,
        role=mat.role,
        composition=mat.composition,
        composition_status=mat.composition_status,
        policy=policy,
    )
    if not decision.apply_library or decision.alloy is None:
        return mat, decision

    alloy = decision.alloy
    new_composition = dict(alloy.elements)
    new_warnings = list(mat.warnings)
    note = (
        f"alloy_library: substituted nominal {alloy.display_name} composition "
        f"({alloy.approximation_level})"
    )
    if note not in new_warnings:
        new_warnings.append(note)

    rewritten = mat.model_copy(
        update={
            "composition": new_composition,
            "composition_basis": "weight_frac",
            "composition_status": "approximate",
            "source_note": (
                mat.source_note + " | " + alloy.source_note
                if mat.source_note
                else alloy.source_note
            ),
            "warnings": new_warnings,
        }
    )
    return rewritten, decision


def build_composition_report(
    materials: list[MaterialSpecPatch],
    policy: MaterialCompositionPolicy = DEFAULT_MATERIAL_POLICY,
    decisions: dict[str, MaterialPolicyDecision] | None = None,
) -> MaterialCompositionReport:
    """Build a structured report from a set of material patches + decisions."""
    decisions = decisions or {}
    entries: list[MaterialCompositionEntryReport] = []
    warnings: list[str] = []
    notes: list[str] = [
        "Smoke-level material composition report; not a benchmark-accuracy statement.",
        "Alloy compositions are nominal engineering approximations and replaceable.",
    ]

    for mat in materials:
        decision = decisions.get(mat.material_id)
        if decision is None:
            decision = evaluate_material_policy(
                material_id=mat.material_id,
                name=mat.name,
                role=mat.role,
                composition=mat.composition,
                composition_status=mat.composition_status,
                policy=policy,
            )
        applied = decision.apply_library and decision.alloy is not None
        elements = (
            dict(decision.alloy.elements) if applied and decision.alloy else dict(mat.composition)
        )
        entry = MaterialCompositionEntryReport(
            material_id=mat.material_id,
            name=mat.name,
            alloy_library_applied=applied,
            alloy_id=decision.alloy_id if applied else None,
            basis="weight_frac" if applied else (mat.composition_basis or "weight_frac"),
            elements=elements,
            original_composition_status=mat.composition_status,
            reason=decision.reason,
            warnings=list(mat.warnings),
        )
        entries.append(entry)
        if decision.issue_code == "materials.alloy_library_missing":
            warnings.append(
                f"material {mat.material_id!r} looks like an alloy but has no "
                f"library entry; kept plan composition"
            )
    return MaterialCompositionReport(
        composition_policy=policy.value,
        materials=entries,
        warnings=warnings,
        notes=notes,
    )


def policy_from_value(value: str | MaterialCompositionPolicy | None) -> MaterialCompositionPolicy:
    """Parse a policy from a string, enum, or None (returns default)."""
    if value is None:
        return DEFAULT_MATERIAL_POLICY
    if isinstance(value, MaterialCompositionPolicy):
        return value
    try:
        return MaterialCompositionPolicy(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"unknown material composition policy {value!r}; expected one of "
            f"{[p.value for p in MaterialCompositionPolicy]}"
        ) from exc


__all__ = [
    "MaterialCompositionPolicy",
    "DEFAULT_MATERIAL_POLICY",
    "MaterialCompositionReport",
    "MaterialCompositionEntryReport",
    "MaterialPolicyDecision",
    "evaluate_material_policy",
    "apply_policy_to_material_patch",
    "build_composition_report",
    "policy_from_value",
]
