import pytest
from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext, gather_graph_context_for_issues
from openmc_agent.rag_search import (
    DocumentChunk,
    RagSearchRequest,
    RagSearchResult,
    build_local_rag_index,
    format_rag_evidence,
    merge_retrieved_evidence,
    rag_request_from_graph_context,
    rag_result_to_evidence,
    rag_search,
)
from openmc_agent.schemas import ValidationReport


def test_rag_index_chunks_markdown_and_examples_and_excludes_binary(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    examples = tmp_path / "examples"
    docs.mkdir()
    examples.mkdir()
    (docs / "guide.md").write_text(
        "# Geometry\n\nOpenMC geometry region surface.\n\n"
        "## HexLattice\n\nHexLattice rings use outer_universe_id.\n",
        encoding="utf-8",
    )
    (examples / "hex_example.py").write_text(
        "import openmc\n\n"
        "def build():\n"
        "    lat = openmc.HexLattice()\n"
        "    lat.outer = None\n",
        encoding="utf-8",
    )
    (docs / "statepoint.10.h5").write_bytes(b"HexLattice")

    chunks = build_local_rag_index([str(docs), str(examples)])

    assert any(chunk.section == "HexLattice" for chunk in chunks)
    assert any(chunk.source_type == "project_example" for chunk in chunks)
    assert all(not chunk.path.endswith(".h5") for chunk in chunks)
    hex_chunk = next(chunk for chunk in chunks if chunk.section == "HexLattice")
    assert hex_chunk.start_line is not None
    assert hex_chunk.end_line is not None
    assert hex_chunk.title == "Geometry"


def test_rag_search_hex_query_hits_lattice_doc(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "geometry.md").write_text(
        "# OpenMC geometry\n\n"
        "The openmc.usersguide.geometry section documents HexLattice rings, "
        "outer_universe_id, and orientation for repeated universes.\n",
        encoding="utf-8",
    )

    result = rag_search(
        RagSearchRequest(
            trigger="manual",
            queries=["HexLattice rings outer universe orientation"],
            search_roots=[str(docs)],
        )
    )

    assert result.chunks
    assert "HexLattice" in result.chunks[0].text
    assert "openmc.geometry.hex_lattice" in result.chunks[0].concept_ids


def test_rag_search_doc_ref_and_api_ref_priority(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "geometry.md").write_text(
        "# Geometry\n\nopenmc.usersguide.geometry covers surfaces and regions.\n",
        encoding="utf-8",
    )
    (docs / "materials.md").write_text(
        "# Materials\n\nUse openmc.api.Material.set_density to set material density units.\n",
        encoding="utf-8",
    )

    doc_result = rag_search(
        RagSearchRequest(
            trigger="manual",
            doc_refs=["openmc.usersguide.geometry"],
            search_roots=[str(docs)],
        )
    )
    api_result = rag_search(
        RagSearchRequest(
            trigger="manual",
            api_refs=["openmc.api.Material.set_density"],
            search_roots=[str(docs)],
        )
    )

    assert doc_result.chunks[0].path.endswith("geometry.md")
    assert api_result.chunks[0].path.endswith("materials.md")


def test_rag_search_no_hits_warns(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("unrelated local note\n", encoding="utf-8")

    result = rag_search(
        RagSearchRequest(
            trigger="manual",
            queries=["HexLattice rings"],
            search_roots=[str(docs)],
        )
    )

    assert result.chunks == []
    assert any("no RAG document chunks matched" in warning for warning in result.warnings)


def test_rag_request_from_hex_graph_context() -> None:
    context = gather_graph_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    request = rag_request_from_graph_context(
        context,
        [issue_from_catalog("lattice.hex.renderer_unsupported")],
    )

    assert "openmc.usersguide.geometry" in request.doc_refs
    assert any("HexLattice" in ref for ref in request.api_refs)
    assert any("HexLattice" in query for query in request.queries)
    assert "openmc.geometry.hex_lattice" in request.concept_ids


def test_rag_request_from_runtime_overlap_and_cross_sections() -> None:
    overlap_context = gather_graph_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )
    overlap_request = rag_request_from_graph_context(
        overlap_context,
        [issue_from_catalog("runtime.geometry_overlap")],
    )
    assert any("geometry overlap" in query.lower() for query in overlap_request.queries)

    cross_issue = issue_from_catalog("runtime.cross_sections_missing")
    cross_context = gather_graph_context_for_issues([cross_issue])
    cross_request = rag_request_from_graph_context(cross_context, [cross_issue])
    assert any("cross_sections" in query for query in cross_request.queries)
    assert cross_issue.requires_human_confirmation is True


def test_rag_result_to_evidence_preserves_locator_metadata_and_limits_text() -> None:
    chunk = DocumentChunk(
        chunk_id="docs/materials.md#1",
        source_id="docs/materials.md",
        source_type="project_doc",
        path="docs/materials.md",
        title="Materials",
        section="Density",
        text="Use openmc.api.Material.set_density for density units.\n" * 80,
        start_line=10,
        end_line=25,
        doc_refs=["openmc.usersguide.materials"],
        api_refs=["openmc.api.Material.set_density"],
        concept_ids=["openmc.material.density_unit"],
        schema_paths=["MaterialSpec.density_unit"],
        metadata={"score": 12.0},
    )

    evidence = rag_result_to_evidence(
        RagSearchResult(
            request=RagSearchRequest(
                trigger="manual",
                api_refs=["openmc.api.Material.set_density"],
                max_chunk_chars=500,
            ),
            chunks=[chunk],
        )
    )

    assert evidence[0].source_type == "rag"
    assert "docs/materials.md:10-25" in evidence[0].locator
    assert evidence[0].metadata["doc_refs"] == ["openmc.usersguide.materials"]
    assert evidence[0].metadata["api_refs"] == ["openmc.api.Material.set_density"]
    assert evidence[0].metadata["concept_ids"] == ["openmc.material.density_unit"]
    assert len(evidence[0].text) <= 520


def test_format_rag_evidence_and_merge_preserves_grep_graph_order() -> None:
    grep = [
        RetrievedEvidence(source_type="grep", locator="openmc_agent/a.py:1-2", text="grep")
    ]
    graph = [RetrievedEvidence(source_type="graph", locator="concept.x", text="graph")]
    rag = [
        RetrievedEvidence(
            source_type="rag",
            locator="docs/geometry.md:1-4",
            text="HexLattice rings",
            metadata={"doc_refs": ["openmc.usersguide.geometry"]},
        )
    ]

    merged = merge_retrieved_evidence(grep, graph, rag, max_items=3)
    rendered = format_rag_evidence(rag)

    assert [item.source_type for item in merged] == ["grep", "graph", "rag"]
    assert "[RAG Evidence]" in rendered
    assert "documentation context" in rendered


@pytest.mark.openmc
def test_reflect_plan_prompt_contains_rag_evidence_and_constraints() -> None:
    pytest.importorskip("openmc", reason="OpenMC is required for graph import")
    from openmc_agent.graph import _build_reflection_requirement

    issue = issue_from_catalog("runtime.cross_sections_missing")
    report = ValidationReport.from_issues([issue])
    rag = [
        RetrievedEvidence(
            source_type="rag",
            locator="docs/cross_sections.md:1-6",
            text="OPENMC_CROSS_SECTIONS points to a local cross_sections.xml file.",
            issue_code=issue.code,
            concept_id=issue.concept_id,
            metadata={
                "doc_refs": ["openmc.usersguide.cross_sections"],
                "concept_ids": ["openmc.data.cross_sections"],
                "requires_human_confirmation": True,
            },
        )
    ]

    prompt = _build_reflection_requirement(
        {
            "requirement": "fix runtime issue",
            "validation_report": report,
            "rag_evidence": [item.model_dump(mode="json") for item in rag],
            "grep_evidence": [
                RetrievedEvidence(
                    source_type="grep",
                    locator="openmc_agent/tools.py:1-3",
                    text="cross_sections.xml",
                ).model_dump(mode="json")
            ],
            "graph_context": GraphContext(
                related_doc_refs=["openmc.usersguide.cross_sections"],
                retrieval_hints=["OpenMC cross_sections.xml OPENMC_CROSS_SECTIONS"],
            ).model_dump(mode="json"),
            "tool_results": [],
            "expert_feedback": [],
            "capability_repair_errors": [],
            "error": "missing cross sections",
        }
    )

    assert "[RAG Evidence]" in prompt
    assert "OPENMC_CROSS_SECTIONS" in prompt
    assert "Do not invent material density" in prompt
    assert "cross section paths" in prompt
    assert "preserve that requirement" in prompt
    assert prompt.index("[Grep Evidence]") < prompt.index("[Graph Context]") < prompt.index("[RAG Evidence]")
