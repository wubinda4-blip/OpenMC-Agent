"""Production injection point: wire investigation into incremental planning.

This module is the single integration surface between the Phase 8A
investigation layer and the incremental patch executor.  It owns:

* Shared :class:`SourceIndex` + :class:`PlanningEvidenceLedger` lifecycle
  (persisted in :class:`PlanBuildState` via the Step 1 state_compat slots).
* Per-patch-type session cache so JSON-format retries / schema retries /
  resume do not repeat the investigation.
* The controlled-mode barrier: when ``mode=controlled`` and the
  investigation blocks, the caller must NOT invoke the Facts patch LLM.

The orchestrator never mutates the supplied :class:`PlanBuildState`
directly; it returns a structured :class:`InvestigationStageOutcome` and
the caller decides which fields to persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from openmc_agent.schemas import AgentBaseModel
from pydantic import Field

from .agent import (
    InvestigationResult,
    collect_evidence_for_patch_prompt,
)
from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    create_empty_ledger,
    recompute_ledger_hash,
)
from .hashing import content_hash, short_id
from .models import SourceKind
from .policy import InvestigationPolicyRegistry, default_policy_registry
from .runner import (
    BLOCK_CODE_CLIENT_UNAVAILABLE,
    BLOCK_CODE_CONFIG_INVALID,
    BLOCK_CODE_SOURCE_BACKED_EVIDENCE_MISSING,
    PlanInvestigationConfig,
    PlanInvestigationMode,
    build_investigation_ledger,
    build_investigation_source_index,
    run_investigation_stage,
)
from .session_artifacts import (
    SESSION_ARTIFACT_RELPATH,
    write_investigation_session_artifact,
)
from .source_index import SourceIndex, build_source_index
from .tool_registry import (
    InvestigationToolRegistry,
    build_default_step2_registry,
)

__all__ = [
    "InvestigationStageOutcome",
    "InvestigationStageBlocked",
    "FactsInvestigationCoverage",
    "SessionCacheKey",
    "SessionCacheEntry",
    "InvestigationSessionCache",
    "run_facts_investigation_stage",
    "inject_investigation_evidence_into_context",
    "BLOCK_CODE_FACTS_BLOCKED",
    "EVENT_INVESTIGATION_STARTED",
    "EVENT_INVESTIGATION_COMPLETED",
    "EVENT_INVESTIGATION_BLOCKED",
    "EVENT_INVESTIGATION_CACHE_REUSED",
    "EVENT_INVESTIGATION_EVIDENCE_INJECTED",
    "EVENT_INVESTIGATION_WARNING",
]


# ---------------------------------------------------------------------------
# Stable event codes (recorded on PlanBuildState.build_log)
# ---------------------------------------------------------------------------


EVENT_INVESTIGATION_STARTED = "planning.investigation_started"
EVENT_INVESTIGATION_COMPLETED = "planning.investigation_completed"
EVENT_INVESTIGATION_BLOCKED = "planning.investigation_blocked"
EVENT_INVESTIGATION_CACHE_REUSED = "planning.investigation_cache_reused"
EVENT_INVESTIGATION_EVIDENCE_INJECTED = "planning.investigation_evidence_injected"
EVENT_INVESTIGATION_WARNING = "planning.investigation_warning"

BLOCK_CODE_FACTS_BLOCKED = "planning.investigation_facts_blocked"


# ---------------------------------------------------------------------------
# Outcome models
# ---------------------------------------------------------------------------


class InvestigationStageOutcome(AgentBaseModel):
    """Structured outcome of :func:`run_facts_investigation_stage`.

    ``evidence_payloads`` is the list of claim payloads ready to be
    injected into :class:`PatchGenerationContext.investigation_evidence`.
    Empty when the investigation did not complete or produced no claims.

    ``blocked=True`` means the caller MUST NOT invoke the Facts patch
    LLM; the investigation failed in controlled mode and the run has to
    surface ``BLOCKED_BY_INVESTIGATION:facts`` instead.
    """

    mode: PlanInvestigationMode
    patch_type: str
    completed: bool = False
    blocked: bool = False
    block_code: str | None = None
    block_message: str | None = None
    session_id: str | None = None
    evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_payloads: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    evidence_context_hash: str = ""
    ledger_hash: str = ""
    source_index_hash: str = ""
    cache_reused: bool = False
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    coverage: dict[str, Any] = Field(default_factory=dict)
    result_hash: str = ""

    @property
    def should_block_facts_patch(self) -> bool:
        """True when the caller MUST NOT call the Facts patch LLM."""

        return self.blocked


class InvestigationStageBlocked(Exception):
    """Raised when controlled-mode investigation blocks Facts generation.

    Carries the structured :class:`InvestigationStageOutcome` so the
    caller can persist telemetry and stop the run cleanly.
    """

    def __init__(self, outcome: InvestigationStageOutcome) -> None:
        super().__init__(
            f"investigation blocked Facts patch: code={outcome.block_code} "
            f"message={outcome.block_message}"
        )
        self.outcome = outcome


# ---------------------------------------------------------------------------
# Facts investigation coverage validator
# ---------------------------------------------------------------------------


class FactsInvestigationCoverage(AgentBaseModel):
    """Reactor-neutral coverage summary for a Facts investigation.

    Used by the controlled-mode barrier to ensure the investigation did
    real work (not just an empty action list) before the Facts patch LLM
    is invoked.
    """

    requirement_structure_inspected: bool = False
    patch_schema_inspected: bool = False
    source_search_executed: bool = False
    source_backed_claim_count: int = 0
    source_span_count: int = 0
    scope_indicator_claim_count: int = 0
    grid_or_layout_indicator_count: int = 0
    assembly_indicator_count: int = 0
    unresolved_target_count: int = 0

    def from_result(
        self, result: InvestigationResult, ledger: PlanningEvidenceLedger
    ) -> "FactsInvestigationCoverage":
        """Populate coverage fields from ``result`` + ``ledger``."""

        tool_names = {tc.tool_name for tc in result.tool_calls}
        object.__setattr__(
            self,
            "requirement_structure_inspected",
            "inspect_requirement_structure" in tool_names,
        )
        object.__setattr__(
            self,
            "patch_schema_inspected",
            "inspect_patch_schema" in tool_names,
        )
        object.__setattr__(
            self,
            "source_search_executed",
            "search_source_index" in tool_names,
        )

        scope_indicators = 0
        grid_indicators = 0
        assembly_indicators = 0
        source_backed = 0
        span_count = 0
        for claim_id in result.evidence_claim_ids:
            claim = ledger.claims.get(claim_id)
            if claim is None:
                continue
            if claim.source_refs:
                source_backed += 1
                span_count += len(claim.source_refs)
            if claim.predicate == "scope_indicator_present":
                scope_indicators += 1
                if claim.value == "full_core":
                    object.__setattr__(self, "grid_or_layout_indicator_count",
                                       self.grid_or_layout_indicator_count + 1)
                if claim.value == "assembly":
                    object.__setattr__(self, "assembly_indicator_count",
                                       self.assembly_indicator_count + 1)
            if claim.predicate == "grid_size_text":
                object.__setattr__(self, "grid_or_layout_indicator_count",
                                   self.grid_or_layout_indicator_count + 1)
        object.__setattr__(self, "source_backed_claim_count", source_backed)
        object.__setattr__(self, "source_span_count", span_count)
        object.__setattr__(self, "scope_indicator_claim_count", scope_indicators)
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------


class SessionCacheKey(AgentBaseModel):
    """Deterministic cache key for an investigation session.

    Two runs with the same key can reuse the same session artifact and
    evidence ledger; any field change invalidates the cache.
    """

    requirement_hash: str
    source_index_hash: str
    patch_type: str
    investigation_mode: str
    require_source_backed_evidence: bool
    budget_hash: str
    policy_hash: str
    tool_registry_hash: str
    investigator_model: str | None = None
    investigator_reasoning_effort: str | None = None
    investigator_output_mode: str | None = None
    schema_version: str = "0.1"

    def to_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class SessionCacheEntry(AgentBaseModel):
    """One cached session: result hash + claim ids + coverage."""

    cache_key_hash: str
    session_id: str
    result_hash: str
    evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_payloads: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    evidence_context_hash: str = ""
    ledger_hash: str = ""
    coverage: dict[str, Any] = Field(default_factory=dict)
    completed: bool = False


class InvestigationSessionCache:
    """In-memory session cache.  One instance per incremental run.

    The cache is consulted before the LLM is invoked; a hit returns the
    stored payloads and skips the LLM call entirely.
    """

    def __init__(self) -> None:
        self._entries: dict[str, SessionCacheEntry] = {}

    def get(self, key: SessionCacheKey) -> SessionCacheEntry | None:
        return self._entries.get(key.to_hash())

    def put(self, key: SessionCacheKey, entry: SessionCacheEntry) -> None:
        self._entries[key.to_hash()] = entry

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Top-level Facts investigation entry point
# ---------------------------------------------------------------------------


def run_facts_investigation_stage(
    *,
    requirement: str,
    state: Any | None = None,
    config: PlanInvestigationConfig | None = None,
    llm_client: Callable[[str], str] | None = None,
    registry: InvestigationToolRegistry | None = None,
    policy_registry: InvestigationPolicyRegistry | None = None,
    session_cache: InvestigationSessionCache | None = None,
    shared_source_index: SourceIndex | None = None,
    shared_ledger: PlanningEvidenceLedger | None = None,
    artifact_output_dir: Path | None = None,
    add_event: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> InvestigationStageOutcome:
    """Run the Facts investigation stage.

    Returns an :class:`InvestigationStageOutcome` describing what
    happened.  The caller is responsible for:

    * Forwarding ``outcome.evidence_payloads`` into
      :class:`PatchGenerationContext.investigation_evidence`.
    * If ``outcome.should_block_facts_patch`` is True, NOT invoking the
      Facts patch LLM.
    * Persisting ``shared_source_index`` / ``shared_ledger`` via the
      Step 1 state_compat helpers if they want to reuse them across
      patch types.
    """

    # Resolve config.
    if config is None:
        try:
            config = _read_config(state)
        except PlanInvestigationIssue as issue:
            return _outcome_from_config_error(issue)
    if config.is_off:
        return InvestigationStageOutcome(
            mode=config.mode, patch_type="facts"
        )

    # Build / reuse shared SourceIndex.
    if shared_source_index is None:
        shared_source_index = build_investigation_source_index(requirement)
    source_indexes = {shared_source_index.document.source_id: shared_source_index}

    # Build / reuse shared Ledger.
    if shared_ledger is None:
        shared_ledger = build_investigation_ledger(
            requirement_text=requirement, source_indexes=source_indexes
        )

    # Resolve registry + policy.
    if registry is None:
        registry = build_default_step2_registry()
    if policy_registry is None:
        policy_registry = default_policy_registry()

    # Session cache check.
    cache_key = _build_cache_key(
        requirement=requirement,
        source_index=shared_source_index,
        config=config,
        registry=registry,
        policy_registry=policy_registry,
    )
    cache_entry: SessionCacheEntry | None = None
    if session_cache is not None and config.reuse_cached_session:
        cache_entry = session_cache.get(cache_key)
    if cache_entry is not None:
        if add_event is not None:
            add_event(
                EVENT_INVESTIGATION_CACHE_REUSED,
                f"facts investigation cache reused (key={cache_key.to_hash()[:12]})",
                {"patch_type": "facts", "session_id": cache_entry.session_id},
            )
        return InvestigationStageOutcome(
            mode=config.mode,
            patch_type="facts",
            completed=cache_entry.completed,
            session_id=cache_entry.session_id,
            evidence_claim_ids=cache_entry.evidence_claim_ids,
            evidence_payloads=cache_entry.evidence_payloads,
            evidence_context_hash=cache_entry.evidence_context_hash,
            ledger_hash=cache_entry.ledger_hash,
            source_index_hash=shared_source_index.index_hash,
            cache_reused=True,
            coverage=cache_entry.coverage,
            result_hash=cache_entry.result_hash,
        )

    if add_event is not None:
        add_event(
            EVENT_INVESTIGATION_STARTED,
            "facts investigation starting",
            {
                "patch_type": "facts",
                "mode": config.mode.value,
                "requirement_hash": content_hash(requirement)[:12],
                "source_index_hash": shared_source_index.index_hash[:12],
            },
        )

    # Run the investigation.
    result = run_investigation_stage(
        requirement=requirement,
        patch_type="facts",
        config=config,
        registry=registry,
        policy_registry=policy_registry,
        llm_client=llm_client,
        source_indexes=source_indexes,
        ledger=shared_ledger,
    )
    if result is None:
        # mode=off path: should have been caught above; defensive.
        return InvestigationStageOutcome(
            mode=config.mode, patch_type="facts"
        )

    # Build coverage + evidence payloads.
    coverage = FactsInvestigationCoverage().from_result(result, shared_ledger)
    payloads: list[dict[str, Any]] = []
    if result.evidence_claim_ids:
        payloads = collect_evidence_for_patch_prompt(
            shared_ledger, result.evidence_claim_ids
        )
    evidence_context_hash = content_hash(
        {
            "patch_type": "facts",
            "evidence_claim_ids": list(result.evidence_claim_ids),
            "ledger_hash": shared_ledger.ledger_hash,
        }
    )

    # Write session artifact (only when caller wants artifacts).
    if artifact_output_dir is not None:
        try:
            write_investigation_session_artifact(
                output_dir=artifact_output_dir, result=result
            )
        except PlanInvestigationIssue as issue:
            if add_event is not None:
                add_event(
                    EVENT_INVESTIGATION_WARNING,
                    f"session artifact write failed: {issue.message}",
                    {"code": issue.code},
                )

    outcome = InvestigationStageOutcome(
        mode=config.mode,
        patch_type="facts",
        completed=result.completed and not result.blocked,
        blocked=result.blocked,
        block_code=result.block_code,
        block_message=result.block_message,
        session_id=result.session_id,
        evidence_claim_ids=result.evidence_claim_ids,
        evidence_payloads=tuple(payloads),
        evidence_context_hash=evidence_context_hash,
        ledger_hash=shared_ledger.ledger_hash,
        source_index_hash=shared_source_index.index_hash,
        coverage=coverage.to_dict(),
        warnings=result.warnings,
        result_hash=result.result_hash,
    )

    # Controlled-mode post-conditions.  A "completed" Facts investigation
    # in controlled mode must satisfy the minimum coverage contract.
    if config.is_controlled and outcome.completed:
        if not _passes_controlled_facts_coverage(coverage):
            outcome = outcome.model_copy(
                update={
                    "completed": False,
                    "blocked": True,
                    "block_code": BLOCK_CODE_FACTS_BLOCKED,
                    "block_message": (
                        "controlled Facts investigation did not satisfy the "
                        "minimum coverage contract (structure + schema + "
                        "search + source-backed evidence)"
                    ),
                }
            )

    # Populate the cache regardless of outcome so subsequent retries do
    # not re-invoke the LLM.  The caller can still distinguish blocked
    # from completed via ``outcome.completed``.
    if session_cache is not None:
        session_cache.put(
            cache_key,
            SessionCacheEntry(
                cache_key_hash=cache_key.to_hash(),
                session_id=result.session_id,
                result_hash=result.result_hash,
                evidence_claim_ids=outcome.evidence_claim_ids,
                evidence_payloads=outcome.evidence_payloads,
                evidence_context_hash=outcome.evidence_context_hash,
                ledger_hash=outcome.ledger_hash,
                coverage=outcome.coverage,
                completed=outcome.completed,
            ),
        )

    if outcome.blocked:
        if add_event is not None:
            add_event(
                EVENT_INVESTIGATION_BLOCKED,
                f"facts investigation blocked: code={outcome.block_code}",
                {
                    "patch_type": "facts",
                    "block_code": outcome.block_code,
                    "session_id": outcome.session_id,
                    "tool_call_count": len(result.tool_calls),
                    "evidence_claim_count": len(outcome.evidence_claim_ids),
                },
            )
    elif outcome.completed:
        if add_event is not None:
            add_event(
                EVENT_INVESTIGATION_COMPLETED,
                "facts investigation completed",
                {
                    "patch_type": "facts",
                    "session_id": outcome.session_id,
                    "evidence_claim_count": len(outcome.evidence_claim_ids),
                    "evidence_context_hash": outcome.evidence_context_hash[:12],
                },
            )

    return outcome


def inject_investigation_evidence_into_context(
    context: Any | None,
    outcome: InvestigationStageOutcome,
) -> Any:
    """Return a copy of ``context`` with ``investigation_evidence`` populated.

    Returns ``context`` unchanged when the outcome has no evidence or
    when ``context`` is None.
    """

    if context is None:
        return context
    if not outcome.evidence_payloads:
        return context
    if not hasattr(context, "model_copy"):
        return context
    return context.model_copy(
        update={"investigation_evidence": list(outcome.evidence_payloads)}
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_config(state: Any | None) -> PlanInvestigationConfig:
    from .runner import get_investigation_config

    return get_investigation_config(state)


def _outcome_from_config_error(issue: PlanInvestigationIssue) -> InvestigationStageOutcome:
    return InvestigationStageOutcome(
        mode=PlanInvestigationMode.CONTROLLED,  # surface as serious
        patch_type="facts",
        completed=False,
        blocked=True,
        block_code=issue.code,
        block_message=issue.message,
        warnings=(issue.message,),
    )


def _build_cache_key(
    *,
    requirement: str,
    source_index: SourceIndex,
    config: PlanInvestigationConfig,
    registry: InvestigationToolRegistry,
    policy_registry: InvestigationPolicyRegistry,
) -> SessionCacheKey:
    """Build a deterministic cache key for the Facts investigation."""

    tool_specs = [spec.model_dump(mode="json") for spec in registry.list_tools()]
    policy_dump = {
        patch_type: policy.model_dump(mode="json")
        for patch_type, policy in policy_registry.policies.items()
    }
    return SessionCacheKey(
        requirement_hash=content_hash(requirement),
        source_index_hash=source_index.index_hash,
        patch_type="facts",
        investigation_mode=config.mode.value,
        require_source_backed_evidence=config.require_source_backed_evidence,
        budget_hash=content_hash(config.budget.model_dump(mode="json")),
        policy_hash=content_hash({"facts": policy_dump.get("facts", {})}),
        tool_registry_hash=content_hash({"tools": tool_specs}),
        investigator_model=config.investigator_model,
        investigator_reasoning_effort=config.investigator_reasoning_effort,
        investigator_output_mode=config.investigator_output_mode,
    )


def _passes_controlled_facts_coverage(coverage: FactsInvestigationCoverage) -> bool:
    """Controlled-mode minimum contract for a Facts investigation.

    Requires:
      * inspect_requirement_structure ran
      * inspect_patch_schema ran
      * search_source_index ran
      * at least one source-backed EvidenceClaim
    """

    return (
        coverage.requirement_structure_inspected
        and coverage.patch_schema_inspected
        and coverage.source_search_executed
        and coverage.source_backed_claim_count >= 1
    )
