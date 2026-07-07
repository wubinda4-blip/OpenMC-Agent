from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.grep_search import GrepSearchRequest, GrepSearchResult, RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.rag_search import DocumentChunk, RagSearchRequest, RagSearchResult
from openmc_agent.retrieval_orchestrator import (
    RetrievalContext,
    RetrievalPolicy,
    decide_retrieval_for_issue,
    format_retrieval_context,
    gather_retrieval_context_for_issues,
    summarize_retrieval_context,
)
from openmc_agent.schemas import ValidationIssue


def test_decision_runtime_geometry_overlap_runs_all_layers() -> None:
    decision = decide_retrieval_for_issue(issue_from_catalog("runtime.geometry_overlap"))

    assert decision.should_run_grep is True
    assert decision.should_run_graph is True
    assert decision.should_run_rag is True


def test_decision_cross_sections_runs_retrieval_but_keeps_human_confirmation() -> None:
    decision = decide_retrieval_for_issue(issue_from_catalog("runtime.cross_sections_missing"))

    assert decision.should_run_grep is True
    assert decision.should_run_graph is True
    assert decision.should_run_rag is True
    assert "fact-gap documentation lookup" in decision.reason


def test_policy_can_still_skip_cross_sections_fact_gap_retrieval() -> None:
    decision = decide_retrieval_for_issue(
        issue_from_catalog("runtime.cross_sections_missing"),
        policy=RetrievalPolicy(
            skip_grep_for_cross_sections_missing=True,
            skip_rag_for_fact_gap=True,
        ),
    )

    assert decision.should_run_grep is False
    assert decision.should_run_graph is True
    assert decision.should_run_rag is False


def test_decision_export_xml_dangling_lattice_universe_runs_grep_graph_not_rag() -> None:
    decision = decide_retrieval_for_issue(
        issue_from_catalog("export_xml.dangling_lattice_universe")
    )

    assert decision.should_run_grep is True
    assert decision.should_run_graph is True
    assert decision.should_run_rag is False


def test_decision_hex_renderer_unsupported_runs_all_layers() -> None:
    decision = decide_retrieval_for_issue(issue_from_catalog("lattice.hex.renderer_unsupported"))

    assert decision.should_run_grep is True
    assert decision.should_run_graph is True
    assert decision.should_run_rag is True


def test_decision_domain_rule_without_retrieval_uses_patterns_but_skips_rag() -> None:
    issue = ValidationIssue(
        severity="error",
        code="domain.rule.invalid",
        message="domain rule failed",
        schema_path="model.field",
        grep_patterns=["DomainRule"],
        requires_retrieval=False,
        route_hint=None,
    )

    decision = decide_retrieval_for_issue(issue)

    assert decision.should_run_grep is True
    assert decision.should_run_graph is True
    assert decision.should_run_rag is False


def test_orchestrator_empty_issues_returns_empty_context() -> None:
    context = gather_retrieval_context_for_issues([])

    assert context.issues == []
    assert context.merged_evidence == []
    assert "issues=0" in (context.summary or "")


def test_orchestrator_hex_issue_collects_all_evidence(monkeypatch) -> None:
    def fake_rag_search(request: RagSearchRequest) -> RagSearchResult:
        return RagSearchResult(
            request=request,
            chunks=[
                DocumentChunk(
                    chunk_id="docs/hex.md#1",
                    source_id="docs/hex.md",
                    source_type="project_doc",
                    path="docs/hex.md",
                    title="Geometry",
                    section="HexLattice",
                    text="HexLattice rings use outer_universe_id and orientation.",
                    start_line=1,
                    end_line=4,
                    doc_refs=["openmc.usersguide.geometry"],
                    api_refs=["openmc.api.HexLattice"],
                    concept_ids=["openmc.geometry.hex_lattice"],
                )
            ],
        )

    monkeypatch.setattr("openmc_agent.retrieval_orchestrator.rag_search", fake_rag_search)

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    assert context.grep_evidence
    assert context.graph_context is not None
    assert context.rag_evidence
    assert context.merged_evidence
    assert [item.source_type for item in context.merged_evidence][:3] != ["rag", "rag", "rag"]


