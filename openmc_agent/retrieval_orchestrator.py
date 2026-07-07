"""Deterministic retrieval orchestration for validation and runtime issues.

The orchestrator coordinates the existing grep, graph, and local RAG tools. It
does not rewrite their internals, modify SimulationPlan objects, or promote
retrieved text into confirmed physical facts.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.evidence_ranker import (
    EvidenceRankerPolicy,
    EvidenceRankingResult,
    format_ranked_evidence_block,
    rank_and_select_evidence,
)
from openmc_agent.grep_search import (
    GrepSearchRequest,
    GrepSearchResult,
    RetrievedEvidence,
    format_grep_evidence,
    grep_request_from_issue,
    grep_result_to_evidence,
    grep_search,
)
from openmc_agent.graphrag_retriever import (
    GraphRagRequest,
    GraphRagResult,
    format_graphrag_evidence,
    format_graphrag_query_plan_section,
    graphrag_request_from_issues,
    graphrag_retrieve,
)
from openmc_agent.knowledge_graph import (
    GraphContext,
    GraphLookupRequest,
    format_graph_context,
    graph_context_to_evidence,
    graph_lookup,
    graph_request_from_issues,
)
from openmc_agent.rag_search import (
    RagSearchRequest,
    RagSearchResult,
    format_rag_evidence,
    merge_retrieved_evidence,
    rag_request_from_graph_context,
    rag_result_to_evidence,
    rag_search,
)
from openmc_agent.schemas import AgentBaseModel, ValidationIssue


_GREP_ROUTE_HINTS = {"reflect_plan", "auto_repair", "retrieval", "manual_review"}
_CROSS_SECTIONS_CODES = {
    "runtime.cross_sections_missing",
    "runtime.cross_sections_invalid",
}
_FACT_GAP_TOKENS = (
    "cross_sections",
    "missing_nuclide_data",
    "material_missing_nuclide_data",
    "density",
    "composition",
)


class RetrievalPolicy(AgentBaseModel):
    enable_grep: bool = True
    enable_graph: bool = True
    enable_rag: bool = True
    enable_graphrag: bool = True
    enable_graphrag_query_planner: bool = True
    prefer_graphrag_over_rag: bool = False
    run_rag_for_manual_review: bool = False
    max_issues: int = 8
    max_grep_evidence: int = 6
    max_graph_evidence: int = 4
    max_rag_evidence: int = 6
    max_graphrag_evidence: int = 6
    max_planned_graph_paths: int = 8
    max_merged_evidence: int = 12
    enable_evidence_ranking: bool = True
    max_ranked_evidence: int = 12
    max_evidence_prompt_chars: int = 6000
    skip_rag_for_fact_gap: bool = True
    skip_grep_for_cross_sections_missing: bool = True


class RetrievalTriggerDecision(AgentBaseModel):
    issue_code: str
    should_run_grep: bool
    should_run_graph: bool
    should_run_rag: bool
    reason: str


class RetrievalContext(AgentBaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)

    grep_requests: list[GrepSearchRequest] = Field(default_factory=list)
    grep_results: list[GrepSearchResult] = Field(default_factory=list)
    grep_evidence: list[RetrievedEvidence] = Field(default_factory=list)

    graph_request: GraphLookupRequest | None = None
    graph_context: GraphContext | None = None
    graph_evidence: list[RetrievedEvidence] = Field(default_factory=list)

    graphrag_request: GraphRagRequest | None = None
    graphrag_result: GraphRagResult | None = None
    graphrag_evidence: list[RetrievedEvidence] = Field(default_factory=list)

    rag_request: RagSearchRequest | None = None
    rag_result: RagSearchResult | None = None
    rag_evidence: list[RetrievedEvidence] = Field(default_factory=list)

    merged_evidence: list[RetrievedEvidence] = Field(default_factory=list)
    evidence_ranking_result: EvidenceRankingResult | None = None
    ranked_evidence: list[RetrievedEvidence] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)
    decisions: list[RetrievalTriggerDecision] = Field(default_factory=list)
    summary: str | None = None


def decide_retrieval_for_issue(
    issue: ValidationIssue,
    policy: RetrievalPolicy | None = None,
) -> RetrievalTriggerDecision:
    """Decide which retrieval layers an issue should trigger."""
    active_policy = policy or RetrievalPolicy()
    should_run_grep = bool(
        issue.grep_patterns
        or issue.route_hint in _GREP_ROUTE_HINTS
        or issue.code.startswith(("runtime.", "export_xml.", "lattice.hex."))
    )
    if (
        active_policy.skip_grep_for_cross_sections_missing
        and issue.code in _CROSS_SECTIONS_CODES
    ):
        should_run_grep = False

    should_run_graph = bool(issue.code or issue.schema_path or issue.concept_id)

    should_run_rag = bool(
        issue.requires_retrieval
        or issue.route_hint == "retrieval"
        or (
            active_policy.run_rag_for_manual_review
            and issue.route_hint == "manual_review"
        )
        or issue.code.startswith(("lattice.hex.", "runtime.geometry_overlap", "runtime.lost_particle"))
        or (issue.code.startswith("runtime.") and "unknown" in issue.code)
    )
    if active_policy.skip_rag_for_fact_gap and _is_fact_gap_issue(issue):
        should_run_rag = False

    reasons: list[str] = []
    if should_run_grep:
        reasons.append("grep: issue has search patterns, retrieval route, or runtime/export/hex code")
    elif issue.code in _CROSS_SECTIONS_CODES:
        reasons.append("grep skipped for cross-section fact gap")
    if should_run_graph:
        reasons.append("graph: issue has code/schema/concept anchors")
    if should_run_rag:
        reasons.append("rag: issue requests retrieval or is geometry/hex/runtime diagnostic")
    elif active_policy.skip_rag_for_fact_gap and _is_fact_gap_issue(issue):
        reasons.append("rag skipped for fact gap requiring human confirmation")
    if not reasons:
        reasons.append("no retrieval trigger matched")

    return RetrievalTriggerDecision(
        issue_code=issue.code,
        should_run_grep=should_run_grep,
        should_run_graph=should_run_graph,
        should_run_rag=should_run_rag,
        reason="; ".join(reasons),
    )


def gather_retrieval_context_for_issues(
    issues: list[ValidationIssue],
    policy: RetrievalPolicy | None = None,
) -> RetrievalContext:
    """Run the deterministic grep -> graph -> RAG -> merge retrieval pipeline."""
    active_policy = policy or RetrievalPolicy()
    bounded_issues = list(issues[: max(0, active_policy.max_issues)])
    context = RetrievalContext(issues=bounded_issues)
    if not bounded_issues:
        context.summary = summarize_retrieval_context(context)
        return context

    decisions = [
        decide_retrieval_for_issue(issue, active_policy) for issue in bounded_issues
    ]
    context.decisions = decisions

    if active_policy.enable_grep:
        _run_grep_stage(context, decisions, active_policy)
    else:
        context.skipped_steps.append("grep disabled by policy")

    if active_policy.enable_graph:
        _run_graph_stage(context, decisions, active_policy)
    else:
        context.skipped_steps.append("graph disabled by policy")

    if active_policy.enable_graphrag:
        _run_graphrag_stage(context, decisions, active_policy)
    else:
        context.skipped_steps.append("graphrag disabled by policy")

    if (
        active_policy.prefer_graphrag_over_rag
        and context.graphrag_evidence
    ):
        context.skipped_steps.append("rag skipped: GraphRAG evidence preferred by policy")
    elif active_policy.enable_rag:
        _run_rag_stage(context, decisions, active_policy)
    else:
        if active_policy.enable_graphrag:
            context.skipped_steps.append(
                "plain rag disabled by policy; GraphRAG document retrieval remains enabled"
            )
        else:
            context.skipped_steps.append("rag disabled by policy")

    context.merged_evidence = merge_retrieved_evidence(
        context.grep_evidence,
        context.graph_evidence,
        context.rag_evidence,
        max_items=active_policy.max_merged_evidence,
        graphrag_evidence=context.graphrag_evidence,
    )
    if active_policy.enable_evidence_ranking:
        _run_evidence_ranking(context, active_policy)
    else:
        context.skipped_steps.append("evidence ranking disabled by policy")
    context.summary = summarize_retrieval_context(context)
    return context


def format_retrieval_context(context: RetrievalContext) -> str:
    """Render bounded retrieval prompt sections."""
    if context.evidence_ranking_result and context.ranked_evidence:
        sections = [
            format_graphrag_query_plan_section(context.graphrag_request),
            format_graph_context(context.graph_context or GraphContext(), limit=8),
            format_ranked_evidence_block(context.evidence_ranking_result),
        ]
        return "".join(section for section in sections if section) + _format_evidence_safety_constraint()

    sections = [
        format_graphrag_query_plan_section(context.graphrag_request),
        format_grep_evidence(context.grep_evidence, limit=6),
        format_graph_context(context.graph_context or GraphContext(), limit=12),
        format_graphrag_evidence(context.graphrag_evidence, limit=6),
        format_rag_evidence(context.rag_evidence, limit=6),
    ]
    rendered = "".join(section for section in sections if section)
    if rendered:
        rendered += _format_evidence_safety_constraint()
    return rendered


def summarize_retrieval_context(context: RetrievalContext) -> str:
    """Return compact trace statistics without dumping evidence text."""
    graph_nodes = len(context.graph_context.nodes) if context.graph_context else 0
    graph_edges = len(context.graph_context.edges) if context.graph_context else 0
    graphrag_chunks = (
        len(context.graphrag_result.rag_result.chunks)
        if context.graphrag_result and context.graphrag_result.rag_result
        else 0
    )
    grep_matches = sum(len(result.matches) for result in context.grep_results)
    rag_chunks = len(context.rag_result.chunks) if context.rag_result else 0
    parts = [
        f"issues={len(context.issues)}",
        f"grep_requests={len(context.grep_requests)}",
        f"grep_matches={grep_matches}",
        f"grep_evidence={len(context.grep_evidence)}",
        f"graph_nodes={graph_nodes}",
        f"graph_edges={graph_edges}",
        f"graph_evidence={len(context.graph_evidence)}",
        f"graphrag_chunks={graphrag_chunks}",
        f"graphrag_evidence={len(context.graphrag_evidence)}",
        f"graphrag_intent={_graphrag_intent_type(context)}",
        f"planned_graph_paths={_planned_graph_path_count(context)}",
        f"preferred_queries={_preferred_query_count(context)}",
        f"rag_chunks={rag_chunks}",
        f"rag_evidence={len(context.rag_evidence)}",
        f"merged_evidence={len(context.merged_evidence)}",
        f"ranked_evidence={len(context.ranked_evidence)}",
    ]
    if context.evidence_ranking_result:
        summary = context.evidence_ranking_result.summary
        parts.append(f"dropped_duplicates={summary.get('dropped_duplicate_count', 0)}")
        parts.append(f"dropped_budget={summary.get('dropped_budget_count', 0)}")
    if context.warnings:
        parts.append(f"warnings={len(context.warnings)}")
    if context.skipped_steps:
        parts.append(f"skipped={len(context.skipped_steps)}")
    return ", ".join(parts)


def _graphrag_intent_type(context: RetrievalContext) -> str | None:
    request = context.graphrag_request
    if request and request.query_plan:
        return request.query_plan.intent.intent_type
    result = context.graphrag_result
    if result and result.request.query_plan:
        return result.request.query_plan.intent.intent_type
    return None


def _planned_graph_path_count(context: RetrievalContext) -> int:
    request = context.graphrag_request
    if request and request.query_plan:
        return len(request.query_plan.planned_paths)
    result = context.graphrag_result
    if result and result.request.query_plan:
        return len(result.request.query_plan.planned_paths)
    return 0


def _preferred_query_count(context: RetrievalContext) -> int:
    request = context.graphrag_request
    if request and request.query_plan:
        return len(request.query_plan.preferred_queries)
    result = context.graphrag_result
    if result and result.request.query_plan:
        return len(result.request.query_plan.preferred_queries)
    return 0


def _run_evidence_ranking(context: RetrievalContext, policy: RetrievalPolicy) -> None:
    if not context.merged_evidence:
        return
    try:
        ranker_policy = EvidenceRankerPolicy(
            max_total_evidence=policy.max_ranked_evidence,
            max_grep_evidence=policy.max_grep_evidence,
            max_graph_evidence=policy.max_graph_evidence,
            max_graphrag_evidence=policy.max_graphrag_evidence,
            max_rag_evidence=policy.max_rag_evidence,
            max_total_chars=policy.max_evidence_prompt_chars,
            prefer_graphrag_over_plain_rag=policy.prefer_graphrag_over_rag,
        )
        result = rank_and_select_evidence(
            context.merged_evidence,
            issue_codes=[issue.code for issue in context.issues],
            schema_paths=[issue.schema_path for issue in context.issues if issue.schema_path],
            concept_ids=[issue.concept_id for issue in context.issues if issue.concept_id],
            policy=ranker_policy,
        )
        context.evidence_ranking_result = result
        context.ranked_evidence = result.selected
        context.warnings.extend(f"ranking: {warning}" for warning in result.warnings)
        if result.warnings and not result.selected:
            context.ranked_evidence = context.merged_evidence
    except Exception as exc:  # pragma: no cover - defensive integration path
        context.warnings.append(f"evidence ranking failed: {exc}")
        context.ranked_evidence = context.merged_evidence


def _format_evidence_safety_constraint() -> str:
    return (
        "\n[Evidence Safety Constraints]\n"
        "- Use retrieval evidence as context for code locations, API usage, and documentation interpretation.\n"
        "- Do not use evidence to invent material density, composition, nuclear-data paths, "
        "benchmark constants, or missing loading maps.\n"
        "- If the input marks a fact as missing or requiring expert confirmation, preserve human confirmation.\n"
    )


def _run_grep_stage(
    context: RetrievalContext,
    decisions: list[RetrievalTriggerDecision],
    policy: RetrievalPolicy,
) -> None:
    for issue, decision in zip(context.issues, decisions):
        if not decision.should_run_grep:
            continue
        try:
            request = grep_request_from_issue(issue)
            context.grep_requests.append(request)
            result = grep_search(request)
            context.grep_results.append(result)
            context.warnings.extend(f"grep {issue.code}: {warning}" for warning in result.warnings)
            context.grep_evidence.extend(grep_result_to_evidence(result))
        except Exception as exc:  # pragma: no cover - exercised via monkeypatch
            context.warnings.append(f"grep failed for {issue.code}: {exc}")
        if len(context.grep_evidence) >= policy.max_grep_evidence:
            context.grep_evidence = context.grep_evidence[: policy.max_grep_evidence]
            break


def _run_graph_stage(
    context: RetrievalContext,
    decisions: list[RetrievalTriggerDecision],
    policy: RetrievalPolicy,
) -> None:
    if not any(decision.should_run_graph for decision in decisions) and not context.grep_evidence:
        context.skipped_steps.append("graph skipped: no issue or grep anchors")
        return
    try:
        context.graph_request = graph_request_from_issues(context.issues, context.grep_evidence)
        context.graph_context = graph_lookup(context.graph_request)
        if context.graph_context.warnings:
            context.warnings.extend(
                f"graph: {warning}" for warning in context.graph_context.warnings
            )
        context.graph_evidence = graph_context_to_evidence(context.graph_context)[
            : policy.max_graph_evidence
        ]
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        context.graph_context = GraphContext()
        context.warnings.append(f"graph failed: {exc}")


def _run_graphrag_stage(
    context: RetrievalContext,
    decisions: list[RetrievalTriggerDecision],
    policy: RetrievalPolicy,
) -> None:
    graph_context = context.graph_context or GraphContext()
    graph_has_hints = bool(
        graph_context.related_doc_refs
        or graph_context.related_api_refs
        or graph_context.related_example_refs
        or graph_context.retrieval_hints
    )
    should_run = any(decision.should_run_rag for decision in decisions) or graph_has_hints
    if policy.skip_rag_for_fact_gap and all(_is_fact_gap_issue(issue) for issue in context.issues):
        should_run = False
    if not should_run:
        context.skipped_steps.append("graphrag skipped: no graph-guided document trigger")
        return

    try:
        request = graphrag_request_from_issues(
            context.issues,
            grep_evidence=context.grep_evidence,
            graph_context=context.graph_context,
            use_query_planner=policy.enable_graphrag_query_planner,
        ).model_copy(update={"top_k_chunks": policy.max_graphrag_evidence})
        if request.query_plan is not None and policy.max_planned_graph_paths >= 0:
            request = request.model_copy(
                update={
                    "query_plan": request.query_plan.model_copy(
                        update={
                            "planned_paths": request.query_plan.planned_paths[
                                : policy.max_planned_graph_paths
                            ]
                        }
                    )
                }
            )
        elif not policy.enable_graphrag_query_planner:
            context.skipped_steps.append("graphrag query planner disabled by policy")
        context.graphrag_request = request
        result = graphrag_retrieve(request)
        context.graphrag_result = result
        context.graphrag_request = result.request
        context.warnings.extend(f"graphrag: {warning}" for warning in result.warnings)
        context.graphrag_evidence = result.evidence[: policy.max_graphrag_evidence]
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        context.warnings.append(f"graphrag failed: {exc}")


def _run_rag_stage(
    context: RetrievalContext,
    decisions: list[RetrievalTriggerDecision],
    policy: RetrievalPolicy,
) -> None:
    graph_context = context.graph_context or GraphContext()
    graph_has_hints = bool(
        graph_context.related_doc_refs
        or graph_context.related_api_refs
        or graph_context.retrieval_hints
    )
    should_run = any(decision.should_run_rag for decision in decisions) or graph_has_hints
    if policy.skip_rag_for_fact_gap and all(_is_fact_gap_issue(issue) for issue in context.issues):
        should_run = False
    if not should_run:
        context.skipped_steps.append("rag skipped: no document retrieval trigger")
        return

    try:
        request = rag_request_from_graph_context(graph_context, context.issues)
        request = request.model_copy(update={"top_k": policy.max_rag_evidence})
        context.rag_request = request
        result = rag_search(request)
        context.rag_result = result
        context.warnings.extend(f"rag: {warning}" for warning in result.warnings)
        context.rag_evidence = rag_result_to_evidence(result)[: policy.max_rag_evidence]
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        context.warnings.append(f"rag failed: {exc}")


def _is_fact_gap_issue(issue: ValidationIssue) -> bool:
    if issue.requires_human_confirmation:
        return True
    code = issue.code.lower()
    concept = (issue.concept_id or "").lower()
    message = issue.message.lower()
    return any(token in code or token in concept or token in message for token in _FACT_GAP_TOKENS)


def retrieval_context_from_raw(raw: Any) -> RetrievalContext:
    """Coerce persisted state back into a RetrievalContext."""
    if isinstance(raw, RetrievalContext):
        return raw
    if isinstance(raw, dict):
        try:
            return RetrievalContext.model_validate(raw)
        except Exception:
            return RetrievalContext()
    return RetrievalContext()
