import pytest

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graphrag_query_planner import (
    GraphExpansionPolicy,
    PlannedGraphPath,
    build_queries_from_plan,
    classify_graphrag_intent,
    expansion_policy_for_intent,
    format_graphrag_query_plan,
    plan_graph_paths,
    plan_graphrag_query,
    score_graph_path,
    start_nodes_for_intent,
)
from openmc_agent.graphrag_retriever import (
    GraphRagRequest,
    expand_graphrag_subgraph,
    graphrag_request_from_issues,
    rag_request_from_graphrag_context,
)
from openmc_agent.knowledge_graph import GraphContext, GraphEdge, GraphNode
from openmc_agent.retrieval_orchestrator import (
    RetrievalContext,
    RetrievalPolicy,
    gather_retrieval_context_for_issues,
)
from openmc_agent.schemas import ValidationIssue
from openmc_agent.workflow_trace import summarize_retrieval_context as summarize_trace_retrieval_context


def issue(code: str, **updates: object) -> ValidationIssue:
    data = {
        "severity": "error",
        "code": code,
        "message": code.replace(".", " "),
    }
    data.update(updates)
    return ValidationIssue(**data)


def test_classifies_lattice_pin_map_mismatch() -> None:
    intent = classify_graphrag_intent([issue("lattice.pin_map_mismatch")])

    assert intent.intent_type == "lattice_map_repair"


def test_classifies_runtime_geometry_overlap() -> None:
    intent = classify_graphrag_intent([issue_from_catalog("runtime.geometry_overlap")])

    assert intent.intent_type == "runtime_diagnosis"


def test_classifies_export_xml_repair() -> None:
    intent = classify_graphrag_intent(
        [issue_from_catalog("export_xml.dangling_lattice_universe")]
    )

    assert intent.intent_type == "export_xml_repair"


