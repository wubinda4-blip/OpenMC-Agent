"""Plan build state for incremental (patch-based) plan generation (Phase 1).

Provides the external state container that future patch-based planning phases
(Phase 2+) will use to track component tasks, patch envelopes, assembled plan,
validation issues, and a structured build log.

Design constraints
------------------
* **Pure data.**  No OpenMC, no renderer, no LLM dependencies.
* **JSON serializable.**  Can be written to transcript / trace / debug files.
* **Future-friendly.**  Helpers (``add_event``, ``add_task``, ``add_patch``,
  ``mark_patch_status``, ``get_valid_patches``, ``to_summary``) support the
  patch generation → assembly → validation cycle that later phases will add.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .mode import PlanningModeDecision


# ---------------------------------------------------------------------------
# Stable event / issue codes
# ---------------------------------------------------------------------------

EVENT_PLANNING_MODE_SELECTED: str = "planning.incremental_mode_selected"
EVENT_MONOLITHIC_MODE_SELECTED: str = "planning.monolithic_mode_selected"
EVENT_INCREMENTAL_RECOMMENDED_NOT_EXECUTED: str = "planning.incremental_recommended_but_not_executed"
EVENT_INCREMENTAL_EXECUTOR_NOT_IMPLEMENTED: str = "planning.incremental_executor_not_implemented"
EVENT_BUILD_STATE_INITIALIZED: str = "planning.build_state_initialized"
EVENT_COMPONENT_TASKS_INITIALIZED: str = "planning.component_tasks_initialized"
EVENT_PATCH_PARSED: str = "planning.patch_parsed"
EVENT_PATCH_VALIDATED: str = "planning.patch_validated"
EVENT_PATCH_INVALID: str = "planning.patch_invalid"


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------


class BuildEvent(AgentBaseModel):
    """A single event in the plan build log."""

    timestamp: str | None = None
    event_type: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class PlanComponentTask(AgentBaseModel):
    """A pending task for building a plan component (materials, universes, ...).

    In Phase 1 these are shallow placeholders.  Phase 2+ will flesh out the
    decomposer that populates and executes them.
    """

    task_id: str
    patch_type: str
    description: str = ""
    status: Literal["pending", "running", "valid", "invalid", "skipped", "blocked"] = (
        "pending"
    )
    dependencies: list[str] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


class PlanPatchEnvelope(AgentBaseModel):
    """A single patch envelope produced by an LLM, deterministic code, or user.

    Phase 1 only stores patches; Phase 2+ will define patch schemas, validators,
    and the assembler that merges valid patches into ``assembled_plan``.
    """

    patch_id: str
    patch_type: str
    schema_version: str = "v1"
    content: dict[str, Any] = Field(default_factory=dict)
    source: Literal["llm", "deterministic", "user", "fixture", "repair"] = "llm"
    status: Literal["pending", "valid", "invalid", "repaired", "skipped"] = "pending"
    issues: list[dict[str, Any]] = Field(default_factory=list)
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanBuildState(AgentBaseModel):
    """External state container for incremental plan building.

    Holds the requirement, detected features, component task list, patches,
    assembled plan, validation issues, and a structured build log.  Designed
    to be JSON-serialized into transcript / trace / debug reports without
    depending on OpenMC or any renderer.
    """

    state_id: str
    requirement_text: str
    planning_mode: Literal["incremental"] = "incremental"

    benchmark_id: str | None = None
    selected_variant: str | None = None

    extracted_facts: dict[str, Any] = Field(default_factory=dict)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)

    component_tasks: list[PlanComponentTask] = Field(default_factory=list)
    patches: dict[str, PlanPatchEnvelope] = Field(default_factory=dict)
    patch_status: dict[str, str] = Field(default_factory=dict)

    assembled_plan: dict[str, Any] | None = None
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    build_log: list[BuildEvent] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def add_event(
        self,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append a build event to ``build_log``."""
        self.build_log.append(
            BuildEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=event_type,
                message=message,
                data=dict(data or {}),
            )
        )

    def add_task(self, task: PlanComponentTask) -> None:
        """Add or replace a component task by ``task_id``."""
        existing = next(
            (t for t in self.component_tasks if t.task_id == task.task_id), None
        )
        if existing is not None:
            idx = self.component_tasks.index(existing)
            self.component_tasks[idx] = task
        else:
            self.component_tasks.append(task)

    def add_patch(self, patch: PlanPatchEnvelope) -> None:
        """Add a patch envelope and initialize its status tracking."""
        self.patches[patch.patch_id] = patch
        self.patch_status[patch.patch_id] = patch.status

    def mark_patch_status(
        self,
        patch_id: str,
        status: str,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update a patch's status (and optionally its issues list)."""
        if patch_id not in self.patches:
            return
        patch = self.patches[patch_id]
        # Use model_validate to update; Pydantic v2 doesn't allow direct field
        # assignment on frozen models, but AgentBaseModel is not frozen.
        patch.status = status  # type: ignore[assignment]
        self.patch_status[patch_id] = status
        if issues is not None:
            patch.issues = issues

    def get_valid_patches(
        self,
        patch_type: str | None = None,
    ) -> list[PlanPatchEnvelope]:
        """Return all patches with ``status='valid'``, optionally filtered by type."""
        result = [p for p in self.patches.values() if p.status == "valid"]
        if patch_type is not None:
            result = [p for p in result if p.patch_type == patch_type]
        return result

    def to_summary(self) -> dict[str, Any]:
        """Return a compact summary suitable for transcript / debug output."""
        return {
            "state_id": self.state_id,
            "planning_mode": self.planning_mode,
            "benchmark_id": self.benchmark_id,
            "selected_variant": self.selected_variant,
            "task_count": len(self.component_tasks),
            "task_statuses": {
                s: sum(1 for t in self.component_tasks if t.status == s)
                for s in ("pending", "running", "valid", "invalid", "skipped", "blocked")
            },
            "tasks": [
                {
                    "task_id": t.task_id,
                    "patch_type": t.patch_type,
                    "status": t.status,
                }
                for t in self.component_tasks
            ],
            "patch_count": len(self.patches),
            "valid_patch_count": len(self.get_valid_patches()),
            "has_assembled_plan": self.assembled_plan is not None,
            "validation_issue_count": len(self.validation_issues),
            "build_log_events": len(self.build_log),
        }


# ---------------------------------------------------------------------------
# Initialization helpers
# ---------------------------------------------------------------------------


def _generate_state_id() -> str:
    """Generate a stable state id."""
    from uuid import uuid4

    return f"pbs_{uuid4().hex[:16]}"


def initialize_plan_build_state(
    requirement: str,
    decision: PlanningModeDecision,
    confirmed_facts: dict[str, Any] | None = None,
    benchmark_id: str | None = None,
    selected_variant: str | None = None,
) -> PlanBuildState:
    """Initialize a :class:`PlanBuildState` from a planning mode decision.

    Records ``planning_mode_selected`` and ``build_state_initialized`` events.
    If the decision is monolithic, the state is still created (for debugging)
    but the mode is noted in metadata.

    Parameters
    ----------
    requirement
        The original requirement text.
    decision
        The :class:`PlanningModeDecision` from
        :func:`~openmc_agent.plan_builder.mode.should_use_incremental_planning`.
    confirmed_facts
        Optional pre-confirmed facts (e.g. from expert feedback).
    benchmark_id
        Optional benchmark identifier (e.g. ``"VERA3"``).
    selected_variant
        Optional variant identifier (e.g. ``"3B"``).

    Returns
    -------
    PlanBuildState
        The initialized build state.
    """
    state = PlanBuildState(
        state_id=_generate_state_id(),
        requirement_text=requirement,
        planning_mode="incremental",
        benchmark_id=benchmark_id,
        selected_variant=selected_variant,
        confirmed_facts=dict(confirmed_facts or {}),
        metadata={
            "planning_mode_decision": decision.model_dump(mode="json"),
        },
    )

    mode_event_type = (
        EVENT_PLANNING_MODE_SELECTED
        if decision.mode == "incremental"
        else EVENT_MONOLITHIC_MODE_SELECTED
    )
    state.add_event(
        event_type=mode_event_type,
        message=f"planning mode: {decision.mode}",
        data={
            "mode": decision.mode,
            "triggers": decision.triggers,
            "reasons": decision.reasons,
            "confidence": decision.confidence,
        },
    )
    state.add_event(
        event_type=EVENT_BUILD_STATE_INITIALIZED,
        message=f"plan build state initialized (state_id={state.state_id})",
        data={
            "benchmark_id": benchmark_id,
            "selected_variant": selected_variant,
            "feature_summary": decision.feature_summary,
        },
    )

    # Initialize shallow component tasks based on feature summary.
    tasks = create_initial_component_tasks(decision.feature_summary)
    for task in tasks:
        state.add_task(task)
    if tasks:
        state.add_event(
            event_type=EVENT_COMPONENT_TASKS_INITIALIZED,
            message=f"initialized {len(tasks)} component tasks",
            data={"task_ids": [t.task_id for t in tasks]},
        )

    return state


# ---------------------------------------------------------------------------
# Shallow component-task skeleton
# ---------------------------------------------------------------------------

# Task type ordering for a complex assembly.  ``facts`` and ``materials`` come
# first; ``settings`` is last and can be deterministic.
_DEFAULT_TASK_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("facts", "Extract and confirm modeling facts from requirement"),
    ("materials", "Define materials (fuel, coolant, cladding, structural)"),
    ("universes", "Define universes (pin cells, guide tubes, special pins)"),
    ("pin_map", "Build lattice pin map with universe assignments"),
    ("axial_layers", "Define core.axial_layers (z-segmentation)"),
    ("axial_overlays", "Define core.axial_overlays (spacer grids, bands)"),
    ("settings", "Define execution settings (batches, particles, source)"),
)

