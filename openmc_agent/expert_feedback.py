"""Expert-feedback semantics: question grouping, decisions, acknowledgements.

The legacy expert loop showed up to eight near-duplicate material assumptions
("enrichment is approximate", "composition_status=approximate",
"Table P3-2; O16 stoichiometric") as separate questions, while the *real*
execution blocker (a structural defect) stayed hidden. This module provides
deterministic, LLM-free machinery to:

* :class:`ExpertQuestionGroup` -- merge per-material assumption duplicates into
  one confirmable group, preserving every ``source_item``.
* :class:`ExpertFeedbackDecision` -- an explicit user action
  (accept/defer/repair/review-only/abort) that replaces the ambiguous "empty
  enter = continue" contract.
* :class:`ExpertAssumptionAcknowledgement` -- a run-level acknowledgement that
  the user accepted an approximation **without** mutating the plan's physical
  fields (composition_status, density, nuclide fractions, boron ppm).

Nothing here calls an LLM. Grouping keys are derived from the plan's material
ids/names plus reactor-agnostic fact kinds (enrichment -> fuel, boron -> water);
no VERA3B-specific text is hardcoded.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from openmc_agent.schemas import AgentBaseModel

if TYPE_CHECKING:
    from openmc_agent.capability_blockers import CapabilityBlockerSummary
    from openmc_agent.schemas import SimulationPlan


# --- attribution helpers (reactor-agnostic) ---------------------------------

_MATERIAL_PREFIX_RE = re.compile(r"material\s+([A-Za-z0-9_\-]+)\s*:")
_ALLOY_LIBRARY_RE = re.compile(
    r"alloy_library.*substituted nominal (.*?)(?:\s*composition|\s*\(nominal\)|$)",
    re.IGNORECASE,
)
_APPROXIMATED_AS_RE = re.compile(r"(.+?)\s+approximated as pure\s+", re.IGNORECASE)

# Generic alloy-family -> id-prefix map. Reactor-agnostic: these are standard
# metallurgical family names, not a specific benchmark's materials.
_ALLOY_FAMILY_PREFIX: list[tuple[str, str]] = [
    ("stainless steel", "ss"),
    ("stainless", "ss"),
    ("zircaloy", "zircaloy"),
    ("zr-4", "zircaloy"),
    ("inconel", "inconel"),
    ("haynes", "haynes"),
    ("hastelloy", "hastelloy"),
]

# Fact-kind detectors (order matters: most specific first).
_FACT_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("enrichment", re.compile(r"\benrichment\b", re.IGNORECASE)),
    ("boron_concentration", re.compile(r"\bboron\b.*\bconcentration\b|\bppm\b", re.IGNORECASE)),
    ("boron_concentration", re.compile(r"\bborated\b.*\bwater\b", re.IGNORECASE)),
    ("density", re.compile(r"\bdensity\b", re.IGNORECASE)),
    ("composition_status", re.compile(r"composition_status\s*=", re.IGNORECASE)),
    ("composition", re.compile(r"compos", re.IGNORECASE)),
    ("alloy_substitution", re.compile(r"alloy_library", re.IGNORECASE)),
    ("source_note", re.compile(r"table|§|source|note", re.IGNORECASE)),
]


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _build_material_aliases(plan: "SimulationPlan | None") -> dict[str, str]:
    """Map every normalized alias (id, name, alloy family) -> material id."""
    aliases: dict[str, str] = {}
    if plan is None or plan.complex_model is None:
        return aliases
    for material in plan.complex_model.materials:
        mid = material.id
        aliases[_normalize_token(mid)] = mid
        if material.name:
            aliases[_normalize_token(material.name)] = mid
        # Alloy-family prefix: "Stainless Steel 304" -> family "stainless steel"
        # -> id prefix "ss"; match the material whose id starts with it.
        name_norm = (material.name or mid).lower()
        for family, prefix in _ALLOY_FAMILY_PREFIX:
            if family in name_norm or family in mid.lower():
                if mid.lower().startswith(prefix) or prefix in mid.lower():
                    aliases.setdefault(family, mid)
    return aliases


def _attribute_subject(text: str, aliases: dict[str, str], plan: "SimulationPlan | None") -> str | None:
    """Return the material id this assumption is about, or None."""
    # 1. Explicit "material <id>:" prefix.
    m = _MATERIAL_PREFIX_RE.search(text)
    if m:
        direct = _normalize_token(m.group(1))
        for alias, mid in aliases.items():
            if alias == direct or direct == _normalize_token(mid):
                return mid
        # Fall through: the id itself may not be in aliases if plan is None.
        return m.group(1)

    # 2. Alloy-library substitution / "<Name> approximated as pure <elt>".
    for pattern in (_ALLOY_LIBRARY_RE, _APPROXIMATED_AS_RE):
        m = pattern.search(text)
        if m:
            alias_name = m.group(1).strip().lower()
            norm = _normalize_token(alias_name)
            if norm in aliases:
                return aliases[norm]
            for family, prefix in _ALLOY_FAMILY_PREFIX:
                if family in alias_name:
                    if family in aliases:
                        return aliases[family]
                    # Family alias absent (e.g. material name "SS304" does not
                    # contain "stainless steel"): resolve by id/name prefix.
                    by_prefix = _find_material_by_prefix(plan, prefix)
                    if by_prefix:
                        return by_prefix

    # 3. Generic physical facts: enrichment -> fuel, boron -> borated water.
    lowered = text.lower()
    if "enrichment" in lowered:
        fuel = _find_material(plan, ("fuel", "uo2", "uran"))
        if fuel:
            return fuel
    if "boron" in lowered or "borated" in lowered:
        water = _find_material(plan, ("boron", "water", "ppm"))
        if water:
            return water

    # 4. Any normalized material alias mentioned verbatim in the text.
    norm_text = _normalize_token(text)
    for alias, mid in aliases.items():
        if len(alias) >= 5 and alias in norm_text:
            return mid
    return None


def _find_material(plan: "SimulationPlan | None", tokens: tuple[str, ...]) -> str | None:
    if plan is None or plan.complex_model is None:
        return None
    for material in plan.complex_model.materials:
        haystack = f"{material.id} {material.name or ''}".lower()
        if any(tok in haystack for tok in tokens):
            return material.id
    return None


def _find_material_by_prefix(plan: "SimulationPlan | None", prefix: str) -> str | None:
    if plan is None or plan.complex_model is None:
        return None
    for material in plan.complex_model.materials:
        if material.id.lower().startswith(prefix):
            return material.id
    return None


def _detect_fact_kind(text: str) -> str:
    for kind, pattern in _FACT_KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return "other"


def _category_for(subject_id: str | None, fact_kind: str) -> str:
    if subject_id is not None:
        if fact_kind == "density":
            return "material_density"
        return "material_composition"
    if fact_kind == "boron_concentration":
        return "operating_condition"
    if fact_kind in ("enrichment", "density", "composition", "composition_status"):
        return "material_composition"
    return "other"


# --- models -----------------------------------------------------------------


class ExpertQuestionGroup(AgentBaseModel):
    """A single confirmable expert question that may merge several assumptions."""

    question_id: str
    category: Literal[
        "material_composition",
        "material_density",
        "operating_condition",
        "geometry_fact",
        "environment",
        "other",
    ]
    subject_id: str | None = None
    prompt: str
    source_items: list[str]
    blocking: bool = False
    accepted_effect: str
    deferred_effect: str


class ExpertFeedbackDecision(AgentBaseModel):
    """Explicit expert-feedback action, replacing ambiguous empty-enter semantics."""

    action: Literal[
        "accept_assumptions_for_this_run",
        "provide_corrections",
        "defer_confirmations",
        "continue_repair",
        "accept_review_only",
        "abort",
    ]
    feedback_items: list[str] = []
    acknowledged_question_ids: list[str] = []
    deferred_question_ids: list[str] = []
    reason: str | None = None


class ExpertAssumptionAcknowledgement(AgentBaseModel):
    """Run-level acknowledgement of an approximation; never mutates plan physics."""

    question_id: str
    status: Literal["accepted_for_run", "deferred", "corrected"]
    answer: str | None = None
    round_index: int
    plan_hash: str
    timestamp: str | None = None


# --- grouping ---------------------------------------------------------------


def group_expert_questions(
    plan: "SimulationPlan | None",
    summary: "CapabilityBlockerSummary | None" = None,
    *,
    assumptions: list[str] | None = None,
) -> list[ExpertQuestionGroup]:
    """Merge per-material assumption duplicates into confirmable groups.

    Deterministic and LLM-free. Items about the same material subject collapse
    into one group that preserves every ``source_item``. ``summary`` provides the
    blocking flag (material assumptions are never blocking on their own; only a
    structural/environment blocker is).
    """
    raw_assumptions = (
        assumptions
        if assumptions is not None
        else (list(plan.expert_assumptions) if plan is not None else [])
    )
    aliases = _build_material_aliases(plan)

    blocking = bool(summary and summary.has_blocking_issue)

    groups: dict[tuple[str, str | None], ExpertQuestionGroup] = {}
    order: list[tuple[str, str | None]] = []

    for item in raw_assumptions:
        if not item or not item.strip():
            continue
        subject_id = _attribute_subject(item, aliases, plan)
        fact_kind = _detect_fact_kind(item)
        category = _category_for(subject_id, fact_kind)
        key = (category, subject_id)
        if key not in groups:
            order.append(key)
            label = subject_id or fact_kind
            groups[key] = ExpertQuestionGroup(
                question_id=f"{category}:{label}",
                category=category,  # type: ignore[arg-type]
                subject_id=subject_id,
                prompt=_group_prompt(category, subject_id, fact_kind),
                source_items=[],
                blocking=False,
                accepted_effect=(
                    "Run proceeds with the current approximation; the plan's "
                    "composition, density, and nuclide fractions are unchanged."
                ),
                deferred_effect=(
                    "Approximation stays unconfirmed; recorded as a pending "
                    "fidelity warning, not as confirmed physics."
                ),
            )
        groups[key].source_items.append(item)

    # Material assumptions never block on their own; a structural/environment
    # blocker does. Keep blocking=False here so the panel can separate them.
    return [groups[key] for key in order]


def _group_prompt(category: str, subject_id: str | None, fact_kind: str) -> str:
    if subject_id is not None:
        if category == "material_density":
            return (
                f"Confirm or correct the density approximation for material "
                f"{subject_id!r} for this run (or defer)."
            )
        return (
            f"Confirm or correct the composition/enrichment approximation for "
            f"material {subject_id!r} for this run (or defer)."
        )
    if fact_kind == "boron_concentration":
        return "Confirm or correct the boron concentration / borated water assumption for this run (or defer)."
    if fact_kind == "enrichment":
        return "Confirm or correct the fuel enrichment approximation for this run (or defer)."
    if fact_kind == "density":
        return "Confirm or correct the density approximation for this run (or defer)."
    return "Confirm or correct this modeling approximation for this run (or defer)."


# --- decisions --------------------------------------------------------------


def interpret_empty_feedback(
    *,
    renderability: str,
    has_blocking_issue: bool,
) -> ExpertFeedbackDecision:
    """Deterministic interpretation of an empty (just-enter) expert reply.

    Empty no longer means a vague "accept the current artifact and continue".
    For a runnable model with only non-blocking assumptions it is an explicit
    *defer* (the run may proceed). For a skeleton/blocked model it is a
    *review-only* acceptance: the skeleton is kept for review but never executed,
    and the outcome is BLOCKED_REVIEW_ONLY, never a silent success.
    """
    if renderability in {"skeleton", "none"} or has_blocking_issue:
        return ExpertFeedbackDecision(
            action="accept_review_only",
            reason=(
                "empty feedback on a non-executable model: keep the review-only "
                "skeleton, do not attempt OpenMC, outcome=BLOCKED_REVIEW_ONLY"
            ),
        )
    return ExpertFeedbackDecision(
        action="defer_confirmations",
        reason=(
            "empty feedback on an executable model with only non-blocking "
            "assumptions: defer confirmations and continue"
        ),
    )


# --- acknowledgements -------------------------------------------------------

_ACK_STATUS_BY_ACTION: dict[str, Literal["accepted_for_run", "deferred", "corrected"]] = {
    "accept_assumptions_for_this_run": "accepted_for_run",
    "defer_confirmations": "deferred",
    "provide_corrections": "corrected",
}


def build_assumption_acknowledgements(
    decision: ExpertFeedbackDecision,
    *,
    question_ids: list[str],
    round_index: int,
    plan_hash: str,
    timestamp: str | None = None,
) -> list[ExpertAssumptionAcknowledgement]:
    """Create run-level acknowledgements for the decision's question ids.

    Accept/defer/correct only records the user's disposition; it never mutates
    composition_status, density, nuclide fractions, or boron ppm.
    """
    status = _ACK_STATUS_BY_ACTION.get(decision.action, "deferred")
    target_ids: list[str]
    if decision.action == "accept_assumptions_for_this_run":
        target_ids = decision.acknowledged_question_ids or question_ids
    elif decision.action == "defer_confirmations":
        target_ids = decision.deferred_question_ids or question_ids
    elif decision.action == "provide_corrections":
        target_ids = list(question_ids)
    else:
        return []
    seen: set[str] = set()
    acks: list[ExpertAssumptionAcknowledgement] = []
    for qid in target_ids:
        if qid in seen:
            continue
        seen.add(qid)
        acks.append(
            ExpertAssumptionAcknowledgement(
                question_id=qid,
                status=status,
                answer=None,
                round_index=round_index,
                plan_hash=plan_hash,
                timestamp=timestamp,
            )
        )
    return acks