def test_orchestrator_runtime_geometry_overlap_returns_rag_evidence(monkeypatch) -> None:
    def fake_rag_search(request: RagSearchRequest) -> RagSearchResult:
        return RagSearchResult(
            request=request,
            chunks=[
                DocumentChunk(
                    chunk_id="docs/geometry.md#1",
                    source_id="docs/geometry.md",
                    source_type="project_doc",
                    path="docs/geometry.md",
                    section="Geometry",
                    text="Geometry overlap diagnostics involve cell regions and surfaces.",
                    doc_refs=["openmc.usersguide.geometry"],
                    concept_ids=["openmc.geometry.surface"],
                )
            ],
        )

    monkeypatch.setattr("openmc_agent.retrieval_orchestrator.rag_search", fake_rag_search)

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )

    assert context.rag_evidence
    assert "Geometry overlap" in context.rag_evidence[0].text


def test_orchestrator_cross_sections_runs_doc_retrieval_and_preserves_fact_gap(monkeypatch) -> None:
    from openmc_agent.graphrag_retriever import GraphRagResult

    def fake_graphrag_retrieve(request):
        return GraphRagResult(
            request=request,
            evidence=[
                RetrievedEvidence(
                    source_type="graphrag",
                    locator="docs/data.md:1-4",
                    text="OPENMC_CROSS_SECTIONS must point to a user-confirmed cross_sections.xml.",
                    metadata={
                        "requires_human_confirmation": True,
                        "issue_codes": ["runtime.cross_sections_missing"],
                        "fact_gap_safe_mode": True,
                    },
                )
            ],
        )

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )
    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.cross_sections_missing")]
    )

    assert context.graph_context is not None
    assert context.graphrag_evidence
    assert context.graphrag_evidence[0].metadata["requires_human_confirmation"] is True
    assert context.graphrag_request is not None
    assert context.graphrag_request.query_plan is not None
    assert context.graphrag_request.query_plan.expansion_policy.fact_gap_safe_mode is True


def test_orchestrator_tool_exception_records_warning(monkeypatch) -> None:
    def broken_grep(_request):
        raise RuntimeError("boom")

    monkeypatch.setattr("openmc_agent.retrieval_orchestrator.grep_search", broken_grep)

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )

    assert any("grep failed" in warning for warning in context.warnings)
    assert context.graph_context is not None


def test_policy_disables_rag_and_records_skipped_step() -> None:
    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(enable_rag=False, enable_graphrag=False),
    )

    assert context.rag_evidence == []
    assert "rag disabled by policy" in context.skipped_steps


def test_format_retrieval_context_sections_and_boundaries() -> None:
    context = RetrievalContext(
        grep_evidence=[
            RetrievedEvidence(
                source_type="grep",
                locator="openmc_agent/schemas.py:1-2",
                text="class LatticeSpec",
            )
        ],
        graph_context=GraphContext(
            related_doc_refs=["openmc.usersguide.geometry"],
            related_api_refs=["openmc.api.HexLattice"],
            retrieval_hints=["OpenMC HexLattice rings outer universe orientation"],
        ),
        rag_evidence=[
            RetrievedEvidence(
                source_type="rag",
                locator="docs/geometry.md:1-4",
                text="HexLattice rings use outer universes.",
                metadata={"doc_refs": ["openmc.usersguide.geometry"]},
            )
        ],
    )

    rendered = format_retrieval_context(context)

    assert "[Grep Evidence]" in rendered
    assert "[Graph Context]" in rendered
    assert "[RAG Evidence]" in rendered
    assert rendered.index("[Grep Evidence]") < rendered.index("[Graph Context]")
    assert rendered.index("[Graph Context]") < rendered.index("[RAG Evidence]")
    assert "local documentation context only" in rendered
    assert "locator context only" in rendered
    assert len(rendered) < 5000


def test_format_retrieval_context_omits_empty_sections() -> None:
    rendered = format_retrieval_context(RetrievalContext())

    assert "[Grep Evidence]" not in rendered
    assert "[Graph Context]" not in rendered
    assert "[RAG Evidence]" not in rendered


def test_summarize_retrieval_context_is_compact() -> None:
    context = RetrievalContext(
        issues=[issue_from_catalog("runtime.geometry_overlap")],
        grep_results=[
            GrepSearchResult(
                request=GrepSearchRequest(
                    trigger="manual",
                    patterns=["overlap"],
                ),
                matches=[],
            )
        ],
        graph_context=GraphContext(nodes=[], edges=[]),
        warnings=["rag: no chunks"],
        skipped_steps=["rag skipped"],
    )

    summary = summarize_retrieval_context(context)

    assert "issues=1" in summary
    assert "grep_requests=0" in summary
    assert "warnings=1" in summary
    assert "skipped=1" in summary
    assert "rag: no chunks" not in summary
