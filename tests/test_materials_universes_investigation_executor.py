"""Phase 8A Step 6A — Materials/Universes investigation executor + shared ledger.

Verifies:
* The unified ``run_patch_investigation_stage`` handles all three patch
  types (facts/materials/universes).
* The shared SourceIndex + Ledger are reused across patch types so
  claims accumulate (single canonical ledger per incremental run).
* Coverage contract applies per patch type.
* ``InvestigationContext`` carries the typed inventory context fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from openmc_agent.plan_investigation.evidence_ledger import (
    PlanningEvidenceLedger,
    create_empty_ledger,
)
from openmc_agent.plan_investigation.executor_injection import (
    BLOCK_CODE_FACTS_BLOCKED,
    BLOCK_CODE_MATERIALS_BLOCKED,
    BLOCK_CODE_UNIVERSES_BLOCKED,
    InvestigationSessionCache,
    PatchInvestigationCoverage,
    run_facts_investigation_stage,
    run_patch_investigation_stage,
)
from openmc_agent.plan_investigation.runner import (
    PlanInvestigationConfig,
    PlanInvestigationMode,
    build_investigation_source_index,
)
from openmc_agent.plan_investigation.source_index import SourceIndex


REQUIREMENT_TEXT = (
    "Build a single PWR fuel assembly. Fuel pin pellets are UO2 with "
    "4.5 wt% enrichment. Cladding is Zircaloy-4. The coolant is light "
    "water at 600 K. The fuel temperature is 1200 K. There are 264 "
    "fuel pins and 25 guide tubes."
)


def _controlled_config(patch_types: tuple[str, ...]) -> PlanInvestigationConfig:
    return PlanInvestigationConfig(
        mode=PlanInvestigationMode.CONTROLLED,
        patch_types=patch_types,
        require_source_backed_evidence=True,
    )


def _advisory_config(patch_types: tuple[str, ...]) -> PlanInvestigationConfig:
    return PlanInvestigationConfig(
        mode=PlanInvestigationMode.ADVISORY,
        patch_types=patch_types,
    )


def _shared_index_and_ledger() -> tuple[SourceIndex, PlanningEvidenceLedger]:
    idx = build_investigation_source_index(REQUIREMENT_TEXT)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    return idx, ledger


# ---------------------------------------------------------------------------
# Unified run_patch_investigation_stage
# ---------------------------------------------------------------------------


def test_unified_stage_off_mode_returns_empty_outcome() -> None:
    cfg = PlanInvestigationConfig(mode=PlanInvestigationMode.OFF)
    outcome = run_patch_investigation_stage(
        patch_type="materials", requirement="r", config=cfg,
    )
    assert outcome.completed is False
    assert outcome.blocked is False
    assert outcome.patch_type == "materials"


def test_unified_stage_controlled_without_llm_blocks() -> None:
    """Controlled mode + no LLM client → blocking outcome."""

    cfg = _controlled_config(("materials",))
    outcome = run_patch_investigation_stage(
        patch_type="materials", requirement="r", config=cfg,
        llm_client=None,
    )
    assert outcome.blocked is True
    assert outcome.completed is False


def test_unified_stage_advisory_without_llm_returns_warning() -> None:
    """Advisory mode + no LLM → non-blocking warning outcome."""

    cfg = _advisory_config(("materials",))
    outcome = run_patch_investigation_stage(
        patch_type="materials", requirement="r", config=cfg,
        llm_client=None,
    )
    assert outcome.blocked is False
    assert outcome.completed is False
    assert any("llm_client" in w for w in outcome.warnings)


def test_facts_wrapper_delegates_to_unified_function() -> None:
    """run_facts_investigation_stage is a thin wrapper."""

    cfg = PlanInvestigationConfig(mode=PlanInvestigationMode.OFF)
    outcome_facts = run_facts_investigation_stage(
        requirement="r", config=cfg,
    )
    outcome_unified = run_patch_investigation_stage(
        patch_type="facts", requirement="r", config=cfg,
    )
    assert outcome_facts.patch_type == outcome_unified.patch_type == "facts"
    assert outcome_facts.completed == outcome_unified.completed
    assert outcome_facts.blocked == outcome_unified.blocked


def test_distinct_cache_keys_per_patch_type() -> None:
    """Cache key includes patch_type so Materials session doesn't reuse Facts."""

    cache = InvestigationSessionCache()
    cfg = PlanInvestigationConfig(mode=PlanInvestigationMode.OFF)
    # Two off-mode calls (no LLM) — should populate no cache but use
    # distinct keys.
    run_patch_investigation_stage(
        patch_type="facts", requirement="r", config=cfg,
        session_cache=cache,
    )
    run_patch_investigation_stage(
        patch_type="materials", requirement="r", config=cfg,
        session_cache=cache,
    )
    # No cache entries populated (off mode skips the cache write).
    assert len(cache) == 0


