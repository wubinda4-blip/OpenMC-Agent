"""Deterministic GraphRAG query planning and graph path reranking."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from openmc_agent.knowledge_graph import GraphContext, GraphEdge
from openmc_agent.schemas import AgentBaseModel, ValidationIssue


GraphRagIntentType = Literal[
    "schema_repair",
    "runtime_diagnosis",
    "export_xml_repair",
    "lattice_map_repair",
    "renderer_capability",
    "documentation_lookup",
    "fact_gap_review",
    "benchmark_interpretation",
    "unknown",
]


class GraphRagQueryIntent(AgentBaseModel):
    intent_type: GraphRagIntentType
    issue_codes: list[str] = Field(default_factory=list)
    schema_paths: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    requires_human_confirmation: bool = False
    requires_retrieval: bool = False
    explanation: str = ""


class GraphExpansionPolicy(AgentBaseModel):
    max_depth: int = 2
    max_nodes: int = 40
    preferred_node_types: list[str] = Field(default_factory=list)
    preferred_relations: list[str] = Field(default_factory=list)
    avoid_node_types: list[str] = Field(default_factory=list)
    avoid_relations: list[str] = Field(default_factory=list)
    include_examples: bool = True
    include_api_docs: bool = True
    include_repair_policies: bool = True
    include_benchmark_docs: bool = True
    fact_gap_safe_mode: bool = False


class PlannedGraphPath(AgentBaseModel):
    nodes: list[str]
    relations: list[str] = Field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class GraphRagQueryPlan(AgentBaseModel):
    intent: GraphRagQueryIntent
    expansion_policy: GraphExpansionPolicy
    start_nodes: list[str] = Field(default_factory=list)
    preferred_queries: list[str] = Field(default_factory=list)
    required_filters: dict[str, list[str]] = Field(default_factory=dict)
    avoided_queries: list[str] = Field(default_factory=list)
    planned_paths: list[PlannedGraphPath] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


_INTENT_PRIORITY = {
    "fact_gap_review": 90,
    "export_xml_repair": 80,
    "runtime_diagnosis": 70,
    "lattice_map_repair": 60,
    "renderer_capability": 50,
    "schema_repair": 40,
    "benchmark_interpretation": 30,
    "documentation_lookup": 20,
    "unknown": 0,
}
_SCHEMA_REPAIR_CODES = {
    "plan.model.missing",
    "plan.complex_model.non_executable",
    "assembly.requires_lattice",
    "assembly.requires_cells",
    "assembly.requires_universes",
    "cell.region_ref_missing",
    "region.surface_ref_missing",
    "universe.cell_ref_missing",
}
_RUNTIME_DIAGNOSIS_CODES = {
    "runtime.geometry_overlap",
    "runtime.lost_particle",
    "runtime.openmc_unknown_error",
    "runtime.material_missing_nuclide_data",
}
_EXPORT_XML_CODES = {
    "export_xml.dangling_cell_fill",
    "export_xml.dangling_lattice_universe",
    "export_xml.dangling_material_ref",
    "export_xml.dangling_region_surface",
    "export_xml.geometry_reference_unknown",
}
_LATTICE_MAP_CODES = {
    "lattice.pin_count_mismatch",
    "lattice.pin_map_mismatch",
    "lattice.hex.rings_missing",
    "lattice.hex.ring_shape_invalid",
    "lattice.hex.outer_universe_missing",
}
_RENDERER_CAPABILITY_CODES = {
    "lattice.hex.renderer_unsupported",
    "plan.executable.unsupported_renderer",
    "assembly.requires_rect_lattice",
}
_FACT_GAP_TOKENS = (
    "cross_sections",
    "missing_nuclide_data",
    "composition missing",
    "density missing",
    "unknown loading pattern",
)
_BENCHMARK_TOKENS = (
    "benchmark.vera",
    "benchmark.c5g7",
    "benchmark.beavrs",
    "benchmark.watts_bar",
    "pin map",
    "loading pattern",
    "assembly layout",
)
_UNSAFE_FACT_TOKENS = ("density", "composition", "cross_sections", "path", "benchmark constants")


def classify_graphrag_intent(issues: list[ValidationIssue]) -> GraphRagQueryIntent:
    """Classify issues into a single highest-priority GraphRAG query intent."""
    issue_codes = _dedupe([issue.code for issue in issues])
    schema_paths = _dedupe([issue.schema_path for issue in issues if issue.schema_path])
    concept_ids = _dedupe([issue.concept_id for issue in issues if issue.concept_id])
    requires_human = any(issue.requires_human_confirmation for issue in issues)
    requires_retrieval = any(issue.requires_retrieval for issue in issues)
    haystack = " ".join(
        [
            *issue_codes,
            *schema_paths,
            *concept_ids,
            *(issue.message for issue in issues),
            *(pattern for issue in issues for pattern in issue.grep_patterns),
        ]
    ).casefold()

    candidates: list[tuple[str, str]] = []
    if requires_human or any(token in haystack for token in _FACT_GAP_TOKENS):
        candidates.append(("fact_gap_review", "issue requires human confirmation or missing facts"))
    if any(code in _EXPORT_XML_CODES or code.startswith("export_xml.") for code in issue_codes):
        candidates.append(("export_xml_repair", "export_xml dangling/reference issue"))
    if any(code in _RUNTIME_DIAGNOSIS_CODES for code in issue_codes):
        candidates.append(("runtime_diagnosis", "runtime diagnostic issue"))
    if any(code in _LATTICE_MAP_CODES for code in issue_codes):
        candidates.append(("lattice_map_repair", "lattice map/count/ring issue"))
    if any(code in _RENDERER_CAPABILITY_CODES for code in issue_codes):
        candidates.append(("renderer_capability", "renderer capability boundary"))
    if any(code in _SCHEMA_REPAIR_CODES for code in issue_codes):
        candidates.append(("schema_repair", "schema repair issue"))
    if any(token in haystack for token in _BENCHMARK_TOKENS):
        candidates.append(("benchmark_interpretation", "benchmark or loading pattern context"))
    if requires_retrieval:
        candidates.append(("documentation_lookup", "issue explicitly requires retrieval"))

    if not candidates:
        intent_type = "unknown"
        explanation = "no specific GraphRAG intent matched"
    else:
        intent_type, explanation = max(
            candidates,
            key=lambda item: (_INTENT_PRIORITY[item[0]], item[0]),
        )
    return GraphRagQueryIntent(
        intent_type=intent_type,  # type: ignore[arg-type]
        issue_codes=issue_codes,
        schema_paths=schema_paths,
        concept_ids=concept_ids,
        requires_human_confirmation=requires_human,
        requires_retrieval=requires_retrieval,
        explanation=explanation,
    )


def expansion_policy_for_intent(intent: GraphRagQueryIntent) -> GraphExpansionPolicy:
    """Return bounded graph expansion preferences for an intent."""
    if intent.intent_type == "schema_repair":
        return GraphExpansionPolicy(
            max_depth=2,
            max_nodes=40,
            preferred_node_types=["schema_field", "validation_rule", "repair_policy", "openmc_concept"],
            preferred_relations=["validated_by", "raises", "repairs_with", "related_to"],
            include_examples=True,
            include_api_docs=False,
        )
    if intent.intent_type == "runtime_diagnosis":
        return GraphExpansionPolicy(
            max_depth=3,
            max_nodes=60,
            preferred_node_types=[
                "runtime_error",
                "openmc_concept",
                "openmc_api",
                "doc_ref",
                "example_ref",
                "repair_policy",
            ],
            preferred_relations=["related_to", "documented_in", "demonstrated_by", "repairs_with"],
            include_examples=True,
            include_api_docs=True,
        )
    if intent.intent_type == "export_xml_repair":
        return GraphExpansionPolicy(
            max_depth=2,
            max_nodes=40,
            preferred_node_types=["validation_issue", "schema_field", "repair_policy", "openmc_concept"],
            preferred_relations=["raises", "related_to", "repairs_with"],
            include_examples=False,
            include_api_docs=False,
        )
    if intent.intent_type == "lattice_map_repair":
        return GraphExpansionPolicy(
            max_depth=2,
            max_nodes=50,
            preferred_node_types=["schema_field", "openmc_concept", "doc_ref", "example_ref", "repair_policy"],
            preferred_relations=["related_to", "documented_in", "demonstrated_by", "repairs_with"],
            include_examples=True,
            include_api_docs=True,
        )
    if intent.intent_type == "renderer_capability":
        return GraphExpansionPolicy(
            max_depth=2,
            max_nodes=40,
            preferred_node_types=["renderer_capability", "openmc_concept", "doc_ref", "repair_policy"],
            preferred_relations=["supports", "downgrades_to", "documented_in", "related_to"],
            include_examples=False,
            include_api_docs=True,
        )
    if intent.intent_type == "fact_gap_review":
        return GraphExpansionPolicy(
            max_depth=1,
            max_nodes=25,
            preferred_node_types=["openmc_concept", "doc_ref"],
            preferred_relations=["documented_in", "related_to"],
            include_examples=False,
            include_api_docs=True,
            include_repair_policies=True,
            fact_gap_safe_mode=True,
        )
    if intent.intent_type == "benchmark_interpretation":
        return GraphExpansionPolicy(
            max_depth=2,
            max_nodes=45,
            preferred_node_types=["openmc_concept", "doc_ref", "example_ref", "schema_field"],
            preferred_relations=["mentions", "related_to", "documented_in", "demonstrated_by"],
            include_examples=True,
            include_api_docs=False,
            include_benchmark_docs=True,
        )
    return GraphExpansionPolicy(
        max_depth=2,
        max_nodes=35,
        preferred_node_types=["openmc_concept", "doc_ref", "schema_field"],
        preferred_relations=["related_to", "documented_in"],
        include_examples=False,
        include_api_docs=True,
    )


def start_nodes_for_intent(
    intent: GraphRagQueryIntent,
    *,
    existing_graph_context: GraphContext | None = None,
) -> list[str]:
    """Generate graph start nodes from intent anchors and targeted supplements."""
    nodes: list[str] = []
    nodes.extend(f"issue.{code}" for code in intent.issue_codes)
    nodes.extend(_schema_node(path) for path in intent.schema_paths)
    nodes.extend(_concept_node(concept) for concept in intent.concept_ids)
    if existing_graph_context is not None:
        nodes.extend(existing_graph_context.start_nodes)

    all_text = " ".join([*intent.issue_codes, *intent.schema_paths, *intent.concept_ids]).casefold()
    if intent.intent_type == "lattice_map_repair":
        nodes.extend(
            [
                "concept.openmc.geometry.pin_map",
                "concept.openmc.geometry.rect_lattice",
                "schema.LatticeSpec.universe_pattern",
                "schema.LatticeSpec.overrides",
                "schema.LatticeSpec.expected_counts",
            ]
        )
    if "hex" in all_text or any(code.startswith("lattice.hex.") for code in intent.issue_codes):
        nodes.extend(
            [
                "concept.openmc.geometry.hex_lattice",
                "schema.LatticeSpec.rings",
                "schema.LatticeSpec.outer_universe_id",
            ]
        )
    if "runtime.geometry_overlap" in intent.issue_codes:
        nodes.extend(
            [
                "concept.openmc.geometry.region_boolean_expression",
                "concept.openmc.geometry.surface",
                "concept.openmc.geometry.boundary_type",
            ]
        )
    if "cross_sections" in all_text or "nuclear" in all_text:
        nodes.extend(
            [
                "concept.openmc.data.cross_sections",
                "concept.openmc.material.nuclide_name",
            ]
        )
    if intent.requires_human_confirmation:
        nodes.append("concept.openmc_agent.human_confirmation")
    return _dedupe(nodes)[:48]


def score_graph_path(
    path: PlannedGraphPath,
    intent: GraphRagQueryIntent,
    policy: GraphExpansionPolicy,
) -> tuple[float, list[str]]:
    """Score a planned path for intent relevance."""
    score = 0.05
    reasons = ["base +0.05"]
    node_text = " ".join(path.nodes).casefold()
    node_types = [_node_type_for_id(node) for node in path.nodes]

    if any(code.casefold() in node_text for code in intent.issue_codes):
        score += 0.20
        reasons.append("issue code in path +0.20")
    if any(_schema_node(schema).casefold() in node_text for schema in intent.schema_paths):
        score += 0.20
        reasons.append("schema path in path +0.20")
    if any(_concept_node(concept).casefold() in node_text for concept in intent.concept_ids):
        score += 0.20
        reasons.append("concept id in path +0.20")

    preferred_type_hits = sum(1 for node_type in node_types if node_type in policy.preferred_node_types)
    if preferred_type_hits:
        bonus = min(0.30, preferred_type_hits * 0.10)
        score += bonus
        reasons.append(f"preferred node types +{bonus:.2f}")
    if any(node_type == "repair_policy" for node_type in node_types) and _is_repair_intent(intent):
        score += 0.15
        reasons.append("repair policy for repair intent +0.15")
    if any(node_type in {"doc_ref", "openmc_api", "example_ref"} for node_type in node_types):
        if _path_outputs_allowed(node_types, policy):
            score += 0.10
            reasons.append("allowed doc/api/example target +0.10")
    if 2 <= len(path.nodes) <= 4:
        score += 0.10
        reasons.append("path length 2-4 +0.10")

    if any(node_type in policy.avoid_node_types for node_type in node_types):
        score -= 0.20
        reasons.append("avoided node type -0.20")
    if any(relation in policy.avoid_relations for relation in path.relations):
        score -= 0.15
        reasons.append("avoided relation -0.15")
    if len(path.nodes) > 4:
        score -= 0.10
        reasons.append("path too long -0.10")
    if policy.fact_gap_safe_mode and _path_looks_unsafe_for_fact_gap(path):
        score -= 0.30
        reasons.append("fact-gap unsafe path -0.30")

    return _clamp(score), reasons


def plan_graph_paths(
    graph_context: GraphContext,
    intent: GraphRagQueryIntent,
    policy: GraphExpansionPolicy,
    *,
    max_paths: int = 8,
) -> list[PlannedGraphPath]:
    """Extract short deterministic paths from a GraphContext and rank them."""
    if not graph_context.nodes:
        return []
    node_ids = {node.id for node in graph_context.nodes}
    adjacency: dict[str, list[tuple[GraphEdge, str]]] = {}
    for edge in graph_context.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            continue
        adjacency.setdefault(edge.source, []).append((edge, edge.target))
        adjacency.setdefault(edge.target, []).append((edge, edge.source))

    starts = [node for node in graph_context.start_nodes if node in node_ids]
    if not starts:
        starts = _preferred_start_candidates(node_ids, intent)

    candidates: list[PlannedGraphPath] = []
    for start in starts:
        for edge, first in sorted(adjacency.get(start, []), key=lambda item: (item[0].relation, item[1])):
            candidates.append(PlannedGraphPath(nodes=[start, first], relations=[edge.relation]))
            for second_edge, second in sorted(adjacency.get(first, []), key=lambda item: (item[0].relation, item[1])):
                if second == start:
                    continue
                candidates.append(
                    PlannedGraphPath(
                        nodes=[start, first, second],
                        relations=[edge.relation, second_edge.relation],
                    )
                )
    if not candidates:
        for start in starts:
            candidates.append(PlannedGraphPath(nodes=[start], relations=[]))

    scored: list[PlannedGraphPath] = []
    for candidate in _dedupe_paths(candidates):
        score, reasons = score_graph_path(candidate, intent, policy)
        scored.append(candidate.model_copy(update={"score": score, "reasons": reasons}))
    scored.sort(key=lambda path: (-path.score, len(path.nodes), " ".join(path.nodes)))
    return scored[:max_paths]


def build_queries_from_plan(
    intent: GraphRagQueryIntent,
    paths: list[PlannedGraphPath],
    graph_context: GraphContext | None = None,
) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Build preferred lexical queries and metadata filters from a query plan."""
    queries: list[str] = []
    filters: dict[str, list[str]] = {"concept_ids": [], "schema_paths": [], "doc_refs": [], "api_refs": []}
    avoided: list[str] = []

    if graph_context is not None:
        queries.extend(graph_context.retrieval_hints)
        filters["concept_ids"].extend(graph_context.related_concept_ids)
        filters["schema_paths"].extend(graph_context.related_schema_paths)
        filters["doc_refs"].extend(graph_context.related_doc_refs)
        filters["api_refs"].extend(graph_context.related_api_refs)

    if intent.intent_type == "lattice_map_repair":
        queries.extend(
            [
                "rect lattice universe pattern row column overrides expected counts",
                "pin map loading pattern guide tube fission chamber lattice positions",
            ]
        )
        filters["concept_ids"].extend(["openmc.geometry.pin_map", "openmc.geometry.rect_lattice"])
        filters["schema_paths"].extend(
            [
                "LatticeSpec.universe_pattern",
                "LatticeSpec.overrides",
                "LatticeSpec.expected_counts",
            ]
        )
    elif intent.intent_type == "runtime_diagnosis":
        if "runtime.geometry_overlap" in intent.issue_codes:
            queries.append("geometry overlap region boolean expression surface boundary")
        if "runtime.lost_particle" in intent.issue_codes:
            queries.append("lost particle geometry boundary source sampling")
        if not queries:
            queries.append("OpenMC runtime diagnostic geometry material error")
    elif intent.intent_type == "export_xml_repair":
        queries.extend(
            [
                "dangling reference missing universe missing material missing region",
                "cell fill lattice universe material id reference",
            ]
        )
    elif intent.intent_type == "renderer_capability":
        queries.extend(
            [
                "renderer capability unsupported lattice skeleton downgrade",
                "RectLattice HexLattice renderer support",
            ]
        )
    elif intent.intent_type == "fact_gap_review":
        queries.extend(
            [
                "cross_sections environment variable nuclear data path documentation",
                "material composition requires human confirmation",
            ]
        )
        avoided.extend(
            [
                "guess density",
                "infer composition",
                "invent cross_sections path",
                "fill missing benchmark constants",
            ]
        )
    elif intent.intent_type == "benchmark_interpretation":
        queries.extend(["benchmark pin map loading pattern assembly layout", "VERA C5G7 BEAVRS reactor benchmark documentation"])
    elif intent.intent_type == "schema_repair":
        queries.extend(["schema field repair policy model structure", "missing cell universe material lattice reference"])
    elif intent.intent_type == "documentation_lookup":
        queries.extend(_path_tail_queries(paths))

    filters["concept_ids"].extend(intent.concept_ids)
    filters["schema_paths"].extend(intent.schema_paths)
    for path in paths:
        for node in path.nodes:
            if node.startswith("concept."):
                filters["concept_ids"].append(node.removeprefix("concept."))
            elif node.startswith("schema."):
                filters["schema_paths"].append(node.removeprefix("schema."))
            elif node.startswith("doc."):
                filters["doc_refs"].append(node.removeprefix("doc."))
            elif node.startswith("api."):
                filters["api_refs"].append(node.removeprefix("api."))
    filters = {key: _dedupe(value) for key, value in filters.items() if _dedupe(value)}
    return _dedupe(_filter_queries(queries))[:8], filters, _dedupe(avoided)