# Dependencies between task types.
_DEFAULT_DEPENDENCIES: dict[str, list[str]] = {
    "materials": ["task_facts"],
    "universes": ["task_materials"],
    "pin_map": ["task_universes"],
    "axial_layers": ["task_facts"],
    "axial_overlays": ["task_axial_layers"],
    "settings": [],
}


def create_initial_component_tasks(
    feature_summary: dict[str, Any],
) -> list[PlanComponentTask]:
    """Create a shallow component-task skeleton from detected features.

    Parameters
    ----------
    feature_summary
        The ``feature_summary`` dict from a :class:`PlanningModeDecision`.

    Returns
    -------
    list[PlanComponentTask]
        Ordered list of pending tasks.  May be empty for a trivial feature set.
    """
    has_axial = bool(feature_summary.get("has_axial_geometry"))
    has_spacer = bool(feature_summary.get("has_spacer_grid"))
    has_special_pin = bool(feature_summary.get("has_special_pin_map"))
    has_large_lattice = feature_summary.get("large_lattice_dimension") is not None
    has_variants = bool(feature_summary.get("has_benchmark_variant"))

    # If nothing complex is detected, return an empty task list.
    if not any([has_axial, has_spacer, has_special_pin, has_large_lattice, has_variants]):
        return []

    tasks: list[PlanComponentTask] = []
    for patch_type, description in _DEFAULT_TASK_SEQUENCE:
        # Skip axial_layers if no axial geometry detected.
        if patch_type == "axial_layers" and not has_axial:
            continue
        # Skip axial_overlays if no spacer grid and no axial geometry.
        if patch_type == "axial_overlays" and not (has_spacer or has_axial):
            continue
        # Skip pin_map if no special pin map.
        if patch_type == "pin_map" and not (has_special_pin or has_large_lattice):
            continue

        tasks.append(
            PlanComponentTask(
                task_id=f"task_{patch_type}",
                patch_type=patch_type,
                description=description,
                status="pending",
                dependencies=list(_DEFAULT_DEPENDENCIES.get(patch_type, [])),
            )
        )

    return tasks


