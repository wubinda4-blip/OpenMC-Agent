"""Reserved interface for agent-authored renderers.

This package is intentionally a stub. The plan is that, when no registered
renderer can handle a :class:`~openmc_agent.schemas.SimulationPlan`, the agent
could eventually propose a *new* renderer on the fly. That is powerful and
dangerous, so the surface area is fixed here and gated behind strict safety
constraints (see :mod:`openmc_agent.renderer_authoring.sandbox`).

The main workflow does NOT call this yet. ``choose_renderer`` is the only path
used today; :class:`RendererAuthoringAgent.propose_renderer` always reports that
autonomous renderer authoring is not implemented.
"""

from openmc_agent.renderer_authoring.planner import (
    AUTHORING_NOT_IMPLEMENTED,
    CandidateRenderer,
    RendererAuthoringAgent,
    SafetyConstraints,
)

__all__ = [
    "CandidateRenderer",
    "RendererAuthoringAgent",
    "SafetyConstraints",
    "AUTHORING_NOT_IMPLEMENTED",
]
