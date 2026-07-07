from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graphrag_retriever import (
    GraphRagRequest,
    GraphRagResult,
    expand_graphrag_subgraph,
    extract_graphrag_paths,
    format_graphrag_evidence,
    graphrag_request_from_issues,
    graphrag_retrieve,
    rag_request_from_graphrag_context,
)
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.rag_search import RagSearchRequest, RagSearchResult
from openmc_agent.retrieval_orchestrator import (
    RetrievalPolicy,
    format_retrieval_context,
    gather_retrieval_context_for_issues,
)


def test_graphrag_request_hex_lattice_adds_required_start_nodes() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    assert "concept.openmc.geometry.hex_lattice" in request.graph_start_nodes
    assert "schema.LatticeSpec.rings" in request.graph_start_nodes
    assert "schema.LatticeSpec.outer_universe_id" in request.graph_start_nodes
    assert request.trigger == "hex_lattice_issue"


def test_graphrag_request_geometry_overlap_adds_surface_region_nodes() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )

    assert "concept.openmc.geometry.surface" in request.graph_start_nodes
    assert "concept.openmc.geometry.region_boolean_expression" in request.graph_start_nodes
    assert "concept.openmc.geometry.boundary_type" in request.graph_start_nodes


def test_graphrag_request_cross_sections_preserves_human_confirmation() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("runtime.cross_sections_missing")]
    )

    assert "concept.openmc.data.cross_sections" in request.graph_start_nodes
    assert "concept.openmc_agent.human_confirmation" in request.graph_start_nodes


def test_expand_graphrag_subgraph_hex_has_doc_refs_and_hints() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    context = expand_graphrag_subgraph(request)

    assert "openmc.usersguide.geometry" in context.related_doc_refs
    assert context.retrieval_hints
    assert "openmc.geometry.hex_lattice" in context.related_concept_ids


def test_expand_graphrag_subgraph_unknown_start_warns() -> None:
    context = expand_graphrag_subgraph(
        GraphRagRequest(
            trigger="manual",
            graph_start_nodes=["concept.openmc.unknown_missing_node"],
        )
    )

    assert context.warnings


def test_rag_request_from_graphrag_context_uses_graph_refs_first() -> None:
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )
    context = expand_graphrag_subgraph(request)

    rag_request = rag_request_from_graphrag_context(context, request)

    assert "openmc.usersguide.geometry" in rag_request.doc_refs
    assert any("HexLattice" in query for query in rag_request.queries)
    assert "openmc.geometry.hex_lattice" in rag_request.concept_ids


def test_graphrag_retrieve_hex_lattice_doc(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text(
        "# HexLattice\n"
        "OpenMC HexLattice rings use an outer_universe_id and orientation.\n"
        "The ring ordering is center outward for this local documentation.\n",
        encoding="utf-8",
    )
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    ).model_copy(update={"search_roots": [str(docs)], "top_k_chunks": 3})

    result = graphrag_retrieve(request)

    assert result.graph_context is not None
    assert result.rag_result is not None
    assert result.evidence
    assert result.evidence[0].source_type == "graphrag"
    assert "HexLattice" in result.evidence[0].text
    assert result.evidence[0].metadata["graph_paths"]


def test_graphrag_retrieve_geometry_overlap_doc(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "geometry.md").write_text(
        "# Geometry overlap\n"
        "OpenMC geometry overlap diagnostics inspect region boolean expressions, "
        "surfaces, and boundary_type settings.\n",
        encoding="utf-8",
    )
    request = graphrag_request_from_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    ).model_copy(update={"search_roots": [str(docs)], "top_k_chunks": 2})

    result = graphrag_retrieve(request)

    assert result.evidence
    assert "region" in result.evidence[0].text.lower()
    assert "openmc.geometry.surface" in result.evidence[0].metadata["related_concept_ids"]


def test_graphrag_retrieve_no_doc_hit_keeps_graph_context(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "other.md").write_text("# Other\nNo matching content here.\n", encoding="utf-8")
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    ).model_copy(update={"search_roots": [str(docs)]})

    result = graphrag_retrieve(request)

    assert result.graph_context is not None
    assert result.evidence == []
    assert result.warnings


