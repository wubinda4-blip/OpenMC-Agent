"""Local deterministic GraphRAG retrieval over the maintained knowledge graph.

GraphRAG in this module is intentionally small: structured issues select graph
start nodes, a bounded graph expansion supplies document/API/concept anchors,
and the existing lexical RAG layer retrieves local chunks. It does not call
external services, infer physical facts, or modify simulation plans.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.graphrag_query_planner import (
    GraphRagQueryPlan,
    build_queries_from_plan,
    format_graphrag_query_plan,
    plan_graph_paths,
    plan_graphrag_query,
)
from openmc_agent.knowledge_graph import (
    GraphContext,
    GraphEdge,
    GraphLookupRequest,
    GraphNode,
    graph_lookup,
)
from openmc_agent.rag_search import (
    RagSearchRequest,
    RagSearchResult,
    rag_result_to_evidence,
    rag_search,
)
from openmc_agent.schemas import AgentBaseModel, ValidationIssue


GraphRagTrigger = Literal[
    "validation_issue",
    "runtime_issue",
    "export_xml_issue",
    "hex_lattice_issue",
    "retrieval_context",
    "manual",
]


class GraphRagRequest(AgentBaseModel):
    trigger: GraphRagTrigger
    issue_codes: list[str] = Field(default_factory=list)
    schema_paths: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    grep_patterns: list[str] = Field(default_factory=list)
    graph_start_nodes: list[str] = Field(default_factory=list)
    search_roots: list[str] = Field(default_factory=list)
    max_graph_depth: int = 2
    max_graph_nodes: int = 40
    top_k_chunks: int = 6
    include_examples: bool = True
    include_api_docs: bool = True
    include_project_docs: bool = True
    query_plan: GraphRagQueryPlan | None = None


class GraphRagPath(AgentBaseModel):
    nodes: list[str]
    relations: list[str] = Field(default_factory=list)
    score: float = 1.0
    explanation: str = ""


class GraphRagResult(AgentBaseModel):
    request: GraphRagRequest
    graph_context: GraphContext | None = None
    graph_paths: list[GraphRagPath] = Field(default_factory=list)
    rag_request: RagSearchRequest | None = None
    rag_result: RagSearchResult | None = None
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


_HEX_START_NODES = [
    "concept.openmc.geometry.hex_lattice",
    "schema.LatticeSpec.rings",
    "schema.LatticeSpec.outer_universe_id",
]
_GEOMETRY_OVERLAP_START_NODES = [
    "concept.openmc.geometry.surface",
    "concept.openmc.geometry.region_boolean_expression",
    "concept.openmc.geometry.boundary_type",
]
_CROSS_SECTIONS_START_NODES = [
    "concept.openmc.data.cross_sections",
    "concept.openmc_agent.human_confirmation",
]
_FACT_GAP_TOKENS = ("cross_sections", "missing_nuclide_data", "density", "composition")
_MAX_QUERY_COUNT = 12


def graphrag_request_from_issues(
    issues: list[ValidationIssue],
    grep_evidence: list[RetrievedEvidence] | None = None,
    graph_context: GraphContext | None = None,
    *,
    use_query_planner: bool = True,
) -> GraphRagRequest:
    """Build a bounded GraphRAG request from issues and prior retrieval output."""
    issue_codes: list[str] = []
    schema_paths: list[str] = []
    concept_ids: list[str] = []
    grep_patterns: list[str] = []
    start_nodes: list[str] = []

    for issue in issues:
        issue_codes.append(issue.code)
        if issue.schema_path:
            schema_paths.append(issue.schema_path)
        if issue.concept_id:
            concept_ids.append(issue.concept_id)
        grep_patterns.extend(issue.grep_patterns)
        start_nodes.extend(_start_nodes_for_issue(issue))

    for item in grep_evidence or []:
        if item.issue_code:
            issue_codes.append(item.issue_code)
        if item.schema_path:
            schema_paths.append(item.schema_path)
        if item.concept_id:
            concept_ids.append(item.concept_id)
        matched = item.metadata.get("matched_pattern")
        symbol = item.metadata.get("symbol_hint")
        if isinstance(matched, str):
            grep_patterns.append(matched)
        if isinstance(symbol, str):
            grep_patterns.append(symbol)

    if graph_context is not None:
        start_nodes.extend(graph_context.start_nodes)
        schema_paths.extend(graph_context.related_schema_paths)
        concept_ids.extend(graph_context.related_concept_ids)

    for code in issue_codes:
        start_nodes.append(f"issue.{code}")
    for schema_path in schema_paths:
        start_nodes.append(schema_path if schema_path.startswith("schema.") else f"schema.{schema_path}")
    for concept_id in concept_ids:
        start_nodes.append(
            concept_id if concept_id.startswith("concept.") else f"concept.{concept_id}"
        )

    trigger: GraphRagTrigger = "manual"
    if any(code.startswith("lattice.hex.") for code in issue_codes):
        trigger = "hex_lattice_issue"
    elif any(code.startswith("runtime.") for code in issue_codes):
        trigger = "runtime_issue"
    elif any(code.startswith("export_xml.") for code in issue_codes):
        trigger = "export_xml_issue"
    elif issue_codes:
        trigger = "validation_issue"
    elif graph_context is not None:
        trigger = "retrieval_context"

    query_plan: GraphRagQueryPlan | None = None
    if use_query_planner:
        try:
            query_plan = plan_graphrag_query(issues, graph_context=graph_context)
            start_nodes.extend(query_plan.start_nodes)
        except Exception:
            query_plan = None

    expansion_update: dict[str, object] = {}
    if query_plan is not None:
        expansion_update = {
            "max_graph_depth": query_plan.expansion_policy.max_depth,
            "max_graph_nodes": query_plan.expansion_policy.max_nodes,
            "include_examples": query_plan.expansion_policy.include_examples,
            "include_api_docs": query_plan.expansion_policy.include_api_docs,
        }

    return GraphRagRequest(
        trigger=trigger,
        issue_codes=_dedupe(issue_codes),
        schema_paths=_dedupe(schema_paths),
        concept_ids=_dedupe(concept_ids),
        grep_patterns=_dedupe(_filter_patterns(grep_patterns)),
        graph_start_nodes=_dedupe(start_nodes)[:48],
        query_plan=query_plan,
        **expansion_update,
    )


def expand_graphrag_subgraph(
    request: GraphRagRequest,
    *,
    extra_nodes: list[GraphNode] | None = None,
    extra_edges: list[GraphEdge] | None = None,
) -> GraphContext:
    """Expand graph anchors with the existing graph_lookup bounded BFS."""
    try:
        plan = request.query_plan
        start_nodes = plan.start_nodes if plan and plan.start_nodes else request.graph_start_nodes
        max_depth = (
            plan.expansion_policy.max_depth
            if plan is not None
            else request.max_graph_depth
        )
        max_nodes = (
            plan.expansion_policy.max_nodes
            if plan is not None
            else request.max_graph_nodes
        )
        lookup_request = GraphLookupRequest(
            issue_codes=_dedupe(request.issue_codes),
            schema_paths=_dedupe(request.schema_paths),
            concept_ids=_dedupe(request.concept_ids),
            grep_patterns=_dedupe([*request.grep_patterns, *start_nodes]),
            max_depth=max(0, min(max_depth, 4)),
            max_nodes=max(1, min(max_nodes, 200)),
        )
        context = graph_lookup(
            lookup_request,
            extra_nodes=extra_nodes,
            extra_edges=extra_edges,
        )
        missing = _missing_explicit_start_nodes(start_nodes, context)
        if missing:
            context = context.model_copy(
                update={
                    "warnings": [
                        *context.warnings,
                        f"unmatched GraphRAG start nodes: {', '.join(missing[:8])}",
                    ]
                }
            )
        return context
    except Exception as exc:  # pragma: no cover - defensive integration path
        return GraphContext(warnings=[f"GraphRAG graph expansion failed: {exc}"])


def rag_request_from_graphrag_context(
    graph_context: GraphContext,
    request: GraphRagRequest,
) -> RagSearchRequest:
    """Translate a GraphRAG subgraph into a graph-guided lexical RAG request."""
    concept_ids = _dedupe([*graph_context.related_concept_ids, *request.concept_ids])
    schema_paths = _dedupe([*graph_context.related_schema_paths, *request.schema_paths])
    queries: list[str] = []
    required_filters: dict[str, list[str]] = {}
    avoided_queries: list[str] = []
    if request.query_plan is not None:
        queries.extend(request.query_plan.preferred_queries)
        required_filters = request.query_plan.required_filters
        avoided_queries = request.query_plan.avoided_queries
    queries.extend(graph_context.retrieval_hints)
    queries.extend(_safe_grep_queries(request))
    queries.extend(_concept_tail_queries(concept_ids))
    queries.extend(_issue_code_queries(request.issue_codes))
    ingested_paths = _ingested_chunk_paths(graph_context)
    concept_ids = _dedupe([*concept_ids, *required_filters.get("concept_ids", [])])
    schema_paths = _dedupe([*schema_paths, *required_filters.get("schema_paths", [])])
    doc_refs = _dedupe([*graph_context.related_doc_refs, *required_filters.get("doc_refs", [])])
    api_refs = _dedupe([*graph_context.related_api_refs, *required_filters.get("api_refs", [])])
    queries = _remove_avoided_queries(queries, avoided_queries)

    source_types: list[str] = []
    if not (request.include_project_docs and request.include_api_docs and request.include_examples):
        if request.include_project_docs:
            source_types.append("project_doc")
        if request.include_api_docs:
            source_types.extend(["openmc_doc", "openmc_api_doc"])
        if request.include_examples:
            source_types.extend(["project_example", "openmc_example"])
        source_types.append("unknown")

    return RagSearchRequest(
        trigger=_rag_trigger_for_graphrag(request),
        issue_codes=_dedupe(request.issue_codes),
        schema_paths=schema_paths,
        concept_ids=concept_ids,
        doc_refs=doc_refs,
        api_refs=api_refs,
        example_refs=_dedupe(graph_context.related_example_refs),
        queries=_dedupe(_filter_queries(queries))[:_MAX_QUERY_COUNT],
        search_roots=request.search_roots or ingested_paths,
        source_types=_dedupe(source_types),
        top_k=request.top_k_chunks,
    )


def graphrag_retrieve(
    request: GraphRagRequest,
    *,
    extra_nodes: list[GraphNode] | None = None,
    extra_edges: list[GraphEdge] | None = None,
) -> GraphRagResult:
    """Run graph expansion followed by graph-guided local RAG retrieval."""
    warnings: list[str] = []
    graph_context = expand_graphrag_subgraph(
        request,
        extra_nodes=extra_nodes,
        extra_edges=extra_edges,
    )
    warnings.extend(graph_context.warnings)
    if request.query_plan is not None:
        try:
            planned_paths = plan_graph_paths(
                graph_context,
                request.query_plan.intent,
                request.query_plan.expansion_policy,
                max_paths=8,
            )
            preferred_queries, filters, avoided = build_queries_from_plan(
                request.query_plan.intent,
                planned_paths,
                graph_context,
            )
            request = request.model_copy(
                update={
                    "query_plan": request.query_plan.model_copy(
                        update={
                            "planned_paths": planned_paths,
                            "preferred_queries": preferred_queries,
                            "required_filters": filters,
                            "avoided_queries": avoided,
                        }
                    )
                }
            )
        except Exception as exc:
            warnings.append(f"GraphRAG query plan path update failed: {exc}")
    graph_paths = extract_graphrag_paths(graph_context)

    rag_request: RagSearchRequest | None = None
    rag_result: RagSearchResult | None = None
    evidence: list[RetrievedEvidence] = []
    try:
        rag_request = rag_request_from_graphrag_context(graph_context, request)
        rag_result = rag_search(rag_request)
        warnings.extend(rag_result.warnings)
        base_result = GraphRagResult(
            request=request,
            graph_context=graph_context,
            graph_paths=graph_paths,
            rag_request=rag_request,
            rag_result=rag_result,
            warnings=warnings,
        )
        evidence = graphrag_result_to_evidence(base_result)
    except Exception as exc:  # pragma: no cover - defensive integration path
        warnings.append(f"GraphRAG RAG retrieval failed: {exc}")

    return GraphRagResult(
        request=request,
        graph_context=graph_context,
        graph_paths=graph_paths,
        rag_request=rag_request,
        rag_result=rag_result,
        evidence=evidence,
        warnings=warnings,
    )


def extract_graphrag_paths(
    graph_context: GraphContext,
    max_paths: int = 6,
) -> list[GraphRagPath]:
    """Extract short explainable graph paths for prompt display."""
    if not graph_context.nodes and not graph_context.edges:
        return []
    paths: list[GraphRagPath] = []
    node_ids = {node.id for node in graph_context.nodes}
    start_nodes = [node for node in graph_context.start_nodes if node in node_ids]
    edges_by_source: dict[str, list[GraphEdge]] = {}
    for edge in graph_context.edges:
        edges_by_source.setdefault(edge.source, []).append(edge)
        edges_by_source.setdefault(edge.target, []).append(
            GraphEdge(
                source=edge.target,
                target=edge.source,
                relation=edge.relation,
                weight=edge.weight,
                metadata=edge.metadata,
            )
        )

    preferred_targets = ("doc.", "api.", "example.", "repair.", "concept.", "schema.")
    for start in start_nodes:
        for edge in edges_by_source.get(start, []):
            if edge.target not in node_ids:
                continue
            if edge.target.startswith(preferred_targets):
                paths.append(
                    GraphRagPath(
                        nodes=[start, edge.target],
                        relations=[edge.relation],
                        score=edge.weight,
                        explanation=f"{_display_node(start)} -> {_display_node(edge.target)}",
                    )
                )
            for second in edges_by_source.get(edge.target, []):
                if second.target == start or second.target not in node_ids:
                    continue
                if second.target.startswith(("doc.", "api.", "example.", "repair.")):
                    paths.append(
                        GraphRagPath(
                            nodes=[start, edge.target, second.target],
                            relations=[edge.relation, second.relation],
                            score=(edge.weight + second.weight) / 2,
                            explanation=(
                                f"{_display_node(start)} -> {_display_node(edge.target)} "
                                f"-> {_display_node(second.target)}"
                            ),
                        )
                    )
            if len(paths) >= max_paths:
                return _dedupe_paths(paths)[:max_paths]
    return _dedupe_paths(paths)[:max_paths]


def graphrag_result_to_evidence(result: GraphRagResult) -> list[RetrievedEvidence]:
    """Convert GraphRAG retrieved chunks into graph-attributed evidence."""
    if result.rag_result is None:
        return []
    graph_context = result.graph_context or GraphContext()
    base_evidence = rag_result_to_evidence(result.rag_result)
    graph_paths = [
        {
            "nodes": path.nodes,
            "relations": path.relations,
            "score": path.score,
            "explanation": path.explanation,
        }
        for path in result.graph_paths
    ]
    converted: list[RetrievedEvidence] = []
    for item in base_evidence:
        metadata = dict(item.metadata)
        metadata.update(
            {
                "retrieval_mode": "graphrag",
                "graph_start_nodes": result.request.graph_start_nodes,
                "graph_paths": graph_paths,
                "related_concept_ids": graph_context.related_concept_ids,
                "related_doc_refs": graph_context.related_doc_refs,
                "related_api_refs": graph_context.related_api_refs,
                "issue_codes": result.request.issue_codes,
                "query_plan_intent": (
                    result.request.query_plan.intent.intent_type
                    if result.request.query_plan
                    else None
                ),
                "planned_graph_paths": (
                    [path.model_dump(mode="json") for path in result.request.query_plan.planned_paths]
                    if result.request.query_plan
                    else []
                ),
                "fact_gap_safe_mode": (
                    result.request.query_plan.expansion_policy.fact_gap_safe_mode
                    if result.request.query_plan
                    else False
                ),
                **_ingested_metadata_for_evidence(item, graph_context),
                "requires_human_confirmation": (
                    metadata.get("requires_human_confirmation")
                    or _is_fact_gap_request(result.request)
                    or "ask_expert" in graph_context.repair_policies
                ),
            }
        )
        converted.append(
            item.model_copy(
                update={
                    "source_type": "graphrag",
                    "text": _truncate_text(item.text, result.rag_result.request.max_chunk_chars),
                    "metadata": metadata,
                }
            )
        )
    return _dedupe_evidence(converted)


def format_graphrag_query_plan_section(request: GraphRagRequest | None) -> str:
    """Render a compact GraphRAG query plan section for prompts."""
    return format_graphrag_query_plan(request.query_plan if request else None)


def format_graphrag_evidence(evidence: list[RetrievedEvidence], *, limit: int = 6) -> str:
    """Render GraphRAG evidence for reflection prompts."""
    if not evidence:
        return ""
    lines = [
        "\n[GraphRAG Evidence]",
        (
            "GraphRAG evidence is graph-guided local documentation context only; "
            "do not use it to invent material densities, compositions, nuclear data "
            "paths, or benchmark constants. Fact gaps still require human confirmation."
        ),
    ]
    for item in evidence[:limit]:
        graph_paths = item.metadata.get("graph_paths") or []
        if graph_paths:
            first_path = graph_paths[0]
            explanation = first_path.get("explanation") if isinstance(first_path, dict) else None
            if explanation:
                lines.append("- graph path:")
                lines.append(f"  {explanation}")
        else:
            lines.append("- graph path: unavailable")
        lines.append(f"  source: {item.locator}")
        concepts = item.metadata.get("related_concept_ids") or item.metadata.get("concept_ids") or []
        doc_refs = item.metadata.get("related_doc_refs") or item.metadata.get("doc_refs") or []
        api_refs = item.metadata.get("related_api_refs") or item.metadata.get("api_refs") or []
        if concepts:
            lines.append(f"  concepts: {', '.join(concepts[:4])}")
        if doc_refs:
            lines.append(f"  doc_refs: {', '.join(doc_refs[:4])}")
        if api_refs:
            lines.append(f"  api_refs: {', '.join(api_refs[:4])}")
        lines.append("  text:")
        lines.extend(f"    {line}" for line in item.text.rstrip().splitlines()[:10])
    return "\n".join(lines) + "\n"


def _start_nodes_for_issue(issue: ValidationIssue) -> list[str]:
    nodes: list[str] = []
    code = issue.code
    if code.startswith("lattice.hex."):
        nodes.extend(_HEX_START_NODES)
    if code == "runtime.geometry_overlap":
        nodes.extend(_GEOMETRY_OVERLAP_START_NODES)
    if code.startswith("runtime.cross_sections"):
        nodes.extend(_CROSS_SECTIONS_START_NODES)
    if issue.requires_human_confirmation:
        nodes.append("concept.openmc_agent.human_confirmation")
    return nodes


def _missing_explicit_start_nodes(start_nodes: list[str], context: GraphContext) -> list[str]:
    found = {node.id for node in context.nodes} | set(context.start_nodes)
    missing: list[str] = []
    for node in start_nodes:
        if node.startswith(("issue.", "schema.", "concept.", "api.", "doc.", "example.", "repair.")):
            if node not in found:
                missing.append(node)
    return missing


def _ingested_chunk_paths(graph_context: GraphContext) -> list[str]:
    paths: list[str] = []
    for node in graph_context.nodes:
        if node.metadata.get("node_subtype") != "doc_chunk":
            continue
        path = node.metadata.get("path")
        if isinstance(path, str) and path:
            paths.append(path)
    return _dedupe(paths)[:12]


def _ingested_metadata_for_evidence(
    evidence: RetrievedEvidence,
    graph_context: GraphContext,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    locator_path = evidence.locator.split(":", 1)[0].split(" (", 1)[0]
    matching_nodes = [
        node
        for node in graph_context.nodes
        if node.metadata.get("node_subtype") == "doc_chunk"
        and node.metadata.get("path") == locator_path
    ]
    if not matching_nodes:
        matching_nodes = [
            node
            for node in graph_context.nodes
            if node.metadata.get("node_subtype") == "doc_chunk"
        ]
    if matching_nodes:
        node = matching_nodes[0]
        metadata["knowledge_source"] = node.metadata.get("knowledge_source")
        metadata["doc_chunk_id"] = node.metadata.get("chunk_id")
        metadata["annotation_method"] = node.metadata.get("annotation_method")
        metadata["ingested_graph_node_id"] = node.id
    return metadata


def _safe_grep_queries(request: GraphRagRequest) -> list[str]:
    if _is_fact_gap_request(request):
        return [
            pattern
            for pattern in request.grep_patterns
            if "cross_sections" not in pattern.lower() and "/" not in pattern
        ][:3]
    return request.grep_patterns[:6]


def _remove_avoided_queries(queries: list[str], avoided_queries: list[str]) -> list[str]:
    if not avoided_queries:
        return queries
    avoided_tokens = [
        token
        for query in avoided_queries
        for token in re.split(r"[^A-Za-z0-9_]+", query.casefold())
        if len(token) >= 5
    ]
    filtered: list[str] = []
    for query in queries:
        lowered = query.casefold()
        if any(token in lowered for token in avoided_tokens):
            continue
        filtered.append(query)
    return filtered


def _concept_tail_queries(concept_ids: list[str]) -> list[str]:
    queries: list[str] = []
    for concept in concept_ids:
        tail = concept.removeprefix("concept.").split(".")[-1]
        if len(tail) >= 3:
            queries.append(tail.replace("_", " "))
    return queries


def _issue_code_queries(issue_codes: list[str]) -> list[str]:
    queries: list[str] = []
    for code in issue_codes:
        if code.startswith("runtime.cross_sections"):
            queries.append("OpenMC cross_sections.xml configuration human confirmation")
            continue
        tokens = [
            token
            for token in re.split(r"[^A-Za-z0-9_]+", code)
            if len(token) >= 3 and token not in {"runtime", "export_xml", "lattice", "hex"}
        ]
        if tokens:
            queries.append(" ".join(tokens))
    return queries


def _rag_trigger_for_graphrag(request: GraphRagRequest) -> Literal[
    "validation_issue",
    "runtime_issue",
    "export_xml_issue",
    "hex_lattice_issue",
    "graph_context",
    "manual",
]:
    if request.trigger == "retrieval_context":
        return "graph_context"
    if request.trigger in {"validation_issue", "runtime_issue", "export_xml_issue", "hex_lattice_issue", "manual"}:
        return request.trigger
    return "graph_context"


def _filter_patterns(patterns: list[str]) -> list[str]:
    filtered: list[str] = []
    for pattern in patterns:
        cleaned = " ".join(str(pattern).split())
        if len(cleaned) < 3:
            continue
        if cleaned.casefold() in {"error", "warning", "none", "null", "true", "false"}:
            continue
        filtered.append(cleaned)
    return filtered[:24]


def _filter_queries(queries: list[str]) -> list[str]:
    filtered: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query).split())
        if len(cleaned) < 3:
            continue
        filtered.append(cleaned)
    return filtered


def _is_fact_gap_request(request: GraphRagRequest) -> bool:
    haystack = " ".join([*request.issue_codes, *request.concept_ids, *request.graph_start_nodes]).lower()
    return any(token in haystack for token in _FACT_GAP_TOKENS)


def _dedupe_paths(paths: list[GraphRagPath]) -> list[GraphRagPath]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[GraphRagPath] = []
    for path in paths:
        key = tuple(path.nodes)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _dedupe_evidence(evidence: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    seen: set[str] = set()
    deduped: list[RetrievedEvidence] = []
    for item in evidence:
        key = re.sub(r":\d+-\d+", "", item.locator)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _display_node(node_id: str) -> str:
    for prefix in ("concept.", "doc.", "api.", "example."):
        if node_id.startswith(prefix):
            return node_id.removeprefix(prefix)
    return node_id


def _truncate_text(text: str, max_chars: int) -> str:
    limit = max(300, min(max_chars, 2000))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + "\n...[truncated]"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped
