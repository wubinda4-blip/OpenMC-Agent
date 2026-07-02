"""Renderer base interface and the :class:`RenderResult` contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ConfigDict, Field

from openmc_agent.schemas import (
    RENDERABILITY_RANK,
    AgentBaseModel,
    Renderability,
    RenderCapabilityReport,
    SimulationPlan,
)


class RenderResult(AgentBaseModel):
    """Everything a renderer produces for one plan.

    ``script`` holds the text written to ``model.py``. ``output_files`` lists the
    paths actually written under ``outdir`` (model.py plus sidecars such as
    ``capability_report.json`` and ``TODO.md``).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    renderer_name: str = Field(description="Name of the renderer that produced this result.")
    renderability: Renderability = Field(
        description="Code-generation level reached: none | skeleton | exportable | runnable."
    )
    is_executable: bool = Field(
        description="True when the renderer emitted XML-ready code (exportable or runnable)."
    )
    script: str = Field(default="", description="Text written to model.py, empty if nothing was rendered.")
    output_files: list[str] = Field(default_factory=list, description="Absolute paths written under outdir.")
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    capability: RenderCapabilityReport = Field(
        description="The capability report computed by can_render for this plan."
    )

    def model_dump(self, **kwargs) -> dict:  # type: ignore[override]
        kwargs.setdefault("exclude", {"script"})
        return super().model_dump(**kwargs)


class BaseRenderer(ABC):
    """Convert a SimulationPlan into OpenMC Python code at a declared capability level."""

    name: str = "base"
    supported_kinds: tuple[str, ...] = ()

    @abstractmethod
    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        """Assess this renderer's capability for ``plan`` without writing anything."""

    @abstractmethod
    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        """Write model.py (and sidecars) into ``outdir`` and return the result."""

    # -- shared helpers ---------------------------------------------------

    @staticmethod
    def renderability_rank(renderability: Renderability) -> int:
        """Higher is more capable. Used by the registry to pick the best renderer."""
        return RENDERABILITY_RANK.get(renderability, 0)


SMOKE_MAX_PARTICLES = 1000
SMOKE_MAX_BATCHES = 20


def low_cost_runnable(plan: SimulationPlan) -> bool:
    """True when the plan's execution check is enabled and within smoke-test limits."""
    if not plan.execution_check.enabled:
        return False
    settings = plan.execution_check.settings
    return settings.particles <= SMOKE_MAX_PARTICLES and settings.batches <= SMOKE_MAX_BATCHES
