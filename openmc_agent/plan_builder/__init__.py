"""Incremental plan builder package.

Phase 0–2 provides:
* :func:`should_use_incremental_planning` — mode decision (Phase 0)
* :class:`PlanningModeDecision` — decision result model (Phase 0)
* :class:`PlanBuildState` — external state container (Phase 1)
* :func:`initialize_plan_build_state` — state initializer (Phase 1)
* :func:`create_initial_component_tasks` — shallow task skeleton (Phase 1)
* Patch schemas (Phase 2): :class:`FactsPatch`, :class:`MaterialsPatch`,
  :class:`UniversesPatch`, :class:`PinMapPatch`, :class:`AxialLayersPatch`,
  :class:`AxialOverlaysPatch`, :class:`SettingsPatch`
* :func:`parse_patch_content` — patch parsing dispatcher (Phase 2)
* :func:`validate_patch` — per-patch validator (Phase 2)
* :func:`add_validated_patch_to_state` — state integration helper (Phase 2)

Future phases will add deterministic assembler, LLM patch generator,
and local retry router.
"""

from __future__ import annotations

from .mode import (
    PlanningModeDecision,
    should_use_incremental_planning,
)
from .patches import (
    AxialLayerPatchItem,
    AxialLayersPatch,
    AxialOverlayPatchItem,
    AxialOverlaysPatch,
    CellLayerPatch,
    CoordinateConvention,
    FactsPatch,
    MaterialSpecPatch,
    MaterialsPatch,
    PatchParseError,
    PatchType,
    PinMapPatch,
    SettingsPatch,
    UniverseSpecPatch,
    UniversesPatch,
    normalized_coords,
    parse_patch_content,
    parse_patch_envelope,
)
from .state import (
    BuildEvent,
    PlanBuildState,
    PlanComponentTask,
    PlanPatchEnvelope,
    add_validated_patch_to_state,
    create_initial_component_tasks,
    initialize_plan_build_state,
)
from .validators import (
    PatchValidationContext,
    PatchValidationIssue,
    PatchValidationResult,
    validate_patch,
)

__all__ = [
    # Phase 0
    "PlanningModeDecision",
    "should_use_incremental_planning",
    # Phase 1
    "BuildEvent",
    "PlanBuildState",
    "PlanComponentTask",
    "PlanPatchEnvelope",
    "create_initial_component_tasks",
    "initialize_plan_build_state",
    # Phase 2 -- patches
    "AxialLayerPatchItem",
    "AxialLayersPatch",
    "AxialOverlayPatchItem",
    "AxialOverlaysPatch",
    "CellLayerPatch",
    "CoordinateConvention",
    "FactsPatch",
    "MaterialSpecPatch",
    "MaterialsPatch",
    "PatchParseError",
    "PatchType",
    "PinMapPatch",
    "SettingsPatch",
    "UniverseSpecPatch",
    "UniversesPatch",
    "normalized_coords",
    "parse_patch_content",
    "parse_patch_envelope",
    # Phase 2 -- validators
    "PatchValidationContext",
    "PatchValidationIssue",
    "PatchValidationResult",
    "add_validated_patch_to_state",
    "validate_patch",
]
