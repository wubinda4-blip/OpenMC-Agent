"""Code-generation stub for agent-authored renderers.

Eventually this module would turn a renderer plan into Python source. Today it
only documents the contract and refuses to emit code so nothing can accidentally
execute an LLM-generated renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from openmc_agent.renderer_authoring.planner import SafetyConstraints


@dataclass(frozen=True)
class GeneratedSource:
    source_code: str
    warnings: tuple[str, ...] = ()


def generate_renderer_source(
    plan_digest: str,
    *,
    constraints: SafetyConstraints,
) -> GeneratedSource:
    """Return an empty GeneratedSource; autonomous codegen is not implemented."""
    return GeneratedSource(
        source_code="",
        warnings=(
            "Renderer code generation is not implemented. No source is produced.",
            f"plan_digest={plan_digest}",
            f"forbidden_calls={constraints.forbidden_calls}",
        ),
    )
