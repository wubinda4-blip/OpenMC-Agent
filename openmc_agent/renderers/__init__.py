"""Pluggable renderer architecture for SimulationPlan -> OpenMC model.py.

A renderer turns a validated :class:`~openmc_agent.schemas.SimulationPlan` into an
OpenMC Python model (or a review-only skeleton). Renderers register themselves in
:data:`openmc_agent.renderers.registry.RENDERERS`; :func:`choose_renderer` picks the
highest-capability renderer with :class:`~openmc_agent.renderers.skeleton.SkeletonRenderer`
always acting as the last-resort fallback.
"""

from openmc_agent.renderers.base import BaseRenderer, RenderResult
from openmc_agent.renderers.registry import RENDERERS, choose_renderer, list_renderers

__all__ = [
    "BaseRenderer",
    "RenderResult",
    "RENDERERS",
    "choose_renderer",
    "list_renderers",
]
