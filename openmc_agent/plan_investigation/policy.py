"""Patch-type specific investigation policies.

A policy is a *suggestion*: it tells the LLM which tools are typically
useful and which search terms tend to appear in source documents for a
given patch type.  The LLM is free to deviate; the budget and tool
registry enforce the hard constraints.

Reactor-neutrality: every search term is a generic descriptor any
PWR / BWR / VVER / HTGR / SFR / CANDU / MOX problem statement might
use.  No reactor-specific names.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .tool_registry import (
    TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
    TOOL_NAME_SEARCH_SOURCE_INDEX,
)

__all__ = [
    "InvestigationPolicy",
    "InvestigationPolicyRegistry",
    "DEFAULT_INVESTIGATION_POLICIES",
    "default_policy_registry",
]


class InvestigationPolicy(AgentBaseModel):
    """Suggested tool calls + search terms for one patch type."""

    patch_type: str
    recommended_tools: tuple[str, ...] = Field(default_factory=tuple)
    recommended_search_terms: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""

    def render_suggestions(self) -> list[str]:
        """Return a flat list of human-readable suggestion strings.

        Used by :func:`build_investigation_prompt` to give the LLM a
        starting point without forcing it down any specific path.
        """

        out: list[str] = []
        if self.recommended_tools:
            out.append(
                "recommended tools: " + ", ".join(self.recommended_tools)
            )
        if self.recommended_search_terms:
            joined = ", ".join(repr(term) for term in self.recommended_search_terms)
            out.append(f"useful search_source_index queries: {joined}")
        if self.notes:
            out.append(self.notes)
        return out


# ---------------------------------------------------------------------------
# Default policy table
# ---------------------------------------------------------------------------


def _build_default_policies() -> dict[str, InvestigationPolicy]:
    return {
        "facts": InvestigationPolicy(
            patch_type="facts",
            recommended_tools=(
                TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
                TOOL_NAME_SEARCH_SOURCE_INDEX,
            ),
            recommended_search_terms=(
                "full core",
                "assembly",
                "lattice",
                "loading",
                "enrichment",
            ),
            notes=(
                "Look for scope indicators (full core vs single assembly), "
                "grid size notation (N x N), and the assembly count before "
                "deciding model_scope."
            ),
        ),
        "materials": InvestigationPolicy(
            patch_type="materials",
            recommended_tools=(TOOL_NAME_SEARCH_SOURCE_INDEX,),
            recommended_search_terms=(
                "material",
                "density",
                "composition",
                "boron",
                "stainless",
            ),
            notes=(
                "Verify density units and composition basis against the "
                "source text before committing a MaterialSpec."
            ),
        ),
        "universes": InvestigationPolicy(
            patch_type="universes",
            recommended_tools=(TOOL_NAME_SEARCH_SOURCE_INDEX,),
            recommended_search_terms=(
                "fuel pin",
                "guide tube",
                "RCCA",
                "Pyrex",
                "universe",
            ),
            notes=(
                "Identify all distinct cell types (fuel / guide tube / "
                "instrument / insert) the source document requires."
            ),
        ),
        "axial_layers": InvestigationPolicy(
            patch_type="axial_layers",
            recommended_tools=(TOOL_NAME_SEARCH_SOURCE_INDEX,),
            recommended_search_terms=(
                "spacer grid",
                "axial",
                "control rod",
                "insertion",
            ),
            notes=(
                "Capture the axial segmentation and any spacer-grid "
                "z-locations the source specifies."
            ),
        ),
        "axial_overlays": InvestigationPolicy(
            patch_type="axial_overlays",
            recommended_tools=(TOOL_NAME_SEARCH_SOURCE_INDEX,),
            recommended_search_terms=("spacer grid", "axial", "overlay"),
            notes=("Mirror axial_layers policy for spacer-grid / overlay bands."),
        ),
    }


DEFAULT_INVESTIGATION_POLICIES: dict[str, InvestigationPolicy] = _build_default_policies()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class InvestigationPolicyRegistry(AgentBaseModel):
    """Holds per-patch-type :class:`InvestigationPolicy` records.

    Step 3 ships a default registry via :func:`default_policy_registry`.
    Callers can extend or override entries without touching the default
    table.
    """

    policies: dict[str, InvestigationPolicy] = Field(default_factory=dict)

    def register(self, policy: InvestigationPolicy) -> None:
        self.policies[policy.patch_type] = policy

    def get(self, patch_type: str) -> InvestigationPolicy:
        """Return the policy for ``patch_type``.

        Unknown patch types return an empty policy (no recommendations);
        the LLM still receives the standard tool list and budget.
        """

        return self.policies.get(
            patch_type,
            InvestigationPolicy(patch_type=patch_type),
        )

    def suggestions_for(self, patch_type: str) -> list[str]:
        return self.get(patch_type).render_suggestions()


def default_policy_registry() -> InvestigationPolicyRegistry:
    """Return a registry seeded with the Step 3 default policy table."""

    registry = InvestigationPolicyRegistry()
    for policy in DEFAULT_INVESTIGATION_POLICIES.values():
        registry.register(policy)
    return registry
