"""Lightweight issue/schema/concept relationship graph.

The graph layer is intentionally small and local: it expands structured
diagnostics and grep evidence into maintained relationships, but it does not
retrieve documents, infer physics facts, or modify plans.
"""

from __future__ import annotations

from collections import deque
import re
from typing import Any, Literal

from pydantic import Field

from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.schemas import AgentBaseModel, ValidationIssue


GraphNodeType = Literal[
    "schema_model",
    "schema_field",
    "validation_rule",
    "validation_issue",
    "openmc_concept",
    "openmc_api",
    "doc_ref",
    "example_ref",
    "renderer_capability",
    "runtime_error",
    "repair_policy",
]

GraphRelation = Literal[
    "represents",
    "validated_by",
    "raises",
    "related_to",
    "documented_in",
    "implemented_by",
    "demonstrated_by",
    "supports",
    "downgrades_to",
    "routes_to",
    "repairs_with",
    "mentions",
    "aliases",
]


class GraphNode(AgentBaseModel):
    id: str
    type: GraphNodeType
    title: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(AgentBaseModel):
    source: str
    target: str
    relation: GraphRelation
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphContext(AgentBaseModel):
    start_nodes: list[str] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    related_schema_paths: list[str] = Field(default_factory=list)
    related_concept_ids: list[str] = Field(default_factory=list)
    related_error_codes: list[str] = Field(default_factory=list)
    related_doc_refs: list[str] = Field(default_factory=list)
    related_api_refs: list[str] = Field(default_factory=list)
    related_example_refs: list[str] = Field(default_factory=list)
    repair_policies: list[str] = Field(default_factory=list)
    retrieval_hints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GraphLookupRequest(AgentBaseModel):
    issue_codes: list[str] = Field(default_factory=list)
    schema_paths: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    grep_patterns: list[str] = Field(default_factory=list)
    evidence_locators: list[str] = Field(default_factory=list)
    max_depth: int = 2
    max_nodes: int = 50


def graph_lookup(
    request: GraphLookupRequest,
    *,
    extra_nodes: list[GraphNode] | None = None,
    extra_edges: list[GraphEdge] | None = None,
) -> GraphContext:
    """Expand issue/schema/concept/evidence anchors with bounded BFS."""
    from openmc_agent.knowledge_graph_registry import GRAPH_EDGES, GRAPH_NODES

    max_depth = max(0, min(request.max_depth, 4))
    max_nodes = max(1, min(request.max_nodes, 200))
    nodes_by_id = dict(GRAPH_NODES)
    for node in extra_nodes or []:
        nodes_by_id.setdefault(node.id, node)
    edges = [*GRAPH_EDGES, *(extra_edges or [])]
    alias_index = _build_alias_index(nodes_by_id)
    adjacency = _build_adjacency(edges)

    starts: list[str] = []
    warnings: list[str] = []
    for code in request.issue_codes:
        starts.extend(_resolve_issue_code(code, nodes_by_id))
    for schema_path in request.schema_paths:
        starts.extend(_resolve_schema_path(schema_path, nodes_by_id, alias_index))
    for concept_id in request.concept_ids:
        starts.extend(_resolve_concept_id(concept_id, nodes_by_id))
    for pattern in request.grep_patterns:
        starts.extend(_resolve_alias(pattern, alias_index))
    for locator in request.evidence_locators:
        starts.extend(_resolve_evidence_locator(locator, alias_index))

    starts = _dedupe(starts)
    if not starts:
        warnings.append("no graph start nodes matched the lookup request")

    visited: set[str] = set()
    selected_edges: list[GraphEdge] = []
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in starts if node_id in nodes_by_id)
    while queue and len(visited) < max_nodes:
        node_id, depth = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        if depth >= max_depth:
            continue
        for edge, neighbor in adjacency.get(node_id, []):
            if edge not in selected_edges:
                selected_edges.append(edge)
            if neighbor not in visited and len(visited) + len(queue) < max_nodes:
                queue.append((neighbor, depth + 1))

    nodes = [nodes_by_id[node_id] for node_id in starts if node_id in visited]
    nodes.extend(
        nodes_by_id[node_id]
        for node_id in visited
        if node_id not in starts and node_id in nodes_by_id
    )
    return _context_from_graph(
        start_nodes=[node_id for node_id in starts if node_id in nodes_by_id],
        nodes=nodes[:max_nodes],
        edges=selected_edges,
        warnings=warnings,
    )


