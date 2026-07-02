"""Renderer registry and selection logic.

``choose_renderer`` returns the highest-capability renderer for a plan. Priority is
``runnable > exportable > skeleton > none``; ties break toward earlier registry
order so specialized renderers always beat the generic skeleton fallback, which
is deliberately registered last.
"""

from __future__ import annotations

from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.renderers.base import BaseRenderer
from openmc_agent.renderers.core import CoreRenderer
from openmc_agent.renderers.pin_cell import PinCellRenderer
from openmc_agent.renderers.skeleton import SkeletonRenderer
from openmc_agent.renderers.triso import TrisoRenderer
from openmc_agent.schemas import RenderCapabilityReport, SimulationPlan

# Specialized renderers first; SkeletonRenderer must stay last as the fallback.
RENDERERS: list[BaseRenderer] = [
    PinCellRenderer(),
    RectAssemblyRenderer(),
    TrisoRenderer(),
    CoreRenderer(),
    SkeletonRenderer(),
]


def list_renderers() -> list[BaseRenderer]:
    """Return a shallow copy of the registered renderers."""
    return list(RENDERERS)


def choose_renderer(
    plan: SimulationPlan,
) -> tuple[BaseRenderer | None, RenderCapabilityReport]:
    """Pick the most capable renderer for ``plan``.

    Returns ``(renderer, capability_report)``. When no renderer (not even the
    skeleton fallback) can handle the plan, ``renderer`` is ``None`` and the
    report has ``renderability='none'``.
    """
    best: tuple[int, int, BaseRenderer, RenderCapabilityReport] | None = None
    for index, renderer in enumerate(RENDERERS):
        report = renderer.can_render(plan)
        rank = BaseRenderer.renderability_rank(report.renderability)
        # Sort key: higher rank first, then earlier registry index (via -index).
        key = (rank, -index)
        if best is None or key > (best[0], -best[1]):
            best = (rank, index, renderer, report)

    assert best is not None
    rank, _index, renderer, report = best
    if rank <= 0:
        return None, report
    return renderer, report