def plan_graphrag_query(
    issues: list[ValidationIssue],
    *,
    graph_context: GraphContext | None = None,
) -> GraphRagQueryPlan:
    """Create a deterministic GraphRAG query plan from issues and optional graph context."""
    intent = classify_graphrag_intent(issues)
    policy = expansion_policy_for_intent(intent)
    starts = start_nodes_for_intent(intent, existing_graph_context=graph_context)
    warnings: list[str] = []
    if not starts:
        warnings.append("no GraphRAG query plan start nodes generated")
    paths = (
        plan_graph_paths(graph_context, intent, policy)
        if graph_context is not None
        else []
    )
    queries, filters, avoided = build_queries_from_plan(intent, paths, graph_context)
    return GraphRagQueryPlan(
        intent=intent,
        expansion_policy=policy,
        start_nodes=starts,
        preferred_queries=queries,
        required_filters=filters,
        avoided_queries=avoided,
        planned_paths=paths,
        warnings=warnings,
    )


def format_graphrag_query_plan(plan: GraphRagQueryPlan | None) -> str:
    """Render a compact prompt/debug summary for a query plan."""
    if plan is None:
        return ""
    lines = [
        "\n[GraphRAG Query Plan]",
        f"intent={plan.intent.intent_type}",
        f"start_nodes={', '.join(plan.start_nodes[:6])}",
        f"preferred_queries={'; '.join(plan.preferred_queries[:4])}",
        f"selected_paths={_format_path_summary(plan.planned_paths[:4])}",
        f"safety=fact_gap_safe_mode {str(plan.expansion_policy.fact_gap_safe_mode).lower()}",
    ]
    if plan.expansion_policy.fact_gap_safe_mode:
        lines.append("GraphRAG query plan is documentation-only; human confirmation is still required.")
    rendered = "\n".join(line for line in lines if line and not line.endswith("="))
    return rendered[:1000].rstrip() + "\n"


