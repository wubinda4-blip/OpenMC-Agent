"""Deterministic evidence-to-Facts consistency checker (Phase 8B Step 3).

Compares the evidence ledger claims (search hits, synthesised semantic
claims, requirement-structure indicators) against the generated FactsPatch
field values.  Catches conflicts that the LLM reviewer might miss when the
prompt is large or the model returns empty.

Design rules:

* **Reactor-neutral.**  Keyword sets describe *semantic roles* (scope,
  fuel variant, spacer grid, localized insert), not specific reactor types.
* **Conservative.**  Only flags a conflict when evidence *clearly* indicates
  something the FactsPatch contradicts.  Ambiguous evidence is left to the
  LLM reviewer.
* **No false negatives for empty fields.**  When evidence mentions a feature
  but the FactsPatch field is empty/missing, that is a finding (the field was
  likely omitted by the LLM).
* **Findings are deterministic.**  Each finding carries ``repairable_by_llm``
  so the gate action router can route to ``REVISE_CURRENT_PATCH`` without
  needing the LLM reviewer to rediscover the same issue.

Finding codes emitted by this module:

* ``facts.scope_evidence_conflict``      — evidence indicates multi-assembly but Facts says single
* ``facts.fuel_variant_missing``         — evidence mentions multiple enrichments but Facts has no variants
* ``facts.localized_insert_missing``     — evidence mentions control rods / Pyrex / inserts but Facts is empty
* ``facts.grid_feature_missing``         — evidence mentions spacer grids but Facts says False
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from openmc_agent.schemas import AgentBaseModel

__all__ = [
    "FactsEvidenceConsistencyFinding",
    "FactsEvidenceConsistencyResult",
    "check_facts_evidence_consistency",
]


# ---------------------------------------------------------------------------
# Keyword sets (reactor-neutral semantic indicators)
# ---------------------------------------------------------------------------

# Words that indicate a multi-assembly / full-core scope when they appear
# in evidence claim values or search excerpts.
_MULTI_ASSEMBLY_INDICATORS: tuple[str, ...] = (
    "full core",
    "full-core",
    "core physics",
    "whole core",
    "entire core",
    "3x3",
    "3 x 3",
    "core lattice",
    "assembly layout",
    "multiple assemblies",
    "multi-assembly",
    "multi assembly",
)

# Words that indicate single-assembly scope.
_SINGLE_ASSEMBLY_INDICATORS: tuple[str, ...] = (
    "single assembly",
    "single-assembly",
    "pin cell",
    "single pin",
    "fuel pin",
)

# Words that indicate fuel variants / multiple enrichments.
_FUEL_VARIANT_INDICATORS: tuple[str, ...] = (
    "enrichment",
    "fuel variant",
    "fuel variant",
    "multiple enrichments",
    "different enrichment",
    "uO2",
    "uo2",
    "burnable poison",
    "gadolinia",
    "gad2o3",
    "mox",
    "ifba",
    "ba",
)

# Words that indicate localized inserts (control rods, Pyrex, etc.)
_LOCALIZED_INSERT_INDICATORS: tuple[str, ...] = (
    "control rod",
    "rcca",
    "rod cluster",
    "pyrex",
    "thimble plug",
    "instrument tube",
    "burnable poison",
    "wet annular",
    "ba",
    "ifba",
    "waba",
    "bp",
    "insert",
)

# Words that indicate spacer grids.
_SPACER_GRID_INDICATORS: tuple[str, ...] = (
    "spacer grid",
    "spacer-grid",
    "grid strap",
    "grid spring",
    "mixing vane",
    "support grid",
    "spacer",
    "grid",
)

# model_scope values that contradict multi-assembly evidence.
_SINGLE_SCOPE_VALUES: frozenset[str] = frozenset({
    "single_pin",
    "single_assembly",
    "unknown",
})


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class FactsEvidenceConsistencyFinding(AgentBaseModel):
    """One deterministic finding from the evidence↔Facts checker."""

    code: str
    severity: str = "error"
    blocking: bool = True
    path: str = ""
    owner_patch_type: str = "facts"
    repairable_by_llm: bool = True
    requires_human: bool = False
    message: str = ""
    evidence_claim_ids: tuple[str, ...] = ()
    expected_value: str = ""
    actual_value: str = ""


class FactsEvidenceConsistencyResult(AgentBaseModel):
    """Aggregate result of the evidence↔Facts consistency check."""

    findings: list[FactsEvidenceConsistencyFinding] = []

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def to_issue_dicts(self) -> list[dict[str, Any]]:
        """Convert to the generic issue-dict shape used by the gate."""

        return [f.model_dump(mode="json") for f in self.findings]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_claim_text(claim: Any) -> str:
    """Extract a searchable text blob from an EvidenceClaim-like object."""

    parts: list[str] = []
    for attr in ("subject", "predicate"):
        val = getattr(claim, attr, "")
        if isinstance(val, str) and val:
            parts.append(val)
    value = getattr(claim, "value", None)
    if value is not None:
        try:
            if isinstance(value, str):
                parts.append(value)
            else:
                parts.append(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            parts.append(str(value))
    return " ".join(parts).lower()


def _claim_id(claim: Any) -> str:
    return getattr(claim, "claim_id", "") or ""


def _has_keyword(text: str, keywords: Iterable[str]) -> bool:
    return any(kw in text for kw in keywords)


def _collect_matching_claim_ids(
    claims: list[Any],
    keywords: Iterable[str],
) -> list[str]:
    kw_tuple = tuple(keywords)
    return [
        _claim_id(c)
        for c in claims
        if _has_keyword(_flatten_claim_text(c), kw_tuple)
    ]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_scope(
    facts_patch: dict[str, Any],
    matching_ids: list[str],
) -> FactsEvidenceConsistencyFinding | None:
    """Evidence says multi-assembly but FactsPatch scope is single/unknown."""

    model_scope = str(facts_patch.get("model_scope", "unknown"))
    if model_scope not in _SINGLE_SCOPE_VALUES:
        return None
    if not matching_ids:
        return None
    return FactsEvidenceConsistencyFinding(
        code="facts.scope_evidence_conflict",
        path="/model_scope",
        message=(
            "Evidence indicates a multi-assembly or full-core scope but the "
            f"FactsPatch model_scope is '{model_scope}'."
        ),
        evidence_claim_ids=tuple(matching_ids[:10]),
        expected_value="multi_assembly_core or full_core",
        actual_value=model_scope,
    )


def _check_fuel_variants(
    facts_patch: dict[str, Any],
    matching_ids: list[str],
) -> FactsEvidenceConsistencyFinding | None:
    """Evidence mentions fuel variants but FactsPatch has none."""

    variants = facts_patch.get("fuel_variant_requirements", []) or []
    if len(variants) > 0:
        return None
    if not matching_ids:
        return None
    return FactsEvidenceConsistencyFinding(
        code="facts.fuel_variant_missing",
        path="/fuel_variant_requirements",
        message=(
            "Evidence mentions fuel enrichment or fuel variants but "
            "fuel_variant_requirements is empty."
        ),
        evidence_claim_ids=tuple(matching_ids[:10]),
        expected_value="at least one FuelVariantRequirement",
        actual_value="[]",
    )


def _check_localized_inserts(
    facts_patch: dict[str, Any],
    matching_ids: list[str],
) -> FactsEvidenceConsistencyFinding | None:
    """Evidence mentions localized inserts but FactsPatch has none."""

    inserts = facts_patch.get("localized_insert_requirements", []) or []
    if len(inserts) > 0:
        return None
    if not matching_ids:
        return None
    return FactsEvidenceConsistencyFinding(
        code="facts.localized_insert_missing",
        path="/localized_insert_requirements",
        message=(
            "Evidence mentions control rods, Pyrex, or other localized "
            "inserts but localized_insert_requirements is empty."
        ),
        evidence_claim_ids=tuple(matching_ids[:10]),
        expected_value="at least one LocalizedInsertPlacementRequirement",
        actual_value="[]",
    )


def _check_spacer_grids(
    facts_patch: dict[str, Any],
    matching_ids: list[str],
) -> FactsEvidenceConsistencyFinding | None:
    """Evidence mentions spacer grids but FactsPatch says False or None."""

    has_grids = facts_patch.get("has_spacer_grids")
    if has_grids is True:
        return None
    if not matching_ids:
        return None
    return FactsEvidenceConsistencyFinding(
        code="facts.grid_feature_missing",
        path="/has_spacer_grids",
        message=(
            "Evidence mentions spacer grids but has_spacer_grids is "
            f"{has_grids!r} (not True)."
        ),
        evidence_claim_ids=tuple(matching_ids[:10]),
        expected_value="True",
        actual_value=str(has_grids),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def check_facts_evidence_consistency(
    *,
    facts_patch: dict[str, Any],
    evidence_claims: list[Any] | Any,
) -> FactsEvidenceConsistencyResult:
    """Run all evidence↔FactsPatch consistency checks.

    Parameters
    ----------
    facts_patch
        The FactsPatch serialised as a dict (``patch.model_dump(mode="json")``).
    evidence_claims
        A list of EvidenceClaim-like objects (objects with ``subject``,
        ``predicate``, ``value``, ``claim_id`` attributes).  A ledger's
        ``claims.values()`` is the typical source.

    Returns
    -------
    FactsEvidenceConsistencyResult
        ``ok`` is True when no error-level findings were produced.
    """

    # Accept ledger.claims dict or list.
    if hasattr(evidence_claims, "values"):
        claims_list = list(evidence_claims.values())
    elif isinstance(evidence_claims, (list, tuple)):
        claims_list = list(evidence_claims)
    else:
        claims_list = []

    findings: list[FactsEvidenceConsistencyFinding] = []

    # Scope
    scope_ids = _collect_matching_claim_ids(claims_list, _MULTI_ASSEMBLY_INDICATORS)
    # Exclude if single-assembly indicators dominate (ambiguous).
    single_ids = _collect_matching_claim_ids(claims_list, _SINGLE_ASSEMBLY_INDICATORS)
    if scope_ids and not (len(single_ids) > len(scope_ids) * 2):
        finding = _check_scope(facts_patch, scope_ids)
        if finding:
            findings.append(finding)

    # Fuel variants
    fuel_ids = _collect_matching_claim_ids(claims_list, _FUEL_VARIANT_INDICATORS)
    finding = _check_fuel_variants(facts_patch, fuel_ids)
    if finding:
        findings.append(finding)

    # Localized inserts
    insert_ids = _collect_matching_claim_ids(claims_list, _LOCALIZED_INSERT_INDICATORS)
    finding = _check_localized_inserts(facts_patch, insert_ids)
    if finding:
        findings.append(finding)

    # Spacer grids
    grid_ids = _collect_matching_claim_ids(claims_list, _SPACER_GRID_INDICATORS)
    finding = _check_spacer_grids(facts_patch, grid_ids)
    if finding:
        findings.append(finding)

    return FactsEvidenceConsistencyResult(findings=findings)