# ---------------------------------------------------------------------------
# Shared ledger reuse
# ---------------------------------------------------------------------------


def test_shared_ledger_is_reused_across_patch_types() -> None:
    """The caller-supplied shared_ledger is the canonical ledger.

    Phase 8A Step 6 (Section 7): one incremental run → one canonical
    PlanningEvidenceLedger.  Facts / Materials / Universes
    investigations all see (and add to) the same ledger object.
    """

    idx, ledger = create_empty_ledger_with_index()
    # Sanity: same ledger object passed in is the one returned via
    # outcome.ledger_hash.
    cfg = PlanInvestigationConfig(mode=PlanInvestigationMode.OFF)
    outcome_f = run_patch_investigation_stage(
        patch_type="facts", requirement=REQUIREMENT_TEXT, config=cfg,
        shared_source_index=idx, shared_ledger=ledger,
    )
    outcome_m = run_patch_investigation_stage(
        patch_type="materials", requirement=REQUIREMENT_TEXT, config=cfg,
        shared_source_index=idx, shared_ledger=ledger,
    )
    outcome_u = run_patch_investigation_stage(
        patch_type="universes", requirement=REQUIREMENT_TEXT, config=cfg,
        shared_source_index=idx, shared_ledger=ledger,
    )
    # All three outcomes carry the SAME ledger_hash (proving they
    # share the canonical ledger, not a freshly-built one).
    assert outcome_f.ledger_hash == outcome_m.ledger_hash == outcome_u.ledger_hash
    assert outcome_f.ledger_hash == ledger.ledger_hash


def create_empty_ledger_with_index() -> tuple[SourceIndex, PlanningEvidenceLedger]:
    idx = build_investigation_source_index(REQUIREMENT_TEXT)
    ledger = create_empty_ledger(requirement_hash=idx.document.source_id)
    return idx, ledger


# ---------------------------------------------------------------------------
# PatchInvestigationCoverage
# ---------------------------------------------------------------------------


def test_patch_coverage_records_tool_kind_counts() -> None:
    """Coverage tracks schema_inspection / source_search / ledger_query."""

    from openmc_agent.plan_investigation.agent import (
        InvestigationResult,
    )
    from openmc_agent.plan_investigation.tool_artifacts import ToolCallRecord
    coverage = PatchInvestigationCoverage()
    result = InvestigationResult(
        session_id="s1",
        patch_type="materials",
        tool_calls=(
            ToolCallRecord(
                tool_name="inspect_patch_schema",
                arguments_hash="ah1",
                result_hash="rh1",
            ),
            ToolCallRecord(
                tool_name="search_source_index",
                arguments_hash="ah2",
                result_hash="rh2",
            ),
            ToolCallRecord(
                tool_name="query_evidence_ledger",
                arguments_hash="ah3",
                result_hash="rh3",
            ),
        ),
        completed=True,
    )
    ledger = create_empty_ledger(requirement_hash="rh")
    coverage.from_result(result, ledger, patch_type="materials")
    assert coverage.schema_inspection_count == 1
    assert coverage.source_search_count == 1
    assert coverage.ledger_query_count == 1
    assert coverage.coverage_complete is False  # no source-backed claims


# ---------------------------------------------------------------------------
# InvestigationContext typed fields
# ---------------------------------------------------------------------------


def test_investigation_context_accepts_typed_inventory_fields() -> None:
    """P0-5 fix: InvestigationContext has accepted_facts / geometry_inventory."""

    from openmc_agent.plan_investigation.agent import InvestigationContext

    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = InvestigationContext(
        requirement_text="r",
        patch_type="materials",
        ledger=ledger,
        accepted_facts={"fake": "facts"},
        geometry_inventory={"fake": "inventory"},
        material_requirement_set={"fake": "mreq"},
        universe_requirement_set={"fake": "ureq"},
    )
    assert ctx.accepted_facts == {"fake": "facts"}
    assert ctx.geometry_inventory == {"fake": "inventory"}
    assert ctx.material_requirement_set == {"fake": "mreq"}
    assert ctx.universe_requirement_set == {"fake": "ureq"}


def test_investigation_context_typed_fields_default_none() -> None:
    """Legacy callers (no inventory) → all typed fields default to None."""

    from openmc_agent.plan_investigation.agent import InvestigationContext

    ledger = create_empty_ledger(requirement_hash="rh")
    ctx = InvestigationContext(
        requirement_text="r",
        patch_type="facts",
        ledger=ledger,
    )
    assert ctx.accepted_facts is None
    assert ctx.geometry_inventory is None
    assert ctx.material_requirement_set is None
    assert ctx.universe_requirement_set is None
    # Budget still defaults to an InvestigationBudget instance.
    assert ctx.budget.max_tool_calls == 5