def _preferred_start_candidates(node_ids: set[str], intent: GraphRagQueryIntent) -> list[str]:
    starts: list[str] = []
    for code in intent.issue_codes:
        node = f"issue.{code}"
        if node in node_ids:
            starts.append(node)
    for schema in intent.schema_paths:
        node = _schema_node(schema)
        if node in node_ids:
            starts.append(node)
    for concept in intent.concept_ids:
        node = _concept_node(concept)
        if node in node_ids:
            starts.append(node)
    return starts or sorted(node_ids)[:4]


def _path_tail_queries(paths: list[PlannedGraphPath]) -> list[str]:
    queries: list[str] = []
    for path in paths:
        tokens = []
        for node in path.nodes:
            tail = node.rsplit(".", 1)[-1].replace("_", " ")
            if len(tail) >= 3:
                tokens.append(tail)
        if tokens:
            queries.append(" ".join(tokens[:4]))
    return queries


def _format_path_summary(paths: list[PlannedGraphPath]) -> str:
    if not paths:
        return "none"
    return "; ".join(" -> ".join(path.nodes[:4]) for path in paths)


def _is_repair_intent(intent: GraphRagQueryIntent) -> bool:
    return intent.intent_type in {
        "schema_repair",
        "export_xml_repair",
        "lattice_map_repair",
        "renderer_capability",
    }