def graph_request_from_issues(
    issues: list[ValidationIssue],
    evidence: list[RetrievedEvidence] | None = None,
    *,
    max_depth: int = 2,
    max_nodes: int = 50,
) -> GraphLookupRequest:
    """Build a graph lookup request from structured issues and grep evidence."""
    schema_paths: list[str] = []
    concept_ids: list[str] = []
    issue_codes: list[str] = []
    grep_patterns: list[str] = []
    locators: list[str] = []
    for issue in issues:
        issue_codes.append(issue.code)
        if issue.schema_path:
            schema_paths.append(issue.schema_path)
        if issue.concept_id:
            concept_ids.append(issue.concept_id)
        grep_patterns.extend(issue.grep_patterns)
    for item in evidence or []:
        locators.append(item.locator)
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

    return GraphLookupRequest(
        issue_codes=_dedupe(issue_codes),
        schema_paths=_dedupe(schema_paths),
        concept_ids=_dedupe(concept_ids),
        grep_patterns=_dedupe(_filter_graph_patterns(grep_patterns)),
        evidence_locators=_dedupe(locators),
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


def gather_graph_context_for_issues(
    issues: list[ValidationIssue],
    evidence: list[RetrievedEvidence] | None = None,
) -> GraphContext:
    if not issues and not evidence:
        return GraphContext()
    return graph_lookup(graph_request_from_issues(issues, evidence))


def graph_context_to_evidence(context: GraphContext) -> list[RetrievedEvidence]:
    """Convert important graph nodes and edges into compact evidence records."""
    evidence: list[RetrievedEvidence] = []
    for node in context.nodes[:20]:
        if node.type not in {
            "validation_issue",
            "schema_field",
            "openmc_concept",
            "openmc_api",
            "doc_ref",
            "repair_policy",
            "renderer_capability",
            "runtime_error",
        }:
            continue
        text = _truncate(
            f"{node.title}: {node.description}".strip(": "),
            360,
        )
        evidence.append(
            RetrievedEvidence(
                source_type="graph",
                locator=node.id,
                text=text,
                issue_code=node.metadata.get("error_code"),
                schema_path=node.metadata.get("schema_path"),
                concept_id=node.metadata.get("concept_id"),
                metadata={
                    "node_type": node.type,
                    "related_schema_paths": context.related_schema_paths,
                    "related_concept_ids": context.related_concept_ids,
                    "related_error_codes": context.related_error_codes,
                    "doc_refs": context.related_doc_refs,
                    "api_refs": context.related_api_refs,
                },
            )
        )
    for edge in context.edges[:20]:
        if edge.relation not in {"routes_to", "repairs_with", "downgrades_to", "documented_in"}:
            continue
        evidence.append(
            RetrievedEvidence(
                source_type="graph",
                locator=f"{edge.source} -[{edge.relation}]-> {edge.target}",
                text=f"{edge.source} {edge.relation} {edge.target}",
                metadata={
                    "relation": edge.relation,
                    "related_schema_paths": context.related_schema_paths,
                    "related_concept_ids": context.related_concept_ids,
                    "related_error_codes": context.related_error_codes,
                },
            )
        )
    return evidence


def format_graph_context(context: GraphContext, *, limit: int = 12) -> str:
    """Render a compact GraphContext block for reflection prompts."""
    if not (
        context.nodes
        or context.warnings
        or context.start_nodes
        or context.related_schema_paths
        or context.related_concept_ids
        or context.related_error_codes
        or context.related_doc_refs
        or context.related_api_refs
        or context.related_example_refs
        or context.repair_policies
        or context.retrieval_hints
    ):
        return ""
    lines = [
        "\n[Graph Context]",
        "Graph context is maintained relationship metadata; it is not a final physics fact.",
    ]
    if context.start_nodes:
        lines.append(f"- start_nodes: {', '.join(context.start_nodes[:limit])}")
    if context.related_schema_paths:
        lines.append(f"- related_schema_paths: {', '.join(context.related_schema_paths[:limit])}")
    if context.related_concept_ids:
        lines.append(f"- related_concepts: {', '.join(context.related_concept_ids[:limit])}")
    if context.related_error_codes:
        lines.append(f"- related_error_codes: {', '.join(context.related_error_codes[:limit])}")
    if context.related_api_refs:
        lines.append(f"- related_api_refs: {', '.join(context.related_api_refs[:limit])}")
    if context.related_doc_refs:
        lines.append(f"- related_doc_refs: {', '.join(context.related_doc_refs[:limit])}")
    if context.related_example_refs:
        lines.append(f"- related_example_refs: {', '.join(context.related_example_refs[:limit])}")
    if context.repair_policies:
        lines.append(f"- repair_policies: {', '.join(context.repair_policies[:limit])}")
    if context.retrieval_hints:
        lines.append(f"- retrieval_hints: {', '.join(context.retrieval_hints[:limit])}")
    if context.warnings:
        lines.append(f"- warnings: {', '.join(context.warnings[:4])}")
    return "\n".join(lines) + "\n"


def _context_from_graph(
    *,
    start_nodes: list[str],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    warnings: list[str],
) -> GraphContext:
    schema_paths: list[str] = []
    concept_ids: list[str] = []
    error_codes: list[str] = []
    doc_refs: list[str] = []
    api_refs: list[str] = []
    example_refs: list[str] = []
    policies: list[str] = []
    hints: list[str] = []
    for node in nodes:
        if node.type == "schema_field":
            schema_paths.append(node.metadata.get("schema_path") or node.id.removeprefix("schema."))
        if node.type in {"openmc_concept", "renderer_capability"}:
            concept_ids.append(node.metadata.get("concept_id") or node.id.removeprefix("concept."))
        if node.type in {"validation_issue", "runtime_error"}:
            code = node.metadata.get("error_code") or node.id.removeprefix("issue.")
            error_codes.append(code)
        if node.type == "doc_ref":
            doc_refs.append(node.metadata.get("ref_id") or node.id.removeprefix("doc."))
            doc_refs.extend(_list_metadata(node, "doc_refs"))
            api_refs.extend(_list_metadata(node, "api_refs"))
            concept_ids.extend(_list_metadata(node, "concept_ids"))
            schema_paths.extend(_list_metadata(node, "schema_paths"))
            error_codes.extend(_list_metadata(node, "issue_codes"))
        if node.type == "openmc_api":
            api_refs.append(node.metadata.get("api_ref") or node.id.removeprefix("api."))
        if node.type == "example_ref":
            example_refs.append(node.metadata.get("ref_id") or node.id.removeprefix("example."))
        if node.type == "repair_policy":
            policies.append(node.metadata.get("policy") or node.id.removeprefix("repair."))
        hints.extend(_list_metadata(node, "retrieval_hints"))
    return GraphContext(
        start_nodes=start_nodes,
        nodes=nodes,
        edges=edges,
        related_schema_paths=_dedupe(schema_paths),
        related_concept_ids=_dedupe(concept_ids),
        related_error_codes=_dedupe(error_codes),
        related_doc_refs=_dedupe(doc_refs),
        related_api_refs=_dedupe(api_refs),
        related_example_refs=_dedupe(example_refs),
        repair_policies=_dedupe(policies),
        retrieval_hints=_dedupe(hints),
        warnings=warnings,
    )


def _build_alias_index(nodes: dict[str, GraphNode]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for node in nodes.values():
        aliases = [node.id, node.title, *node.aliases]
        for key in aliases:
            normalized = _normalize_alias(key)
            if not normalized:
                continue
            index.setdefault(normalized, []).append(node.id)
        if node.type == "schema_field":
            schema_path = node.metadata.get("schema_path")
            if isinstance(schema_path, str):
                for token in _schema_aliases(schema_path):
                    index.setdefault(_normalize_alias(token), []).append(node.id)
    return {key: _dedupe(value) for key, value in index.items()}


def _build_adjacency(edges: list[GraphEdge]) -> dict[str, list[tuple[GraphEdge, str]]]:
    adjacency: dict[str, list[tuple[GraphEdge, str]]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append((edge, edge.target))
        adjacency.setdefault(edge.target, []).append((edge, edge.source))
    return adjacency


def _resolve_issue_code(code: str, nodes: dict[str, GraphNode]) -> list[str]:
    candidates = [f"issue.{code}", f"runtime.{code}", code]
    return [candidate for candidate in candidates if candidate in nodes]


def _resolve_concept_id(concept_id: str, nodes: dict[str, GraphNode]) -> list[str]:
    candidates = [f"concept.{concept_id}", concept_id]
    return [candidate for candidate in candidates if candidate in nodes]


def _resolve_schema_path(
    schema_path: str,
    nodes: dict[str, GraphNode],
    alias_index: dict[str, list[str]],
) -> list[str]:
    direct = schema_path if schema_path.startswith("schema.") else f"schema.{schema_path}"
    if direct in nodes:
        return [direct]
    aliases = _schema_aliases(schema_path)
    resolved: list[str] = []
    for alias in aliases:
        resolved.extend(_resolve_alias(alias, alias_index))
    return [node_id for node_id in _dedupe(resolved) if node_id.startswith("schema.")]


def _resolve_alias(pattern: str, alias_index: dict[str, list[str]]) -> list[str]:
    normalized = _normalize_alias(pattern)
    if not normalized:
        return []
    if normalized in alias_index:
        return alias_index[normalized]
    # Allow field-name matches like "outer_universe_id" without expanding every
    # noisy text token into the prompt.
    if "." not in normalized and len(normalized) >= 4:
        matches: list[str] = []
        for alias, node_ids in alias_index.items():
            if alias.endswith(f".{normalized}") or alias == normalized:
                matches.extend(node_ids)
        return _dedupe(matches)
    return []


def _resolve_evidence_locator(locator: str, alias_index: dict[str, list[str]]) -> list[str]:
    normalized = locator.replace("\\", "/")
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", normalized)
    resolved: list[str] = []
    if "schemas.py" in normalized:
        for token in tokens:
            resolved.extend(_resolve_alias(token, alias_index))
    if any(name in normalized for name in ("validator.py", "tools.py", "error_catalog.py")):
        for token in tokens:
            if "." in token:
                resolved.extend(_resolve_alias(token, alias_index))
    if any(name in normalized for name in ("assembly.py", "core.py", "triso.py", "skeleton.py")):
        resolved.extend(_resolve_alias("renderability", alias_index))
    return _dedupe(resolved)


def _schema_aliases(schema_path: str) -> list[str]:
    clean = re.sub(r"\[\d+\]", "", schema_path)
    parts = [part for part in clean.split(".") if part]
    aliases = [clean, clean.removeprefix("schema.")]
    model_by_field = {
        "fuel_radius_cm": "GeometrySpec",
        "pitch_cm": "GeometrySpec",
        "clad_inner_radius_cm": "GeometrySpec",
        "clad_outer_radius_cm": "GeometrySpec",
        "fill_type": "CellSpec",
        "fill_id": "CellSpec",
        "cell_ids": "UniverseSpec",
        "universe_pattern": "LatticeSpec",
        "rings": "LatticeSpec",
        "outer_universe_id": "LatticeSpec",
        "batches": "RunSettingsSpec",
        "inactive": "RunSettingsSpec",
        "particles": "RunSettingsSpec",
        "energy_mode": "RunSettingsSpec",
        "seed": "RunSettingsSpec",
        "density_unit": "MaterialSpec",
        "density_value": "MaterialSpec",
        "composition": "MaterialSpec",
        "sab": "MaterialSpec",
        "chemical_formula": "MaterialSpec",
        "macroscopic": "ComplexMaterialSpec",
        "renderability": "RenderCapabilityReport",
        "supported_renderer": "RenderCapabilityReport",
        "unsupported_subsystems": "RenderCapabilityReport",
        "required_human_confirmations": "RenderCapabilityReport",
    }
    if parts:
        tail = parts[-1]
        aliases.append(tail)
        model = model_by_field.get(tail)
        if model:
            aliases.append(f"{model}.{tail}")
            aliases.append(f"schema.{model}.{tail}")
    for idx, part in enumerate(parts):
        if part and part[0].isupper():
            aliases.append(".".join(parts[idx:]))
    return _dedupe(aliases)


def _filter_graph_patterns(patterns: list[str]) -> list[str]:
    filtered: list[str] = []
    for pattern in patterns:
        candidate = pattern.strip()
        if len(candidate) < 3:
            continue
        if candidate.casefold() in {"error", "warning", "none", "null", "true", "false"}:
            continue
        filtered.append(candidate)
    return filtered[:48]


def _list_metadata(node: GraphNode, key: str) -> list[str]:
    value = node.metadata.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _normalize_alias(value: str) -> str:
    return value.strip().removeprefix("schema.").casefold()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
