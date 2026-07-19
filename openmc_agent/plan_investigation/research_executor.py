"""Phase 8A Step 6B — minimal research executor (Sections 13-15).

Executes a :class:`PlanResearchRequest` against the existing
SourceIndex + Ledger using the existing read-only investigation tools.
No shell, no network search, no repository grep — only the bounded
tools already approved for the investigation layer.

The executor is deliberately minimal in Step 6B:

* Runs the deterministic mandatory actions (query_evidence_ledger +
  search_source_index for each target's suggested search terms).
* Records a :class:`SourceAbsenceRecord` for each target that
  produced no new evidence.
* Computes a :class:`PlanningEvidenceDelta` from the ledger hash
  before/after.
* Returns a :class:`PlanResearchResult` with status:

    - ``evidence_added`` when new claims were committed,
    - ``no_evidence_found`` when no targets produced claims
      (caller decides ASK_HUMAN vs FAIL_CLOSED),
    - ``no_progress`` when the delta is empty AND the request
      fingerprint was already seen.

The full LLM-supplemental-action plan + invalidation + authoritative
replay (Sections 17-19) is reserved for a follow-up commit; this
commit ships the deterministic core that unblocks the Material-
Universe canary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import PlanInvestigationIssue
from .evidence_ledger import PlanningEvidenceLedger
from .research_models import (
    PlanResearchRequest,
    PlanResearchResult,
    PlanResearchStatus,
    PlanningEvidenceDelta,
    SourceAbsenceRecord,
)
from .source_index import SourceIndex

__all__ = [
    "execute_plan_research_request",
    "ResearchExecutorConfig",
]


class ResearchExecutorConfig:
    """Budget knobs for the research executor.

    Defaults follow Section 16 recommendations:

    * ``max_research_rounds_per_gate = 1``
    * ``max_research_tool_calls_per_gate = 6``
    * ``max_research_llm_calls_per_gate = 2`` (used by the LLM-
      supplemental path that ships in a follow-up commit).
    """

    def __init__(
        self,
        *,
        max_tool_calls_per_gate: int = 6,
        max_results_per_search: int = 5,
        enable_llm_supplemental: bool = False,
    ) -> None:
        self.max_tool_calls_per_gate = max_tool_calls_per_gate
        self.max_results_per_search = max_results_per_search
        self.enable_llm_supplemental = enable_llm_supplemental


def execute_plan_research_request(
    *,
    request: PlanResearchRequest,
    source_index: SourceIndex,
    ledger: PlanningEvidenceLedger,
    config: ResearchExecutorConfig | None = None,
    seen_request_fingerprints: set[str] | None = None,
    seen_delta_hashes: set[str] | None = None,
    add_event: Callable[[str, str, dict[str, Any]], None] | None = None,
    artifact_output_dir: Path | None = None,
) -> PlanResearchResult:
    """Execute one research request deterministically.

    The executor reuses the read-only investigation tools
    (``query_evidence_ledger`` + ``search_source_index``).  It does
    NOT invoke the LLM in this minimal version; that path is reserved
    for a follow-up commit.
    """

    if config is None:
        config = ResearchExecutorConfig()
    if seen_request_fingerprints is None:
        seen_request_fingerprints = set()
    if seen_delta_hashes is None:
        seen_delta_hashes = set()
    ledger_hash_before = ledger.ledger_hash
    tool_calls: list[dict[str, Any]] = []
    absence_records: list[SourceAbsenceRecord] = []
    new_claim_ids: list[str] = []
    new_span_ids: list[str] = []
    budget_used = 0
    # No-progress precheck: same request fingerprint already executed.
    if request.request_fingerprint in seen_request_fingerprints:
        return PlanResearchResult(
            request_id=request.request_id,
            status=PlanResearchStatus.NO_PROGRESS,
            tool_calls=tuple(tool_calls),
            ledger_hash_after=ledger_hash_before,
            no_progress=True,
            budget_used={"tool_calls": 0, "reason": "duplicate_fingerprint"},
            warnings=("request fingerprint already executed",),
        )
    # Run deterministic mandatory actions per target.
    for target in request.targets:
        if budget_used >= config.max_tool_calls_per_gate:
            break
        # Step 1: query the existing ledger for matching predicates.
        matching_claims = _query_ledger_for_predicates(
            ledger, target.claim_predicates,
        )
        tool_calls.append({
            "tool": "query_evidence_ledger",
            "arguments": {"predicates": list(target.claim_predicates)},
            "result_count": len(matching_claims),
        })
        budget_used += 1
        if matching_claims:
            for claim_id in matching_claims:
                if claim_id not in new_claim_ids:
                    new_claim_ids.append(claim_id)
            continue  # target satisfied; no search needed
        # Step 2: run a bounded source search per suggested term.
        any_hit = False
        for term in target.suggested_search_terms:
            if budget_used >= config.max_tool_calls_per_gate:
                break
            hits = _search_source_index(
                source_index, term, limit=config.max_results_per_search,
            )
            tool_calls.append({
                "tool": "search_source_index",
                "arguments": {"query": term},
                "result_count": len(hits),
            })
            budget_used += 1
            if hits:
                any_hit = True
                for span in hits:
                    if span.span_id not in new_span_ids:
                        new_span_ids.append(span.span_id)
                break  # one good search per target
        if not any_hit:
            absence_records.append(SourceAbsenceRecord(
                request_id=request.request_id,
                target_id=target.target_id,
                source_ids_searched=(source_index.document.source_id,),
                query_fingerprints=tuple(target.suggested_search_terms),
                search_result_counts={
                    term: 0 for term in target.suggested_search_terms
                },
                search_complete_within_policy=True,
            ))
    # Compute the evidence delta.  In this minimal executor we don't
    # add NEW EvidenceClaims (that requires the LLM synthesis path);
    # we only surface what's already in the ledger or what spans were
    # located.  The delta hash distinguishes "found new spans" from
    # "found nothing".
    delta = PlanningEvidenceDelta(
        request_id=request.request_id,
        ledger_hash_before=ledger_hash_before,
        ledger_hash_after=ledger.ledger_hash,  # unchanged in minimal path
        added_claim_ids=tuple(new_claim_ids),
        added_source_span_ids=tuple(new_span_ids),
    )
    # No-progress check: same delta hash already seen.
    if delta.delta_hash in seen_delta_hashes and delta.is_empty:
        status = PlanResearchStatus.NO_PROGRESS
    elif new_claim_ids or new_span_ids:
        # Phase 8A Step 7 (Section 4): distinguish "found spans" from
        # "committed evidence".  The minimal executor does NOT accept
        # new EvidenceClaims (that requires the LLM synthesis path);
        # it only locates candidate spans.  So we report
        # ``candidate_spans_found`` rather than ``evidence_added``.
        # The gate MUST NOT reopen on this status.
        if new_claim_ids:
            # Claims existed in the ledger before but were not yet
            # surfaced via this target's predicate query.  This is
            # still not "new evidence committed" — the ledger hash
            # did not change.
            status = PlanResearchStatus.CANDIDATE_SPANS_FOUND
        else:
            status = PlanResearchStatus.CANDIDATE_SPANS_FOUND
    elif absence_records:
        status = PlanResearchStatus.NO_EVIDENCE_FOUND
    else:
        status = PlanResearchStatus.NO_PROGRESS
    # Write artifact if requested.
    if artifact_output_dir is not None:
        try:
            import json
            artifact_output_dir.mkdir(parents=True, exist_ok=True)
            (artifact_output_dir / f"research_result_{request.request_id}.json").write_text(
                json.dumps({
                    "request_id": request.request_id,
                    "status": status,
                    "tool_calls": tool_calls,
                    "absence_records": [a.model_dump(mode="json") for a in absence_records],
                    "delta": delta.model_dump(mode="json"),
                }, indent=2, default=str)
            )
        except Exception:
            pass
    if add_event is not None:
        add_event(
            f"planning.research_{status}",
            f"research request {request.request_id} → {status}",
            {
                "tool_call_count": len(tool_calls),
                "absence_count": len(absence_records),
                "added_claim_count": len(new_claim_ids),
                "added_span_count": len(new_span_ids),
            },
        )
    return PlanResearchResult(
        request_id=request.request_id,
        status=status,
        tool_calls=tuple(tool_calls),
        evidence_delta=delta,
        ledger_hash_after=ledger.ledger_hash,
        absence_records=tuple(absence_records),
        no_progress=(status == PlanResearchStatus.NO_PROGRESS),
        budget_used={"tool_calls": budget_used},
    )


def _query_ledger_for_predicates(
    ledger: PlanningEvidenceLedger,
    predicates: Iterable[str],
) -> list[str]:
    """Return claim ids whose predicate matches any in ``predicates``."""

    wanted = {p for p in predicates if p}
    if not wanted:
        return []
    out: list[str] = []
    for claim_id, claim in ledger.claims.items():
        if claim.predicate in wanted:
            out.append(claim_id)
    return out


def _search_source_index(
    source_index: SourceIndex,
    query: str,
    *,
    limit: int = 5,
) -> list[Any]:
    """Search the source index using its built-in search."""

    if not query:
        return []
    try:
        results = source_index.search(query, limit=limit)
    except Exception:
        return []
    return list(results) if results else []
