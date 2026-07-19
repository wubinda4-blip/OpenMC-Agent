"""Feature flag + top-level orchestration hook for the investigation stage.

Public surface:

* :class:`PlanInvestigationMode` — ``off`` / ``advisory`` / ``controlled``.
* :class:`PlanInvestigationConfig` — typed opt-in flag + budget override.
* :func:`get_investigation_config` — read flag from ``PlanBuildState.metadata``
  (or a fresh default).
* :func:`run_investigation_stage` — the only entry point executors should
  call.  Returns ``None`` when the mode is ``off`` (zero-impact legacy path).

Mode semantics
--------------
* ``off`` (default): zero LLM calls, zero tool calls, zero artifacts,
  legacy patch prompt byte-identical.
* ``advisory``: investigation runs but failures are non-blocking; the
  legacy Facts path continues.  The result is marked
  ``completed=False`` so callers cannot misrepresent a failed
  investigation as a successful one.
* ``controlled``: investigation failures are blocking.  Client missing,
  invalid LLM output, unknown tool, invalid arguments, budget exceeded,
  and missing source-backed evidence ALL block the run with a stable
  ``block_code``.

When enabled, the function:

1. Builds / reuses a :class:`SourceIndex` from the requirement text.
2. Builds / reuses an empty :class:`PlanningEvidenceLedger` bound to the
   requirement hash + source index.
3. Constructs an :class:`InvestigationAgent` with the supplied LLM client.
4. Runs the agent, returns an :class:`InvestigationResult`.

The function does NOT modify the supplied :class:`PlanBuildState`; the
caller decides what to do with the result (typically, populate
``PatchGenerationContext.investigation_evidence`` and write the session
artifact).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .agent import (
    InvestigationAgent,
    InvestigationBudget,
    InvestigationContext,
    InvestigationResult,
)
from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    create_empty_ledger,
)
from .hashing import content_hash
from .models import SourceKind
from .policy import InvestigationPolicyRegistry, default_policy_registry
from .source_index import build_source_index
from .tool_registry import (
    InvestigationToolRegistry,
    build_default_step2_registry,
)

__all__ = [
    "PlanInvestigationMode",
    "PlanInvestigationConfig",
    "CONFIG_METADATA_KEY",
    "BLOCK_CODE_CLIENT_UNAVAILABLE",
    "BLOCK_CODE_CONFIG_INVALID",
    "BLOCK_CODE_SOURCE_BACKED_EVIDENCE_MISSING",
    "get_investigation_config",
    "set_investigation_config",
    "build_investigation_source_index",
    "build_investigation_ledger",
    "run_investigation_stage",
]


CONFIG_METADATA_KEY: str = "plan_investigation_config"

BLOCK_CODE_CLIENT_UNAVAILABLE = "planning.investigation_client_unavailable"
BLOCK_CODE_CONFIG_INVALID = "planning.investigation_config_invalid"
BLOCK_CODE_SOURCE_BACKED_EVIDENCE_MISSING = (
    "planning.investigation_source_backed_evidence_missing"
)


class PlanInvestigationMode(str, Enum):
    """Three explicit modes for the investigation stage.

    The string values are stable across config serialization.
    """

    OFF = "off"
    ADVISORY = "advisory"
    CONTROLLED = "controlled"


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class PlanInvestigationConfig(AgentBaseModel):
    """Opt-in flag + budget + scope for the investigation stage.

    Default is OFF (``mode=PlanInvestigationMode.OFF``); the entire
    investigation surface is inert unless a caller explicitly switches
    to ``advisory`` or ``controlled``.

    Backwards compatibility: the original Step 3 ``enabled: bool`` field
    is still honoured.  ``enabled=True`` with no explicit ``mode`` maps
    to ``controlled`` (the safer default).  ``enabled=False`` (the
    default) maps to ``off``.
    """

    mode: PlanInvestigationMode = PlanInvestigationMode.OFF
    enabled: bool | None = None  # legacy compat; canonical source is `mode`
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    caller_stage: str = "investigation"

    # Step 4 scope knobs.
    patch_types: tuple[str, ...] = Field(default=("facts",))
    max_sessions_per_patch_type: int = Field(default=1, ge=1, le=8)
    reuse_cached_session: bool = True
    require_source_backed_evidence: bool = True
    # Free-form provider/audit metadata the campaign may stamp on the
    # config for fingerprinting.  Never affects execution semantics.
    investigator_model: str | None = None
    investigator_reasoning_effort: str | None = None
    investigator_output_mode: str | None = None

    @field_validator("patch_types")
    @classmethod
    def _patch_types_nonempty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise PlanInvestigationIssue(
                BLOCK_CODE_CONFIG_INVALID,
                "patch_types must list at least one patch type",
            )
        return value

    @model_validator(mode="after")
    def _resolve_legacy_enabled(self) -> "PlanInvestigationConfig":
        """Reconcile the legacy ``enabled`` field with the canonical ``mode``."""

        if self.enabled is True and self.mode == PlanInvestigationMode.OFF:
            # Legacy "enabled=True" with default mode → upgrade to controlled
            # (the safer of the two non-off modes).
            object.__setattr__(self, "mode", PlanInvestigationMode.CONTROLLED)
        elif self.enabled is False and self.mode != PlanInvestigationMode.OFF:
            # Legacy "enabled=False" overrides a non-off mode → force off.
            object.__setattr__(self, "mode", PlanInvestigationMode.OFF)
        return self

    @property
    def is_off(self) -> bool:
        return self.mode == PlanInvestigationMode.OFF

    @property
    def is_controlled(self) -> bool:
        return self.mode == PlanInvestigationMode.CONTROLLED

    @property
    def is_advisory(self) -> bool:
        return self.mode == PlanInvestigationMode.ADVISORY


def get_investigation_config(
    state_or_metadata: Any,
) -> PlanInvestigationConfig:
    """Read the config from a :class:`PlanBuildState` or a metadata dict.

    Returns the default (mode=off) when nothing is set, when the state
    has no ``metadata`` attribute, or when the metadata value is empty.

    A *present but malformed* config raises
    :class:`PlanInvestigationIssue` (``planning.investigation_config_invalid``)
    rather than silently degrading to ``off``.  Only totally absent
    configs are interpreted as "user has not opted in → off".
    """

    metadata: Mapping[str, Any] | None = None
    if state_or_metadata is None:
        return PlanInvestigationConfig()
    if hasattr(state_or_metadata, "metadata"):
        metadata = state_or_metadata.metadata
    elif isinstance(state_or_metadata, Mapping):
        metadata = state_or_metadata
    if not metadata:
        return PlanInvestigationConfig()
    raw = metadata.get(CONFIG_METADATA_KEY)
    if raw is None:
        return PlanInvestigationConfig()
    try:
        return PlanInvestigationConfig.model_validate(raw)
    except Exception as exc:
        raise PlanInvestigationIssue(
            BLOCK_CODE_CONFIG_INVALID,
            "plan_investigation_config is present but malformed",
            details={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc


def set_investigation_config(
    state: Any,
    config: PlanInvestigationConfig,
) -> None:
    """Write ``config`` into ``state.metadata[CONFIG_METADATA_KEY]``.

    The state's ``metadata`` dict must be mutable (PlanBuildState's is).
    """

    if not hasattr(state, "metadata"):
        raise TypeError("state must expose a mutable 'metadata' attribute")
    state.metadata[CONFIG_METADATA_KEY] = config.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Source index + ledger builders
# ---------------------------------------------------------------------------


def build_investigation_source_index(
    requirement_text: str,
    *,
    title: str = "requirement",
):
    """Build the canonical :class:`SourceIndex` for the requirement text."""

    return build_source_index(
        text=requirement_text,
        title=title,
        source_kind=SourceKind.USER_REQUIREMENT,
        origin_label="investigation_stage",
    )


def build_investigation_ledger(
    *,
    requirement_text: str,
    source_indexes,
) -> PlanningEvidenceLedger:
    """Build an empty ledger bound to the requirement hash + source index."""

    return create_empty_ledger(
        requirement_hash=content_hash(requirement_text),
        source_indexes=list(source_indexes.values()) if isinstance(source_indexes, dict) else list(source_indexes),
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def run_investigation_stage(
    *,
    requirement: str,
    patch_type: str,
    state: Any | None = None,
    config: PlanInvestigationConfig | None = None,
    registry: InvestigationToolRegistry | None = None,
    policy_registry: InvestigationPolicyRegistry | None = None,
    llm_client: Callable[[str], str] | None = None,
    source_indexes: dict | None = None,
    ledger: PlanningEvidenceLedger | None = None,
    accepted_facts: Any = None,
    geometry_inventory: Any = None,
    material_requirement_set: Any = None,
    universe_requirement_set: Any = None,
) -> InvestigationResult | None:
    """Run the optional investigation stage.

    Returns ``None`` when ``mode=off`` (the default).  In ``advisory`` and
    ``controlled`` modes, returns an :class:`InvestigationResult`.

    Safety rules enforced here (the top-level boundary):

    * ``mode=controlled`` + ``llm_client is None`` → returns a blocked
      :class:`InvestigationResult` with code
      ``planning.investigation_client_unavailable`` instead of silently
      returning ``None``.
    * ``mode=advisory`` + ``llm_client is None`` → returns a non-blocking
      :class:`InvestigationResult` with ``completed=False`` and a warning.

    The caller is responsible for:

    * Building / reusing the source index + ledger (helpers above).
    * Writing the session artifact (if any).
    * Forwarding ``result.evidence_claim_ids`` into
      :class:`PatchGenerationContext.investigation_evidence`.

    This function never mutates ``state``; the config read is read-only.

    Phase 8A Step 6 (P0-5 fix): ``accepted_facts``, ``geometry_inventory``,
    ``material_requirement_set`` and ``universe_requirement_set`` are now
    forwarded into :class:`InvestigationContext` so the Materials /
    Universes baseline resolver can actually use them.
    """

    if config is None:
        try:
            config = get_investigation_config(state)
        except PlanInvestigationIssue:
            # Malformed config: re-raise so the caller can surface the
            # error.  We must NOT silently degrade to off here.
            raise
    if config.is_off:
        return None

    # Controlled mode requires a real client.  Advisory tolerates the
    # absence but records a warning so the caller cannot misrepresent
    # the outcome.
    if llm_client is None:
        if config.is_controlled:
            return InvestigationResult(
                session_id=_blocked_session_id(requirement, patch_type),
                patch_type=patch_type,
                blocked=True,
                block_code=BLOCK_CODE_CLIENT_UNAVAILABLE,
                block_message=(
                    "controlled investigation mode requires a real llm_client"
                ),
                budget=config.budget,
                warnings=("controlled investigation mode requires a real llm_client",),
            )
        # Advisory: return a non-blocking result that callers can identify
        # as "did not run" via completed=False.
        return InvestigationResult(
            session_id=_blocked_session_id(requirement, patch_type),
            patch_type=patch_type,
            completed=False,
            blocked=False,
            warnings=("advisory investigation skipped: no llm_client supplied",),
            budget=config.budget,
        )

    # Build / reuse source indexes + ledger.
    if source_indexes is None:
        idx = build_investigation_source_index(requirement)
        source_indexes = {idx.document.source_id: idx}
    if ledger is None:
        ledger = build_investigation_ledger(
            requirement_text=requirement, source_indexes=source_indexes
        )

    # Resolve registry + policy.
    if registry is None:
        registry = build_default_step2_registry()
    if policy_registry is None:
        policy_registry = default_policy_registry()

    available_tools = tuple(registry.list_tools())
    existing_evidence = tuple(ledger.claims.values())
    policy_suggestions = tuple(policy_registry.suggestions_for(patch_type))

    context = InvestigationContext(
        requirement_text=requirement,
        patch_type=patch_type,
        available_tools=available_tools,
        existing_evidence=existing_evidence,
        budget=config.budget,
        source_indexes=dict(source_indexes),
        ledger=ledger,
        policy_suggestions=policy_suggestions,
        caller_stage=config.caller_stage,
        accepted_facts=accepted_facts,
        geometry_inventory=geometry_inventory,
        material_requirement_set=material_requirement_set,
        universe_requirement_set=universe_requirement_set,
    )
    agent = InvestigationAgent(registry=registry, llm_client=llm_client)
    result = agent.run(context)

    # Controlled post-check: require source-backed evidence.
    if config.is_controlled and config.require_source_backed_evidence and result.completed:
        if not _has_source_backed_evidence(ledger, result.evidence_claim_ids):
            return result.model_copy(
                update={
                    "blocked": True,
                    "completed": False,
                    "block_code": BLOCK_CODE_SOURCE_BACKED_EVIDENCE_MISSING,
                    "block_message": (
                        "controlled investigation requires at least one "
                        "source-backed EvidenceClaim; the LLM produced none"
                    ),
                }
            )
    return result


def _blocked_session_id(requirement: str, patch_type: str) -> str:
    from .hashing import short_id

    return short_id(
        "inv",
        {
            "patch_type": patch_type,
            "requirement_hash": content_hash(requirement),
            "blocked": True,
        },
    )


def _has_source_backed_evidence(
    ledger: PlanningEvidenceLedger,
    claim_ids: tuple[str, ...] | list[str],
) -> bool:
    for claim_id in claim_ids:
        claim = ledger.claims.get(claim_id)
        if claim is None:
            continue
        if claim.source_refs:
            return True
    return False