def test_extract_graphrag_paths_is_short_and_explainable() -> None:
    context = expand_graphrag_subgraph(
        graphrag_request_from_issues([issue_from_catalog("runtime.geometry_overlap")])
    )

    paths = extract_graphrag_paths(context)

    assert paths
    assert all(len(path.nodes) <= 3 for path in paths)
    assert any("geometry_overlap" in ".".join(path.nodes) for path in paths)


def test_format_graphrag_evidence_section() -> None:
    text = format_graphrag_evidence(
        [
            RetrievedEvidence(
                source_type="graphrag",
                locator="docs/hex.md:1-3",
                text="HexLattice rings use outer_universe_id.",
                metadata={
                    "graph_paths": [
                        {
                            "explanation": (
                                "issue.lattice.hex.renderer_unsupported -> "
                                "openmc.geometry.hex_lattice -> openmc.HexLattice"
                            )
                        }
                    ],
                    "related_concept_ids": ["openmc.geometry.hex_lattice"],
                    "related_doc_refs": ["openmc.usersguide.geometry"],
                },
            )
        ]
    )

    assert "[GraphRAG Evidence]" in text
    assert "graph-guided local documentation context" in text
    assert "HexLattice rings" in text


def test_orchestrator_default_graphrag_enabled(monkeypatch) -> None:
    def fake_graphrag_retrieve(request):
        return GraphRagResult(
            request=request,
            evidence=[
                RetrievedEvidence(
                    source_type="graphrag",
                    locator="docs/geometry.md:1-2",
                    text="Default GraphRAG evidence.",
                )
            ],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )

    assert context.graphrag_evidence


def test_orchestrator_can_disable_graphrag_by_policy() -> None:
    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(enable_graphrag=False),
    )

    assert context.graphrag_evidence == []
    assert "graphrag disabled by policy" in context.skipped_steps


def test_orchestrator_runs_graphrag_when_enabled(monkeypatch) -> None:
    def fake_graphrag_retrieve(request):
        evidence = RetrievedEvidence(
            source_type="graphrag",
            locator="docs/geometry.md:1-4",
            text="GraphRAG geometry surface region evidence.",
            metadata={
                "graph_paths": [{"explanation": "runtime.geometry_overlap -> surface"}],
                "related_concept_ids": ["openmc.geometry.surface"],
            },
        )
        return GraphRagResult(
            request=request,
            graph_context=GraphContext(related_doc_refs=["openmc.usersguide.geometry"]),
            rag_request=RagSearchRequest(trigger="runtime_issue"),
            rag_result=RagSearchResult(request=RagSearchRequest(trigger="runtime_issue")),
            evidence=[evidence],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(enable_graphrag=True, enable_rag=False),
    )

    assert context.graphrag_evidence
    assert context.merged_evidence
    assert any(item.source_type == "graphrag" for item in context.merged_evidence)
    assert any("GraphRAG document retrieval" in step for step in context.skipped_steps)


def test_orchestrator_prefer_graphrag_skips_plain_rag(monkeypatch) -> None:
    def fake_graphrag_retrieve(request):
        return GraphRagResult(
            request=request,
            evidence=[
                RetrievedEvidence(
                    source_type="graphrag",
                    locator="docs/hex.md:1-2",
                    text="GraphRAG HexLattice evidence.",
                )
            ],
        )

    def fail_rag_search(_request):
        raise AssertionError("plain rag should be skipped")

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )
    monkeypatch.setattr("openmc_agent.retrieval_orchestrator.rag_search", fail_rag_search)

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")],
        policy=RetrievalPolicy(enable_graphrag=True, prefer_graphrag_over_rag=True),
    )

    assert context.graphrag_evidence
    assert context.rag_evidence == []
    assert any("GraphRAG evidence preferred" in step for step in context.skipped_steps)


def test_format_retrieval_context_includes_graphrag_section() -> None:
    context = gather_retrieval_context_for_issues([])
    context.graphrag_evidence = [
        RetrievedEvidence(
            source_type="graphrag",
            locator="docs/hex.md:1-3",
            text="HexLattice graph-guided evidence.",
            metadata={"graph_paths": [{"explanation": "issue -> concept -> doc"}]},
        )
    ]

    rendered = format_retrieval_context(context)

    assert "[GraphRAG Evidence]" in rendered
    assert "HexLattice graph-guided evidence" in rendered
