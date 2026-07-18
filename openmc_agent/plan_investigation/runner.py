"""Feature flag + top-level orchestration hook for the investigation stage.

Public surface:

* :class:`PlanInvestigationConfig` — typed opt-in flag + budget override.
* :func:`get_investigation_config` — read flag from ``PlanBuildState.metadata``
  (or a fresh default).
* :func:`run_investigation_stage` — the only entry point executors should
  call.  Returns ``None`` when the flag is off (zero-impact legacy path).

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

from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .agent import (
    InvestigationAgent,
    InvestigationBudget,
    InvestigationContext,
    InvestigationResult,
)
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
    "PlanInvestigationConfig",
    "CONFIG_METADATA_KEY",
    "get_investigation_config",
    "set_investigation_config",
    "build_investigation_source_index",
    "build_investigation_ledger",
    "run_investigation_stage",
]


CONFIG_METADATA_KEY: str = "plan_investigation_config"


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class PlanInvestigationConfig(AgentBaseModel):
    """Opt-in flag + budget for the investigation stage.

    Default is OFF (``enabled=False``); the entire investigation surface
    is inert unless a caller explicitly enables it via
    :func:`set_investigation_config` or constructs a config with
    ``enabled=True`` and passes it to :func:`run_investigation_stage`
    directly.
    """

    enabled: bool = False
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    caller_stage: str = "investigation"


def get_investigation_config(
    state_or_metadata: Any,
) -> PlanInvestigationConfig:
    """Read the config from a :class:`PlanBuildState` or a metadata dict.

    Returns the default (enabled=False) when nothing is set, when the
    state has no ``metadata`` attribute, or when the metadata value is
    malformed.
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
    except Exception:
        # Malformed config must NEVER break the legacy path.
        return PlanInvestigationConfig()


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
) -> InvestigationResult | None:
    """Run the optional investigation stage.

    Returns ``None`` when the feature is disabled (the default).  When
    enabled, returns an :class:`InvestigationResult` whose
    ``evidence_claim_ids`` can be passed to the patch generator.

    The caller is responsible for:

    * Building / reusing the source index + ledger (helpers above).
    * Writing the session artifact (if any).
    * Forwarding ``result.evidence_claim_ids`` into
      :class:`PatchGenerationContext.investigation_evidence`.

    This function never mutates ``state``; the config read is read-only.
    """

    if config is None:
        config = get_investigation_config(state)
    if not config.enabled:
        return None
    if llm_client is None:
        return None  # no LLM → no investigation; silently no-op.

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
    )
    agent = InvestigationAgent(registry=registry, llm_client=llm_client)
    return agent.run(context)
