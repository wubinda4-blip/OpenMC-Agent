"""Planner stub for the agent-authored renderer pipeline.

``RendererAuthoringAgent.propose_renderer`` is the single entry point the main
workflow would call if no registered renderer matches a plan. It currently
returns a ``CandidateRenderer`` whose ``status == AUTHORING_NOT_IMPLEMENTED`` so
callers can fall back to the skeleton renderer without surprises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openmc_agent.schemas import RenderCapabilityReport, SimulationPlan

AUTHORING_NOT_IMPLEMENTED = "not_implemented"
"""Status reported when autonomous renderer authoring is unavailable."""


@dataclass(frozen=True)
class SafetyConstraints:
    """Hard safety gates any auto-generated renderer must pass before registration.

    These are documented as policy here and enforced by the sibling modules
    (``validator.py`` for AST/static checks, ``sandbox.py`` for isolated
    execution). A candidate that fails any gate MUST NOT be registered.
    """

    require_ast_static_check: bool = True
    forbidden_modules: tuple[str, ...] = (
        "os",
        "subprocess",
        "eval",
        "exec",
        "requests",
        "urllib",
        "socket",
        "ctypes",
    )
    forbidden_calls: tuple[str, ...] = (
        "os.system",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "eval",
        "exec",
        "compile",
        "requests.get",
        "requests.post",
    )
    require_sandbox_execution: bool = True
    require_unit_tests: bool = True
    require_export_to_xml_test: bool = True
    require_human_approval: bool = True


@dataclass(frozen=True)
class CandidateRenderer:
    """Outcome of a renderer-authoring proposal."""

    status: str
    name: str = ""
    source_code: str = ""
    reasons: list[str] = field(default_factory=list)
    safety_constraints: SafetyConstraints = field(default_factory=SafetyConstraints)

    @property
    def implemented(self) -> bool:
        return self.status != AUTHORING_NOT_IMPLEMENTED and bool(self.source_code)


class RendererAuthoringAgent:
    """Propose new renderers for plans no registered renderer can handle.

    NOTE: fully autonomous code generation is intentionally not implemented.
    The interface exists so the main workflow can be wired to it later.
    """

    safety_constraints = SafetyConstraints()

    def propose_renderer(
        self,
        plan: SimulationPlan,
        capability_report: RenderCapabilityReport,
    ) -> CandidateRenderer:
        return CandidateRenderer(
            status=AUTHORING_NOT_IMPLEMENTED,
            reasons=[
                "Autonomous renderer authoring is not implemented.",
                "Falling back to the skeleton renderer; a human must implement and "
                "register a renderer for this IR family.",
            ],
            safety_constraints=self.safety_constraints,
        )
