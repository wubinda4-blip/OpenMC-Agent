"""Backwards-compatible access helpers for plan-investigation state slots.

PlanBuildState gained three optional fields in Step 1:

* ``planning_source_manifest: dict | None``
* ``planning_evidence_ledger: dict | None``
* ``plan_investigation_schema_version: str | None``

These slots are deliberately inert in Step 1: nothing in the existing
LangGraph topology, gate lifecycle, or patch generator reads or writes them.
They exist so that a later step can populate them from
:mod:`openmc_agent.plan_investigation` outputs without another state-schema
migration.

Central access helpers prevent stringly-typed keys from leaking across the
codebase.  Callers MUST go through these helpers rather than hand-writing
``state.metadata["plan_investigation_..."]`` keys.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from . import PLAN_INVESTIGATION_SCHEMA_VERSION

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openmc_agent.plan_builder.state import PlanBuildState

__all__ = [
    "get_planning_source_manifest",
    "set_planning_source_manifest",
    "get_planning_evidence_ledger",
    "set_planning_evidence_ledger",
    "get_plan_investigation_schema_version",
    "mark_plan_investigation_schema_version",
    "has_plan_investigation_state",
    "PLAN_INVESTIGATION_SCHEMA_VERSION",
]


def get_planning_source_manifest(state: "PlanBuildState") -> dict[str, Any] | None:
    return getattr(state, "planning_source_manifest", None)


def set_planning_source_manifest(state: "PlanBuildState", manifest: dict[str, Any] | None) -> None:
    if manifest is not None and not isinstance(manifest, dict):
        raise TypeError("planning_source_manifest must be a dict or None")
    # Use object.__setattr__ to bypass any frozen-model restrictions; the
    # existing PlanBuildState is mutable so this is a no-op there.
    state.planning_source_manifest = manifest  # type: ignore[attr-defined]


def get_planning_evidence_ledger(state: "PlanBuildState") -> dict[str, Any] | None:
    return getattr(state, "planning_evidence_ledger", None)


def set_planning_evidence_ledger(state: "PlanBuildState", ledger: dict[str, Any] | None) -> None:
    if ledger is not None and not isinstance(ledger, dict):
        raise TypeError("planning_evidence_ledger must be a dict or None")
    state.planning_evidence_ledger = ledger  # type: ignore[attr-defined]


def get_plan_investigation_schema_version(state: "PlanBuildState") -> str | None:
    return getattr(state, "plan_investigation_schema_version", None)


def mark_plan_investigation_schema_version(state: "PlanBuildState") -> None:
    state.plan_investigation_schema_version = PLAN_INVESTIGATION_SCHEMA_VERSION  # type: ignore[attr-defined]


def has_plan_investigation_state(state: "PlanBuildState") -> bool:
    return (
        getattr(state, "planning_source_manifest", None) is not None
        or getattr(state, "planning_evidence_ledger", None) is not None
        or getattr(state, "plan_investigation_schema_version", None) is not None
    )