def test_classifies_renderer_capability() -> None:
    intent = classify_graphrag_intent(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    assert intent.intent_type == "renderer_capability"


def test_classifies_fact_gap_and_priority() -> None:
    fact_gap = issue_from_catalog("runtime.cross_sections_missing")
    export_issue = issue_from_catalog("export_xml.dangling_lattice_universe")

    intent = classify_graphrag_intent([export_issue, fact_gap])

    assert intent.intent_type == "fact_gap_review"
    assert intent.requires_human_confirmation is True


def test_expansion_policy_matches_intent_shape() -> None:
    lattice_policy = expansion_policy_for_intent(
        classify_graphrag_intent([issue("lattice.pin_map_mismatch")])
    )
    runtime_policy = expansion_policy_for_intent(
        classify_graphrag_intent([issue_from_catalog("runtime.geometry_overlap")])
    )
    fact_policy = expansion_policy_for_intent(
        classify_graphrag_intent([issue_from_catalog("runtime.cross_sections_missing")])
    )
    renderer_policy = expansion_policy_for_intent(
        classify_graphrag_intent([issue_from_catalog("lattice.hex.renderer_unsupported")])
    )

    assert "repair_policy" in lattice_policy.preferred_node_types
    assert "openmc_concept" in lattice_policy.preferred_node_types
    assert runtime_policy.max_depth > lattice_policy.max_depth
    assert fact_policy.fact_gap_safe_mode is True
    assert renderer_policy.include_examples is False


def test_start_nodes_include_issue_schema_concept_and_supplements() -> None:
    intent = classify_graphrag_intent(
        [
            issue(
                "lattice.pin_map_mismatch",
                schema_path="LatticeSpec.universe_pattern",
                concept_id="openmc.geometry.pin_map",
            )
        ]
    )

    nodes = start_nodes_for_intent(intent)

    assert "issue.lattice.pin_map_mismatch" in nodes
    assert "schema.LatticeSpec.universe_pattern" in nodes
    assert "concept.openmc.geometry.pin_map" in nodes
    assert "concept.openmc.geometry.rect_lattice" in nodes
    assert "schema.LatticeSpec.expected_counts" in nodes


def test_start_nodes_add_hex_geometry_and_cross_sections_supplements() -> None:
    hex_nodes = start_nodes_for_intent(
        classify_graphrag_intent([issue_from_catalog("lattice.hex.renderer_unsupported")])
    )
    overlap_nodes = start_nodes_for_intent(
        classify_graphrag_intent([issue_from_catalog("runtime.geometry_overlap")])
    )
    cross_nodes = start_nodes_for_intent(
        classify_graphrag_intent([issue_from_catalog("runtime.cross_sections_missing")])
    )

    assert "schema.LatticeSpec.rings" in hex_nodes
    assert "schema.LatticeSpec.outer_universe_id" in hex_nodes
    assert "concept.openmc.geometry.surface" in overlap_nodes
    assert "concept.openmc.geometry.region_boolean_expression" in overlap_nodes
    assert "concept.openmc.data.cross_sections" in cross_nodes


def test_score_graph_path_prefers_issue_schema_concept_and_repair_policy() -> None:
    intent = classify_graphrag_intent(
        [
            issue(
                "lattice.pin_map_mismatch",
                schema_path="LatticeSpec.universe_pattern",
                concept_id="openmc.geometry.pin_map",
            )
        ]
    )
    policy = expansion_policy_for_intent(intent)
    relevant = PlannedGraphPath(
        nodes=[
            "issue.lattice.pin_map_mismatch",
            "schema.LatticeSpec.universe_pattern",
            "concept.openmc.geometry.pin_map",
            "repair.reflect_plan",
        ],
        relations=["raises", "represents", "repairs_with"],
    )
    weak = PlannedGraphPath(nodes=["doc.openmc.usersguide.settings"])

    relevant_score, reasons = score_graph_path(relevant, intent, policy)
    weak_score, _ = score_graph_path(weak, intent, policy)

    assert relevant_score > weak_score
    assert any("repair policy" in reason for reason in reasons)


def test_score_graph_path_penalizes_fact_gap_unsafe_path() -> None:
    intent = classify_graphrag_intent([issue_from_catalog("runtime.cross_sections_missing")])
    policy = expansion_policy_for_intent(intent)
    safe = PlannedGraphPath(
        nodes=["concept.openmc.data.cross_sections", "doc.openmc.usersguide.cross_sections"],
        relations=["documented_in"],
    )
    unsafe = PlannedGraphPath(
        nodes=["concept.openmc.data.cross_sections", "doc.guess.cross_sections.path"],
        relations=["related_to"],
    )

    safe_score, _ = score_graph_path(safe, intent, policy)
    unsafe_score, unsafe_reasons = score_graph_path(unsafe, intent, policy)

    assert unsafe_score < safe_score
    assert any("fact-gap unsafe" in reason for reason in unsafe_reasons)


def test_plan_graph_paths_extracts_and_sorts_short_paths() -> None:
    context = GraphContext(
        start_nodes=["issue.lattice.pin_map_mismatch"],
        nodes=[
            GraphNode(id="issue.lattice.pin_map_mismatch", type="validation_issue", title="pin mismatch"),
            GraphNode(id="schema.LatticeSpec.universe_pattern", type="schema_field", title="pattern"),
            GraphNode(id="concept.openmc.geometry.pin_map", type="openmc_concept", title="pin map"),
            GraphNode(id="repair.reflect_plan", type="repair_policy", title="reflect_plan"),
        ],
        edges=[
            GraphEdge(source="issue.lattice.pin_map_mismatch", target="schema.LatticeSpec.universe_pattern", relation="raises"),
            GraphEdge(source="schema.LatticeSpec.universe_pattern", target="concept.openmc.geometry.pin_map", relation="represents"),
            GraphEdge(source="concept.openmc.geometry.pin_map", target="repair.reflect_plan", relation="repairs_with"),
        ],
    )
    intent = classify_graphrag_intent(
        [
            issue(
                "lattice.pin_map_mismatch",
                schema_path="LatticeSpec.universe_pattern",
                concept_id="openmc.geometry.pin_map",
            )
        ]
    )
    paths = plan_graph_paths(context, intent, expansion_policy_for_intent(intent))

    assert paths
    assert paths[0].score >= paths[-1].score
    assert any("schema.LatticeSpec.universe_pattern" in path.nodes for path in paths)


def test_build_queries_for_lattice_runtime_and_fact_gap() -> None:
    lattice_intent = classify_graphrag_intent([issue("lattice.pin_map_mismatch")])
    runtime_intent = classify_graphrag_intent([issue_from_catalog("runtime.geometry_overlap")])
    fact_intent = classify_graphrag_intent([issue_from_catalog("runtime.cross_sections_missing")])

    lattice_queries, lattice_filters, _ = build_queries_from_plan(lattice_intent, [])
    runtime_queries, _, _ = build_queries_from_plan(runtime_intent, [])
    fact_queries, _, avoided = build_queries_from_plan(fact_intent, [])

    assert any("pin map" in query for query in lattice_queries)
    assert "openmc.geometry.pin_map" in lattice_filters["concept_ids"]
    assert any("geometry overlap" in query for query in runtime_queries)
    assert any("cross_sections" in query for query in fact_queries)
    assert "invent cross_sections path" in avoided
    assert len(lattice_queries) <= 8


def test_plan_graphrag_query_formats_compact_summary() -> None:
    plan = plan_graphrag_query([issue("lattice.pin_map_mismatch")])
    rendered = format_graphrag_query_plan(plan)

    assert plan.intent.intent_type == "lattice_map_repair"
    assert "intent=lattice_map_repair" in rendered
    assert len(rendered) <= 1000


def test_graphrag_request_uses_query_planner_by_default() -> None:
    request = graphrag_request_from_issues([issue("lattice.pin_map_mismatch")])

    assert request.query_plan is not None
    assert request.query_plan.intent.intent_type == "lattice_map_repair"
    assert "concept.openmc.geometry.pin_map" in request.graph_start_nodes


def test_expand_graphrag_subgraph_uses_plan_depth_and_start_nodes(monkeypatch) -> None:
    captured = {}

    def fake_graph_lookup(request, *, extra_nodes=None, extra_edges=None):
        captured["max_depth"] = request.max_depth
        captured["grep_patterns"] = request.grep_patterns
        return GraphContext(start_nodes=["concept.openmc.geometry.pin_map"])

    monkeypatch.setattr("openmc_agent.graphrag_retriever.graph_lookup", fake_graph_lookup)
    request = graphrag_request_from_issues([issue("lattice.pin_map_mismatch")])

    expand_graphrag_subgraph(request)

    assert captured["max_depth"] == request.query_plan.expansion_policy.max_depth
    assert "concept.openmc.geometry.pin_map" in captured["grep_patterns"]


def test_rag_request_uses_planned_preferred_queries_and_filters() -> None:
    request = graphrag_request_from_issues([issue("lattice.pin_map_mismatch")])
    context = GraphContext(
        related_concept_ids=["openmc.geometry.rect_lattice"],
        retrieval_hints=["generic lattice hint"],
    )

    rag_request = rag_request_from_graphrag_context(context, request)

    assert any("pin map" in query for query in rag_request.queries)
    assert "openmc.geometry.pin_map" in rag_request.concept_ids
    assert "openmc.geometry.rect_lattice" in rag_request.concept_ids


def test_graphrag_request_can_disable_planner() -> None:
    request = graphrag_request_from_issues(
        [issue("lattice.pin_map_mismatch")],
        use_query_planner=False,
    )

    assert request.query_plan is None
    assert "issue.lattice.pin_map_mismatch" in request.graph_start_nodes


def test_planner_failure_falls_back(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("planner failed")

    monkeypatch.setattr("openmc_agent.graphrag_retriever.plan_graphrag_query", boom)
    request = graphrag_request_from_issues([issue("lattice.pin_map_mismatch")])

    assert request.query_plan is None
    assert "issue.lattice.pin_map_mismatch" in request.graph_start_nodes


def test_orchestrator_summary_records_query_plan(monkeypatch) -> None:
    def fake_graphrag_retrieve(request):
        return pytest.importorskip("openmc_agent.graphrag_retriever").GraphRagResult(
            request=request,
            graph_context=GraphContext(),
            evidence=[],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )
    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(enable_grep=False, enable_rag=False),
    )

    assert "graphrag_intent=runtime_diagnosis" in (context.summary or "")
    assert "preferred_queries=" in (context.summary or "")


def test_orchestrator_can_disable_query_planner(monkeypatch) -> None:
    def fake_graphrag_retrieve(request):
        return pytest.importorskip("openmc_agent.graphrag_retriever").GraphRagResult(
            request=request,
            graph_context=GraphContext(),
            evidence=[],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )
    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(
            enable_grep=False,
            enable_rag=False,
            enable_graphrag_query_planner=False,
        ),
    )

    assert context.graphrag_request is not None
    assert context.graphrag_request.query_plan is None
    assert "graphrag query planner disabled by policy" in context.skipped_steps


def test_fact_gap_safe_mode_preserves_human_confirmation() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("runtime.cross_sections_missing")]
    )

    assert request.query_plan is not None
    assert request.query_plan.expansion_policy.fact_gap_safe_mode is True
    assert request.query_plan.intent.requires_human_confirmation is True


def test_trace_summary_records_query_plan_fields() -> None:
    request = graphrag_request_from_issues([issue_from_catalog("runtime.geometry_overlap")])
    context = RetrievalContext(graphrag_request=request)

    summary = summarize_trace_retrieval_context(context)

    assert summary["graphrag_intent_type"] == "runtime_diagnosis"
    assert summary["graphrag_query_plan_enabled"] is True
    assert summary["preferred_query_count"] >= 1
    assert summary["fact_gap_safe_mode"] is False
