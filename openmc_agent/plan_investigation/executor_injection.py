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
    """Run the Facts investigation stage (Phase 8A Step 4 wrapper).

    Phase 8A Step 6: this is now a thin wrapper around the generic
    :func:`run_patch_investigation_stage` that fixes ``patch_type=
    "facts"``.  Materials and Universes investigations go through the
    generic function directly so they can pass inventory / requirement
    context.

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

    return run_patch_investigation_stage(
        patch_type="facts",
        requirement=requirement,
        state=state,
        config=config,
        llm_client=llm_client,
        registry=registry,
        policy_registry=policy_registry,
        session_cache=session_cache,
        shared_source_index=shared_source_index,
        shared_ledger=shared_ledger,
        artifact_output_dir=artifact_output_dir,
        add_event=add_event,
    )


# Stable block codes for the Materials / Universes investigation stages.
BLOCK_CODE_MATERIALS_BLOCKED = "planning.investigation_materials_blocked"
BLOCK_CODE_UNIVERSES_BLOCKED = "planning.investigation_universes_blocked"


def _block_code_for_patch_type(patch_type: str) -> str:
    if patch_type == "materials":
        return BLOCK_CODE_MATERIALS_BLOCKED
    if patch_type == "universes":
        return BLOCK_CODE_UNIVERSES_BLOCKED
    return BLOCK_CODE_FACTS_BLOCKED


def run_patch_investigation_stage(
    *,
    patch_type: str,
    requirement: str,
    state: Any | None = None,
    config: PlanInvestigationConfig | None = None,
    llm_client: Callable[[str], str] | None = None,
    registry: InvestigationToolRegistry | None = None,
    policy_registry: InvestigationPolicyRegistry | None = None,
    session_cache: InvestigationSessionCache | None = None,
    shared_source_index: SourceIndex | None = None,
    shared_ledger: PlanningEvidenceLedger | None = None,
    accepted_facts: Any = None,
    geometry_inventory: Any = None,
    material_requirement_set: Any = None,
    universe_requirement_set: Any = None,
    artifact_output_dir: Path | None = None,
    add_event: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> InvestigationStageOutcome:
    """Run the investigation stage for any patch type (facts/materials/universes).

    Phase 8A Step 6 (P0-1 fix): the previous implementation only ran
    for the Facts patch.  Materials and Universes patches received at
    most a static inventory-payload injection — no mandatory baseline,
    no LLM supplemental plan, no typed synthesis, no shared Ledger
    update.  This generic function closes that gap.

    For ``patch_type="facts"`` the behaviour is byte-identical to the
    legacy Facts path (the wrapper above delegates here).  For
    ``patch_type in {"materials", "universes"}`` the function:

    * Reuses the SAME shared SourceIndex + Ledger that the Facts
      investigation produced (callers must pass ``shared_source_index``
      and ``shared_ledger`` so claims accumulate across patch types).
    * Forwards ``accepted_facts`` / ``geometry_inventory`` /
      ``material_requirement_set`` / ``universe_requirement_set`` into
      :class:`InvestigationContext` so the Materials/Universes baseline
      resolver can read fuel variants and inventory roles.
    * Applies a per-patch-type coverage check.  For ``facts`` the
      legacy FactsInvestigationCoverage is used.  For materials /
      universes a :class:`PatchInvestigationCoverage` check applies
      (at minimum: schema inspection + at least one source search +
      at least one source-backed claim).
    """

    if config is None:
        try:
            config = _read_config(state)
        except PlanInvestigationIssue as issue:
            return _outcome_from_config_error(issue)
    if config.is_off:
        return InvestigationStageOutcome(
            mode=config.mode, patch_type=patch_type
        )

    if shared_source_index is None:
        shared_source_index = build_investigation_source_index(requirement)
    source_indexes = {shared_source_index.document.source_id: shared_source_index}

    if shared_ledger is None:
        shared_ledger = build_investigation_ledger(
            requirement_text=requirement, source_indexes=source_indexes
        )

    if registry is None:
        registry = build_default_step2_registry()
    if policy_registry is None:
        policy_registry = default_policy_registry()

    cache_key = _build_cache_key(
        requirement=requirement,
        source_index=shared_source_index,
        config=config,
        registry=registry,
        policy_registry=policy_registry,
        patch_type=patch_type,
    )
    cache_entry: SessionCacheEntry | None = None
    if session_cache is not None and config.reuse_cached_session:
        cache_entry = session_cache.get(cache_key)
    if cache_entry is not None:
        if add_event is not None:
            add_event(
                EVENT_INVESTIGATION_CACHE_REUSED,
                f"{patch_type} investigation cache reused (key={cache_key.to_hash()[:12]})",
                {"patch_type": patch_type, "session_id": cache_entry.session_id},
            )
        return InvestigationStageOutcome(
            mode=config.mode,
            patch_type=patch_type,
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
            f"{patch_type} investigation starting",
            {
                "patch_type": patch_type,
                "mode": config.mode.value,
                "requirement_hash": content_hash(requirement)[:12],
                "source_index_hash": shared_source_index.index_hash[:12],
                "has_accepted_facts": accepted_facts is not None,
                "has_geometry_inventory": geometry_inventory is not None,
            },
        )

    result = run_investigation_stage(
        requirement=requirement,
        patch_type=patch_type,
        config=config,
        registry=registry,
        policy_registry=policy_registry,
        llm_client=llm_client,
        source_indexes=source_indexes,
        ledger=shared_ledger,
        accepted_facts=accepted_facts,
        geometry_inventory=geometry_inventory,
        material_requirement_set=material_requirement_set,
        universe_requirement_set=universe_requirement_set,
    )
    if result is None:
        return InvestigationStageOutcome(
            mode=config.mode, patch_type=patch_type
        )

    # Coverage: Facts uses the legacy FactsInvestigationCoverage;
    # materials/universes use the generic PatchInvestigationCoverage.
    coverage_dict: dict[str, Any]
    if patch_type == "facts":
        coverage = FactsInvestigationCoverage().from_result(result, shared_ledger)
        coverage_dict = coverage.to_dict()
    else:
        patch_coverage = PatchInvestigationCoverage().from_result(
            result, shared_ledger, patch_type=patch_type
        )
        coverage_dict = patch_coverage.to_dict()
    payloads: list[dict[str, Any]] = []
    if result.evidence_claim_ids:
        payloads = collect_evidence_for_patch_prompt(
            shared_ledger, result.evidence_claim_ids
        )
    evidence_context_hash = content_hash(
        {
            "patch_type": patch_type,
            "evidence_claim_ids": list(result.evidence_claim_ids),
            "ledger_hash": shared_ledger.ledger_hash,
        }
    )

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
        patch_type=patch_type,
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
        coverage=coverage_dict,
        warnings=result.warnings,
        result_hash=result.result_hash,
    )

    # Controlled post-condition: per-patch-type coverage check.
    if config.is_controlled and outcome.completed:
        passed = (
            _passes_controlled_facts_coverage(coverage)
            if patch_type == "facts"
            else _passes_controlled_patch_coverage(coverage_dict, patch_type)
        )
        if not passed:
            block_code = _block_code_for_patch_type(patch_type)
            outcome = outcome.model_copy(
                update={
                    "completed": False,
                    "blocked": True,
                    "block_code": block_code,
                    "block_message": (
                        f"controlled {patch_type} investigation did not satisfy "
                        f"the minimum coverage contract"
                    ),
                }
            )

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
                f"{patch_type} investigation blocked: code={outcome.block_code}",
                {
                    "patch_type": patch_type,
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
                f"{patch_type} investigation completed",
                {
                    "patch_type": patch_type,
                    "session_id": outcome.session_id,
                    "evidence_claim_count": len(outcome.evidence_claim_ids),
                    "evidence_context_hash": outcome.evidence_context_hash[:12],
                },
            )

    return outcome


class PatchInvestigationCoverage(AgentBaseModel):
    """Generic coverage metric for Materials/Universes investigations.

    Phase 8A Step 6 (Section 8): tracks the minimum controlled-mode
    coverage contract.  A "completed" Materials or Universes
    investigation must:

    * inspect the owner patch schema at least once,
    * run at least one source search,
    * produce at least one source-backed EvidenceClaim,
    * query the ledger at least once (to confirm it saw existing
      evidence).
    """

    patch_type: str = ""
    schema_inspection_count: int = 0
    source_search_count: int = 0
    ledger_query_count: int = 0
    tool_call_count: int = 0
    source_backed_claim_count: int = 0
    typed_semantic_claim_count: int = 0
    coverage_complete: bool = False

    def from_result(
        self,
        result: InvestigationResult,
        ledger: PlanningEvidenceLedger,
        *,
        patch_type: str,
    ) -> "PatchInvestigationCoverage":
        self.patch_type = patch_type
        self.tool_call_count = len(result.tool_calls)
        for call in result.tool_calls:
            # ToolCallRecord has ``tool_name`` (not ``tool``).
            tool = getattr(call, "tool_name", None) or getattr(call, "tool", "")
            if tool == "inspect_patch_schema":
                self.schema_inspection_count += 1
            elif tool == "search_source_index":
                self.source_search_count += 1
            elif tool == "query_evidence_ledger":
                self.ledger_query_count += 1
        for claim_id in result.evidence_claim_ids:
            claim = ledger.claims.get(claim_id)
            if claim is None:
                continue
            if claim.source_refs:
                self.source_backed_claim_count += 1
            else:
                self.typed_semantic_claim_count += 1
        self.coverage_complete = (
            self.schema_inspection_count >= 1
            and self.source_search_count >= 1
            and self.source_backed_claim_count >= 1
        )
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _passes_controlled_patch_coverage(coverage: dict[str, Any], patch_type: str) -> bool:
    """Minimum coverage contract for Materials / Universes investigations."""

    if not coverage:
        return False
    return (
        coverage.get("schema_inspection_count", 0) >= 1
        and coverage.get("source_search_count", 0) >= 1
        and coverage.get("source_backed_claim_count", 0) >= 1
    )


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
    patch_type: str = "facts",
) -> SessionCacheKey:
    """Build a deterministic cache key for an investigation session.

    Phase 8A Step 6: ``patch_type`` parameter so the Materials /
    Universes investigations get distinct cache entries (the previous
    implementation hardcoded ``"facts"``).
    """

    tool_specs = [spec.model_dump(mode="json") for spec in registry.list_tools()]
    policy_dump = {
        p_type: policy.model_dump(mode="json")
        for p_type, policy in policy_registry.policies.items()
    }
    return SessionCacheKey(
        requirement_hash=content_hash(requirement),
        source_index_hash=source_index.index_hash,
        patch_type=patch_type,
        investigation_mode=config.mode.value,
        require_source_backed_evidence=config.require_source_backed_evidence,
        budget_hash=content_hash(config.budget.model_dump(mode="json")),
        policy_hash=content_hash({patch_type: policy_dump.get(patch_type, {})}),
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
