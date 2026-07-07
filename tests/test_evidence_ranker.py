from openmc_agent.evidence_ranker import (
    EvidenceRankerPolicy,
    deduplicate_evidence,
    format_ranked_evidence_block,
    rank_and_select_evidence,
    score_evidence,
)
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.retrieval_orchestrator import (
    RetrievalContext,
    RetrievalPolicy,
    format_retrieval_context,
    gather_retrieval_context_for_issues,
)
from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graphrag_retriever import GraphRagResult


def evidence(
    source_type: str,
    locator: str,
    text: str,
    **metadata: object,
) -> RetrievedEvidence:
    return RetrievedEvidence(
        source_type=source_type,  # type: ignore[arg-type]
        locator=locator,
        text=text,
        metadata=dict(metadata),
    )


def test_grep_exact_match_scores_higher_than_plain_rag() -> None:
    grep = evidence(
        "grep",
        "openmc_agent/schemas.py:10-20",
        "class LatticeSpec has rings",
        matched_pattern="LatticeSpec",
        schema_paths=["LatticeSpec.rings"],
    )
    rag = evidence("rag", "docs/geometry.md:1-4", "LatticeSpec rings")

    grep_score, _ = score_evidence(grep, schema_paths=["LatticeSpec.rings"])
    rag_score, _ = score_evidence(rag, schema_paths=["LatticeSpec.rings"])

    assert grep_score > rag_score


def test_graphrag_graph_paths_score_higher_than_plain_rag() -> None:
    graphrag = evidence(
        "graphrag",
        "docs/hex.md:1-8",
        "HexLattice rings and outer universe",
        graph_paths=[{"explanation": "issue -> concept -> doc_chunk"}],
        doc_chunk_id="chunk-1",
        related_concept_ids=["openmc.geometry.hex_lattice"],
    )
    rag = evidence("rag", "docs/hex.md:1-8", "HexLattice rings and outer universe")

    graphrag_score, _ = score_evidence(
        graphrag,
        concept_ids=["openmc.geometry.hex_lattice"],
    )
    rag_score, _ = score_evidence(rag, concept_ids=["openmc.geometry.hex_lattice"])

    assert graphrag_score > rag_score


def test_issue_schema_and_concept_matches_increase_score() -> None:
    base = evidence("rag", "docs/hex.md:1", "HexLattice rings")
    matched = evidence(
        "rag",
        "docs/hex.md:1",
        "HexLattice rings",
        issue_codes=["lattice.hex.renderer_unsupported"],
        schema_paths=["LatticeSpec.rings"],
        concept_ids=["openmc.geometry.hex_lattice"],
    )

    base_score, _ = score_evidence(
        base,
        issue_codes=["lattice.hex.renderer_unsupported"],
        schema_paths=["LatticeSpec.rings"],
        concept_ids=["openmc.geometry.hex_lattice"],
    )
    matched_score, reasons = score_evidence(
        matched,
        issue_codes=["lattice.hex.renderer_unsupported"],
        schema_paths=["LatticeSpec.rings"],
        concept_ids=["openmc.geometry.hex_lattice"],
    )

    assert matched_score > base_score
    assert any("issue code match" in reason for reason in reasons)
    assert any("schema path exact" in reason for reason in reasons)
    assert any("concept match" in reason for reason in reasons)


def test_fact_gap_unsafe_evidence_gets_penalty() -> None:
    unsafe = evidence(
        "rag",
        "docs/data.md:1",
        "Set OPENMC_CROSS_SECTIONS=/guessed/cross_sections.xml",
        requires_human_confirmation=True,
    )
    safe = evidence(
        "rag",
        "docs/data.md:2",
        "OpenMC needs cross_sections.xml configured by the user.",
        requires_human_confirmation=True,
    )

    unsafe_score, unsafe_reasons = score_evidence(
        unsafe,
        issue_codes=["runtime.cross_sections_missing"],
    )
    safe_score, _ = score_evidence(safe, issue_codes=["runtime.cross_sections_missing"])

    assert unsafe_score < safe_score
    assert any("fact-gap" in reason for reason in unsafe_reasons)


def test_deduplicates_same_locator() -> None:
    kept, dropped = deduplicate_evidence(
        [
            evidence("rag", "docs/a.md:1-3", "same text"),
            evidence("graphrag", "docs/a.md:1-3", "same text", graph_paths=[{}]),
        ]
    )

    assert len(kept) == 1
    assert len(dropped) == 1
    assert kept[0].source_type == "graphrag"


def test_deduplicates_same_doc_chunk_keeps_graphrag_over_rag() -> None:
    kept, dropped = deduplicate_evidence(
        [
            evidence("rag", "docs/a.md:1", "chunk text", doc_chunk_id="c1"),
            evidence("graphrag", "docs/a.md:1", "chunk text", doc_chunk_id="c1"),
        ]
    )

    assert len(kept) == 1
    assert len(dropped) == 1
    assert kept[0].source_type == "graphrag"


def test_near_duplicate_text_is_deduplicated_without_dropping_grep() -> None:
    text = "HexLattice rings outer universe orientation " * 4
    kept, dropped = deduplicate_evidence(
        [
            evidence("rag", "docs/a.md:1", text),
            evidence("graphrag", "docs/b.md:1", text),
            evidence("grep", "tests/a.py:1", text),
        ]
    )

    assert len(kept) == 2
    assert len(dropped) == 1
    assert any(item.source_type == "grep" for item in kept)


