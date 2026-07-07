"""Incremental plan builder package.

Phase 0–1 provides:
* :func:`should_use_incremental_planning` — mode decision (Phase 0)
* :class:`PlanningModeDecision` — decision result model (Phase 0)
* :class:`PlanBuildState` — external state container (Phase 1)
* :func:`initialize_plan_build_state` — state initializer (Phase 1)
* :func:`create_initial_component_tasks` — shallow task skeleton (Phase 1)

Future phases will add patch schemas, validators, deterministic assembler,
LLM patch generator, and local retry router.
"""

from __future__ import annotations

from .mode import (
    PlanningModeDecision,
    should_use_incremental_planning,
)
from .state import (
    BuildEvent,
    PlanBuildState,
    PlanComponentTask,
    PlanPatchEnvelope,
    create_initial_component_tasks,
    initialize_plan_build_state,
)

__all__ = [
    "BuildEvent",
    "PlanBuildState",
    "PlanComponentTask",
    "PlanPatchEnvelope",
    "PlanningModeDecision",
    "create_initial_component_tasks",
    "initialize_plan_build_state",
    "should_use_incremental_planning",
]
