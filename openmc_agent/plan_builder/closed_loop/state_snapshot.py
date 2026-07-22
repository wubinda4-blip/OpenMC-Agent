"""Sanitization + boundary checkpoint callback for accepted plan states.

Phase 8C Step 3B moves production campaign checkpoint persistence from a
single final artifact-only write to **accepted boundaries**.  This module
provides:

* :func:`sanitize_plan_build_state` — strips raw prompts, reasoning text,
  un-normalized LLM outputs and any sensitive keys from a
  :class:`~openmc_agent.plan_builder.state.PlanBuildState` snapshot.
* :func:`make_boundary_checkpoint_callback` — a backward-compatible
  callable threaded through ``build_plan_graph`` → incremental generation
  → ``run_incremental_planning``.  It persists a full sanitized snapshot
  atomically at the accepted boundaries.

Design rules (from AGENTS.md safety boundaries):

* Never persist prompts, reasoning content or raw LLM responses.
* Snapshots are append-only and atomic (``CampaignCheckpointStore``).
* The callback is a no-op when no store is supplied (legacy path).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from openmc_agent.structured_output import canonical_payload_hash

from .campaign_checkpoint import (
    ACCEPTED_BOUNDARIES,
    BOUNDARY_GATE_FACTS,
    BOUNDARY_GATE_MATERIAL_UNIVERSE,
    BOUNDARY_GATE_PLACEMENT,
    BOUNDARY_GATE_AXIAL_GEOMETRY,
    BOUNDARY_GATE_ASSEMBLED_PLAN,
    BOUNDARY_PATCH_MATERIALS,
    BOUNDARY_PATCH_UNIVERSES,
    CampaignCheckpointStore,
    CampaignStateSnapshot,
    GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION,
)

__all__ = [
    "BoundaryCheckpointCallback",
    "FactsActionCallback",
    "sanitize_plan_build_state",
    "make_boundary_checkpoint_callback",
    "make_facts_action_callback",
    "ACCEPTED_BOUNDARIES",
]

# Sensitive key fragments that must never appear in a persisted snapshot.
# Matching is substring-based against the full dotted key path so nested
# occurrences are also stripped.
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "raw_text",
    "raw_output",
    "prompt_text",
    "prompt",
    "reasoning",
    "reasoning_content",
    "api_key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
)


# Type alias: (boundary_id, state) -> None.  ``state`` is a live
# PlanBuildState; the callback sanitizes it before persisting.
BoundaryCheckpointCallback = Callable[[str, Any], None]

# Type alias for Facts action-level callbacks (Task 2).
FactsActionCallback = Callable[..., None]


def _dotted_keys(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a nested JSON-ish structure into ``(dotted_key, leaf)`` pairs."""

    out: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_dotted_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            out.extend(_dotted_keys(item, path))
    else:
        out.append((prefix, value))
    return out


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


def _sanitize_node(value: Any) -> Any:
    """Recursively strip sensitive keys from a JSON-compatible structure."""

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                continue
            cleaned[str(key)] = _sanitize_node(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_node(item) for item in value]
    return value


def sanitize_plan_build_state(state: Any) -> dict[str, Any]:
    """Return a sanitized JSON dict of a PlanBuildState.

    Strips raw prompts, reasoning text, raw LLM outputs and any key whose
    name matches a sensitive fragment.  The result is safe to persist in a
    checkpoint / replay bundle.
    """

    if hasattr(state, "model_dump"):
        payload = state.model_dump(mode="json")
    elif isinstance(state, Mapping):
        payload = dict(state)
    else:
        payload = {"value": state}
    return _sanitize_node(payload)


def _boundary_snapshot(
    *,
    store: CampaignCheckpointStore,
    campaign_id: str,
    boundary: str,
    state: Any,
    sequence: int,
    fingerprints: Mapping[str, str],
) -> CampaignStateSnapshot:
    sanitized = sanitize_plan_build_state(state)
    return CampaignStateSnapshot(
        campaign_id=campaign_id,
        boundary=boundary,
        schema_version=GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION,
        sequence=sequence,
        state_hash=canonical_payload_hash(sanitized),
        plan_build_state=sanitized,
        requirement_hash=fingerprints.get("requirement_hash", ""),
        input_hash=fingerprints.get("input_hash", ""),
        policy_hash=fingerprints.get("policy_hash", ""),
        git_sha=fingerprints.get("git_sha", ""),
        structured_output_policy_hash=fingerprints.get("structured_output_policy_hash", ""),
        accepted_at=datetime.now(timezone.utc).isoformat(),
    )


def make_boundary_checkpoint_callback(
    store: CampaignCheckpointStore,
    *,
    campaign_id: str,
    fingerprints: Mapping[str, str] | None = None,
) -> BoundaryCheckpointCallback:
    """Build a callback that persists sanitized snapshots at accepted boundaries.

    The returned callable accepts ``(boundary, state)`` and writes a full
    sanitized snapshot atomically.  Unknown boundaries are ignored.  Store
    errors propagate: an accepted boundary is not durable unless persistence
    succeeds.
    """

    finger = dict(fingerprints or {})
    counter = {"seq": 0}

    def _callback(boundary: str, state: Any) -> None:
        if boundary not in ACCEPTED_BOUNDARIES:
            return
        counter["seq"] += 1
        snapshot = _boundary_snapshot(
            store=store,
            campaign_id=campaign_id,
            boundary=boundary,
            state=state,
            sequence=counter["seq"],
            fingerprints=finger,
        )
        store.accept_state_snapshot(snapshot)

    return _callback


def make_facts_action_callback(
    store: CampaignCheckpointStore,
) -> FactsActionCallback:
    """Build a callback that records Facts action checkpoints.

    Records hashes/status/billing/deadline only — never prompts, reasoning
    or raw responses.  Used by the investigation runner/executor only when
    a campaign output supplies a store.
    """

    from .campaign_checkpoint import FactsActionCheckpoint

    def _callback(
        *,
        action_id: str,
        patch_type: str = "",
        tool_name: str,
        arguments_hash: str,
        status: str = "pending",
        billed_call_count: int = 0,
        provider_deadline: str = "",
        unfinished: bool = True,
        context_hash: str = "",
        campaign_fingerprints: Mapping[str, str] | None = None,
        normalized_progress: Mapping[str, Any] | None = None,
    ) -> None:
        store.record_facts_action(
            FactsActionCheckpoint(
                action_id=action_id,
                patch_type=patch_type,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                status=status,
                billed_call_count=billed_call_count,
                provider_deadline=provider_deadline,
                unfinished=unfinished,
                context_hash=context_hash,
                campaign_fingerprints=dict(campaign_fingerprints or {}),
                normalized_progress=dict(normalized_progress or {}),
            )
        )

    def _restore_action(
        *,
        action_id: str,
        patch_type: str,
        tool_name: str,
        arguments_hash: str,
        context_hash: str,
        campaign_fingerprints: Mapping[str, str],
    ) -> dict[str, Any] | None:
        checkpoint = store.restore_facts_action(
            action_id,
            patch_type=patch_type,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            context_hash=context_hash,
            campaign_fingerprints=campaign_fingerprints,
        )
        return dict(checkpoint.normalized_progress) if checkpoint is not None else None

    # Preserve the existing callable injection API while giving the agent a
    # narrowly-scoped restore hook.  The hook returns sanitized normalized
    # progress only; it never exposes prompts or provider raw output.
    setattr(_callback, "restore_action", _restore_action)
    return _callback