def test_rank_and_select_applies_total_and_source_limits() -> None:
    items = [
        evidence("grep", f"openmc_agent/a{i}.py:1", "LatticeSpec", matched_pattern="LatticeSpec")
        for i in range(4)
    ]
    items.extend(
        evidence("rag", f"docs/a{i}.md:1", "HexLattice rings") for i in range(4)
    )

    result = rank_and_select_evidence(
        items,
        policy=EvidenceRankerPolicy(
            max_total_evidence=3,
            max_grep_evidence=2,
            max_rag_evidence=2,
            max_graphrag_evidence=2,
            max_graph_evidence=2,
        ),
    )

    assert len(result.selected) == 3
    assert sum(item.source_type == "grep" for item in result.selected) <= 2
    assert result.dropped_budget


def test_rank_and_select_truncates_per_evidence_and_total_chars() -> None:
    long_text = "HexLattice rings " * 200
    result = rank_and_select_evidence(
        [evidence("rag", "docs/a.md:1", long_text), evidence("rag", "docs/b.md:1", long_text)],
        policy=EvidenceRankerPolicy(
            max_total_evidence=4,
            max_chars_per_evidence=180,
            max_total_chars=220,
        ),
    )

    assert result.selected
    assert len(result.selected[0].text) <= 200
    assert result.selected[0].metadata.get("truncated") is True


def test_rank_and_select_empty_input_is_safe() -> None:
    result = rank_and_select_evidence([])

    assert result.selected == []
    assert result.summary["selected_count"] == 0


def test_format_ranked_evidence_block_includes_score_reasons_locator_and_path() -> None:
    result = rank_and_select_evidence(
        [
            evidence(
                "graphrag",
                "docs/hex.md:1-4",
                "HexLattice rings",
                graph_paths=[{"explanation": "issue -> concept -> doc_chunk"}],
                related_concept_ids=["openmc.geometry.hex_lattice"],
            )
        ],
        concept_ids=["openmc.geometry.hex_lattice"],
    )

    rendered = format_ranked_evidence_block(result)

    assert "[Ranked Evidence]" in rendered
    assert "score=" in rendered
    assert "relevance=" in rendered
    assert "locator=docs/hex.md:1-4" in rendered
    assert "graph_path=issue -> concept -> doc_chunk" in rendered


def test_format_ranked_evidence_block_warns_for_fact_gap() -> None:
    result = rank_and_select_evidence(
        [
            evidence(
                "rag",
                "docs/data.md:1",
                "cross_sections.xml must be configured",
                requires_human_confirmation=True,
            )
        ],
        issue_codes=["runtime.cross_sections_missing"],
    )

    assert "human confirmation is still required" in format_ranked_evidence_block(result)
    assert format_ranked_evidence_block(rank_and_select_evidence([])) == ""


def test_orchestrator_populates_ranked_evidence_when_enabled(monkeypatch) -> None:
    issue = issue_from_catalog("runtime.geometry_overlap")

    def fake_graph_lookup(request):
        return GraphContext(
            related_doc_refs=["openmc.usersguide.geometry"],
            retrieval_hints=["geometry overlap surface region"],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graph_lookup",
        fake_graph_lookup,
    )
    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        lambda request: GraphRagResult(
            request=request,
            evidence=[
                evidence(
                    "graphrag",
                    "docs/geometry.md:1",
                    "geometry overlap surface region",
                    graph_paths=[{"explanation": "issue -> concept -> doc"}],
                )
            ],
        ),
    )

    context = gather_retrieval_context_for_issues(
        [issue],
        policy=RetrievalPolicy(enable_grep=False, enable_rag=False),
    )

    assert context.evidence_ranking_result is not None
    assert context.ranked_evidence


def test_orchestrator_ranking_failure_falls_back(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("ranking broken")

    monkeypatch.setattr("openmc_agent.retrieval_orchestrator.rank_and_select_evidence", boom)
    context = RetrievalContext(
        issues=[issue_from_catalog("runtime.geometry_overlap")],
        merged_evidence=[evidence("rag", "docs/a.md:1", "geometry")],
    )
    from openmc_agent.retrieval_orchestrator import _run_evidence_ranking

    _run_evidence_ranking(context, RetrievalPolicy())

    assert context.ranked_evidence == context.merged_evidence
    assert any("evidence ranking failed" in warning for warning in context.warnings)


def test_format_retrieval_context_uses_ranked_evidence_when_available() -> None:
    ranking = rank_and_select_evidence(
        [evidence("rag", "docs/a.md:1", "geometry region surface")]
    )
    context = RetrievalContext(
        graph_context=GraphContext(related_doc_refs=["openmc.usersguide.geometry"]),
        evidence_ranking_result=ranking,
        ranked_evidence=ranking.selected,
        grep_evidence=[evidence("grep", "tests/a.py:1", "raw grep")],
    )

    rendered = format_retrieval_context(context)

    assert "[Ranked Evidence]" in rendered
    assert "[Grep Evidence]" not in rendered
    assert "Evidence Safety Constraints" in rendered


def test_format_retrieval_context_old_format_when_ranking_disabled() -> None:
    context = RetrievalContext(
        grep_evidence=[evidence("grep", "tests/a.py:1", "raw grep")],
        rag_evidence=[evidence("rag", "docs/a.md:1", "raw rag")],
    )

    rendered = format_retrieval_context(context)

    assert "[Grep Evidence]" in rendered
    assert "[RAG Evidence]" in rendered
