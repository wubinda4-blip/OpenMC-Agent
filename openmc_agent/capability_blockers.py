"""Capability blocker classification for expert-feedback routing.

The capability report and validation report carry a mix of:

* **structural plan defects** the agent can repair itself (a universe/cell/
  lattice reference that points at an id the plan never defines, a pin-count
  mismatch, a bad radius, an axial loading that names a replacement universe
  the plan does not declare);
* **physical facts only a human expert can supply** (a material composition,
  density, or boron concentration the request left approximate);
* **environment issues** (missing cross-section data, runtime failures);
* **non-blocking fidelity warnings** (an approximate enrichment that is fine to
  run with but should be acknowledged).

The supervisor and the expert panel historically conflated these: a pile of
material assumptions masked the single structural defect that actually forced a
skeleton. This module splits them deterministically so that:

* structural blockers precede human confirmation in routing;
* the expert panel can surface the *real* execution blocker at the top;
* non-blocking material assumptions never block an otherwise runnable model.

Classification is driven by ``issue.route_hint``, ``issue.requires_human_confirmation``,
``issue.code`` prefix, and the :mod:`error_catalog` policy -- never by natural
language keywords over message text. The one exception is a **code extraction**
from ``capability.reasons``: some renderers (axial-lattice materialization) only
surface stable ``subsystem.rule`` codes inside free-text reasons rather than in
the structured ``issues`` list. Extracting a *code token* (not a keyword) is a
deterministic bridge until P0-D5 makes those renderers emit structured issues.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from openmc_agent.error_catalog import ERROR_CATALOG
from openmc_agent.schemas import (
    AgentBaseModel,
    ValidationIssue,
    ValidationReport,
)

if TYPE_CHECKING:
    from openmc_agent.schemas import SimulationPlan


# --- code-prefix policy -----------------------------------------------------

# Code prefixes that mark structural plan defects the agent can fix itself
# (reference-consistency, pin-count, cylinder geometry, axial-loading
# materialization). Matched on the stable ``subsystem.`` code prefix.
STRUCTURAL_CODE_PREFIXES: tuple[str, ...] = (
    "assembly3d.",
    "lattice_loading.",
    "axial_layer.",
    "lattice_transform.",
    "lattice.",
    "cell.",
    "universe.",
    "region.",
    "surface.",
    "core.",
    # Renderer-level materialization failures wrap structural lattice defects
    # (a loading references an undefined replacement universe); they are plan
    # defects, not missing expert facts.
    "renderer.axial_loading_materialization_failed",
    "renderer.axial_loading_base_lattice_mismatch",
    "renderer.axial_loading_base_lattice_missing",
    # A mixed percent type is a planner typo, not a missing fact.
    "material.mixed_percent_type",
)

# Codes whose route_hint the error_catalog already marks as structural repair.
STRUCTURAL_ROUTE_HINTS: frozenset[str] = frozenset(
    {"auto_repair", "reflect_plan", "retrieval"}
)

ENVIRONMENT_CODE_PREFIXES: tuple[str, ...] = ("runtime.",)

# Stable ``subsystem.rule_name`` code token embedded in renderer reason strings.
# Used ONLY to recover structured codes when ``capability.issues`` is empty
# (renderer did not emit structured issues yet). This is code extraction, not
# natural-language keyword classification.
_CODE_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9_]*\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)\b")


class CapabilityBlockerSummary(AgentBaseModel):
    """Deterministic split of capability/validation issues into routing buckets."""

    structural_agent_fixable: list[ValidationIssue] = []
    human_fact_required: list[ValidationIssue] = []
    environment_required: list[ValidationIssue] = []
    fidelity_warnings: list[str] = []
    material_assumptions: list[str] = []
    renderability: str = "none"
    is_executable: bool = False
    primary_blocker_codes: list[str] = []
    # True when the blocking codes were recovered from ``reasons`` free text
    # rather than structured ``issues`` -- i.e. the defect is not yet visible
    # to validate_plan / assess_capability and needs P0-D5 alignment so the
    # existing repair route can act on it.
    structural_issue_not_visible_to_validate_plan: bool = False

    @property
    def has_blocking_issue(self) -> bool:
        """Any structural or environment blocker that prevents execution."""
        return bool(self.structural_agent_fixable or self.environment_required)


def _error_catalog_route_hint(code: str) -> str | None:
    entry = ERROR_CATALOG.get(code)
    if entry is None:
        return None
    hint = entry.get("route_hint")
    return hint if isinstance(hint, str) else None


def is_structural_blocker_code(code: str) -> bool:
    """Public classifier: is this error code a structural (agent-fixable) defect?"""
    if any(code.startswith(prefix) for prefix in STRUCTURAL_CODE_PREFIXES):
        return True
    hint = _error_catalog_route_hint(code)
    return hint in STRUCTURAL_ROUTE_HINTS


def is_environment_blocker_code(code: str) -> bool:
    """Public classifier: is this error code an environment/runtime issue?"""
    return any(code.startswith(prefix) for prefix in ENVIRONMENT_CODE_PREFIXES)


# Internal aliases kept for the classifier above.
_is_structural_code = is_structural_blocker_code
_is_environment_code = is_environment_blocker_code


def _classify_issue(issue: ValidationIssue) -> str:
    """Return one of: structural, human_fact, environment, fidelity.

    Deterministic policy ordered by signal strength:
    1. explicit route_hint / requires_human_confirmation on the issue;
    2. error_catalog route_hint for the code;
    3. code prefix;
    4. severity (warnings -> fidelity).
    """
    # Environment codes (runtime cross-sections, openmc subprocess failures)
    # take precedence: a runtime.cross_sections_missing is an environment issue
    # even though the catalog also marks it ask_expert for confirmation.
    if _is_environment_code(issue.code):
        return "environment"
    # Explicit human-fact signal: an issue the catalog or validator flags as
    # ask_expert / requires_human_confirmation is a fact gap.
    if issue.route_hint == "ask_expert" or issue.requires_human_confirmation:
        return "human_fact"
    # Structural route hints (auto_repair / reflect_plan / retrieval).
    if issue.route_hint in STRUCTURAL_ROUTE_HINTS:
        return "structural"
    catalog_hint = _error_catalog_route_hint(issue.code)
    if catalog_hint in STRUCTURAL_ROUTE_HINTS:
        return "structural"
    if catalog_hint == "ask_expert":
        return "human_fact"
    # Code-prefix policy.
    if _is_structural_code(issue.code):
        return "structural"
    # Non-error severities are fidelity warnings, not blockers.
    if issue.severity != "error":
        return "fidelity"
    # Default: a structural plan defect the agent should attempt to repair
    # rather than asking the expert to supply a physical value.
    return "structural"


def _extract_codes_from_reasons(reasons: list[str]) -> list[tuple[str, str]]:
    """Recover (code, reason_text) pairs from free-text renderer reasons.

    Deterministic code-token extraction (``subsystem.rule_name``), used only
    when structured ``issues`` are absent. Returns pairs preserving order and
    de-duplicated by code.
    """
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for reason in reasons:
        for match in _CODE_TOKEN_RE.finditer(reason):
            code = match.group(1)
            if code in seen:
                continue
            # Filter to plausibly structural/environment codes; free text can
            # contain dotted tokens that are not error codes (e.g. "u-235.o16").
            if not (
                _is_structural_code(code)
                or _is_environment_code(code)
                or code in ERROR_CATALOG
            ):
                continue
            seen.add(code)
            pairs.append((code, reason))
    return pairs


def classify_capability_blockers(
    plan: "SimulationPlan",
    report: ValidationReport | None = None,
) -> CapabilityBlockerSummary:
    """Split a plan's capability/validation issues into routing buckets.

    Prefers structured ``capability.issues`` + ``report.issues``; falls back to
    deterministic code extraction from ``capability.reasons`` when no structured
    issues exist (the P0-D5 renderer-alignment gap).
    """
    capability = plan.capability_report
    material_assumptions = list(plan.expert_assumptions)

    structural: list[ValidationIssue] = []
    human_fact: list[ValidationIssue] = []
    environment: list[ValidationIssue] = []
    fidelity_warnings: list[str] = []
    primary_codes: list[str] = []

    # Structured issues from capability + validation report.
    structured_issues: list[ValidationIssue] = list(capability.issues)
    if report is not None:
        structured_issues.extend(report.issues)

    not_visible_to_validate_plan = False

    if structured_issues:
        for issue in structured_issues:
            bucket = _classify_issue(issue)
            if bucket == "structural":
                structural.append(issue)
            elif bucket == "human_fact":
                human_fact.append(issue)
            elif bucket == "environment":
                environment.append(issue)
            else:
                fidelity_warnings.append(issue.message)
    elif capability.renderability in {"none", "skeleton"}:
        # Renderer surfaced the blocker only as free-text reasons. Recover the
        # stable codes so the panel and routing can act on the *real* blocker
        # instead of a pile of material assumptions.
        not_visible_to_validate_plan = bool(capability.reasons)
        for code, reason in _extract_codes_from_reasons(capability.reasons):
            issue = ValidationIssue(
                severity="error",
                code=code,
                message=reason,
                route_hint="reflect_plan" if _is_structural_code(code) else None,
            )
            if _is_environment_code(code):
                environment.append(issue)
            elif _is_structural_code(code):
                structural.append(issue)
            else:
                human_fact.append(issue)

    # Blocking codes: structural + environment (the issues that forced skeleton).
    for issue in structural + environment:
        if issue.code not in primary_codes:
            primary_codes.append(issue.code)

    # Material assumptions are non-blocking fidelity notes unless the capability
    # is runnable/exportable AND there is no structural blocker; they still get
    # recorded so the expert panel can acknowledge them, but they must not mask
    # a structural blocker.
    return CapabilityBlockerSummary(
        structural_agent_fixable=structural,
        human_fact_required=human_fact,
        environment_required=environment,
        fidelity_warnings=fidelity_warnings,
        material_assumptions=material_assumptions,
        renderability=capability.renderability,
        is_executable=capability.is_executable,
        primary_blocker_codes=primary_codes,
        structural_issue_not_visible_to_validate_plan=not_visible_to_validate_plan,
    )