# ---------------------------------------------------------------------------
# Patch integration helpers (Phase 2)
# ---------------------------------------------------------------------------


def add_validated_patch_to_state(
    state: PlanBuildState,
    envelope: PlanPatchEnvelope,
    parsed_patch: Any,
    validation: Any,
) -> PlanBuildState:
    """Integrate a validated patch into a :class:`PlanBuildState`.

    Parameters
    ----------
    state
        The build state to update (mutated in place and returned).
    envelope
        The :class:`PlanPatchEnvelope` wrapping the patch content.
    parsed_patch
        The parsed patch model (from :func:`parse_patch_content`).
    validation
        The :class:`~openmc_agent.plan_builder.validators.PatchValidationResult`.

    Returns
    -------
    PlanBuildState
        The updated state (same object, for chaining).
    """
    patch_type = getattr(parsed_patch, "patch_type", envelope.patch_type)
    state.add_event(
        event_type=EVENT_PATCH_PARSED,
        message=f"patch {envelope.patch_id!r} parsed as {patch_type}",
        data={"patch_id": envelope.patch_id, "patch_type": patch_type},
    )

    issue_dicts = [issue.model_dump(mode="json") for issue in validation.issues]
    envelope.issues = issue_dicts

    if validation.ok:
        envelope.status = "valid"  # type: ignore[assignment]
        state.add_patch(envelope)
        state.add_event(
            event_type=EVENT_PATCH_VALIDATED,
            message=f"patch {envelope.patch_id!r} validated ({patch_type})",
            data={
                "patch_id": envelope.patch_id,
                "patch_type": patch_type,
                "issue_count": len(validation.issues),
                "summary": validation.summary,
            },
        )
    else:
        envelope.status = "invalid"  # type: ignore[assignment]
        state.add_patch(envelope)
        error_codes = [
            i.code for i in validation.issues if i.severity == "error"
        ]
        state.add_event(
            event_type=EVENT_PATCH_INVALID,
            message=f"patch {envelope.patch_id!r} invalid ({patch_type}): {error_codes}",
            data={
                "patch_id": envelope.patch_id,
                "patch_type": patch_type,
                "error_codes": error_codes,
                "summary": validation.summary,
            },
        )

    return state


__all__ = [
    "BuildEvent",
    "PlanBuildState",
    "PlanComponentTask",
    "PlanPatchEnvelope",
    "add_validated_patch_to_state",
    "initialize_plan_build_state",
    "create_initial_component_tasks",
    "EVENT_PLANNING_MODE_SELECTED",
    "EVENT_MONOLITHIC_MODE_SELECTED",
    "EVENT_INCREMENTAL_RECOMMENDED_NOT_EXECUTED",
    "EVENT_INCREMENTAL_EXECUTOR_NOT_IMPLEMENTED",
    "EVENT_BUILD_STATE_INITIALIZED",
    "EVENT_COMPONENT_TASKS_INITIALIZED",
    "EVENT_PATCH_PARSED",
    "EVENT_PATCH_VALIDATED",
    "EVENT_PATCH_INVALID",
]