def _path_outputs_allowed(node_types: list[str], policy: GraphExpansionPolicy) -> bool:
    if "example_ref" in node_types and not policy.include_examples:
        return False
    if "openmc_api" in node_types and not policy.include_api_docs:
        return False
    return True


def _path_looks_unsafe_for_fact_gap(path: PlannedGraphPath) -> bool:
    haystack = " ".join([*path.nodes, *path.relations]).casefold()
    if any(token in haystack for token in ("guess", "invent", "infer")):
        return True
    if any(token in haystack for token in ("density_value", "composition")):
        return True
    return "cross_sections" in haystack and ".path" in haystack


def _node_type_for_id(node_id: str) -> str:
    if node_id.startswith("issue."):
        if node_id.startswith("issue.runtime."):
            return "runtime_error"
        return "validation_issue"
    if node_id.startswith("schema."):
        return "schema_field"
    if node_id.startswith("concept.openmc_agent."):
        return "renderer_capability"
    if node_id.startswith("concept."):
        return "openmc_concept"
    if node_id.startswith("api."):
        return "openmc_api"
    if node_id.startswith("doc."):
        return "doc_ref"
    if node_id.startswith("example."):
        return "example_ref"
    if node_id.startswith("repair."):
        return "repair_policy"
    return "unknown"


def _schema_node(schema_path: str) -> str:
    return schema_path if schema_path.startswith("schema.") else f"schema.{schema_path}"


def _concept_node(concept_id: str) -> str:
    return concept_id if concept_id.startswith("concept.") else f"concept.{concept_id}"


def _filter_queries(queries: list[str]) -> list[str]:
    filtered: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query).split())
        if len(cleaned) < 3:
            continue
        filtered.append(cleaned)
    return filtered


def _dedupe_paths(paths: list[PlannedGraphPath]) -> list[PlannedGraphPath]:
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    deduped: list[PlannedGraphPath] = []
    for path in paths:
        key = (tuple(path.nodes), tuple(path.relations))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


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


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 6)))
