import json
import subprocess
import sys
from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graphrag_retriever import graphrag_request_from_issues, graphrag_retrieve
from openmc_agent.knowledge_graph import GraphLookupRequest, graph_lookup
from openmc_agent.knowledge_ingestion import (
    KnowledgeIngestionConfig,
    KnowledgeSource,
    annotate_text_refs,
    chunks_to_graph,
    default_knowledge_ingestion_config,
    ingest_knowledge_sources,
    load_ingested_graph,
    load_knowledge_sources_manifest,
    save_knowledge_ingestion_result,
)
from openmc_agent.rag_search import DocumentChunk


def test_manifest_loads_sources_object(tmp_path: Path) -> None:
    manifest = tmp_path / "knowledge_sources.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "docs",
                        "source_type": "project_docs",
                        "root_path": str(tmp_path / "docs"),
                        "include_globs": ["**/*.md"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config = load_knowledge_sources_manifest(manifest)

    assert config.sources[0].source_id == "docs"
    assert config.sources[0].source_type == "project_docs"


def test_missing_manifest_returns_default_config(tmp_path: Path) -> None:
    config = load_knowledge_sources_manifest(tmp_path / "missing.json")

    assert config.sources
    assert default_knowledge_ingestion_config().sources[0].source_id == "project_docs"


def test_missing_source_root_warns_without_failure(tmp_path: Path) -> None:
    config = KnowledgeIngestionConfig(
        sources=[
            KnowledgeSource(
                source_id="missing",
                source_type="project_docs",
                root_path=str(tmp_path / "missing"),
                include_globs=["**/*.md"],
            )
        ]
    )

    result = ingest_knowledge_sources(config)

    assert result.chunks == []
    assert any("source root does not exist" in warning for warning in result.warnings)


def test_annotation_material_geometry_lattice_runtime_benchmark_rules() -> None:
    refs = annotate_text_refs(
        "Material.set_density uses add_s_alpha_beta and OPENMC_CROSS_SECTIONS. "
        "RectLattice universe_pattern and HexLattice rings outer universe appear. "
        "A lost particle and pin count mismatch occurred in C5G7 MOX 17x17."
    )

    assert "openmc.material.density_value" in refs["concept_ids"]
    assert "openmc.material.thermal_scattering" in refs["concept_ids"]
    assert "openmc.data.cross_sections" in refs["concept_ids"]
    assert "openmc.geometry.rect_lattice" in refs["concept_ids"]
    assert "openmc.geometry.hex_lattice" in refs["concept_ids"]
    assert "runtime.lost_particle" in refs["issue_codes"]
    assert "lattice.pin_count_mismatch" in refs["issue_codes"]
    assert "benchmark.c5g7" in refs["benchmark_refs"]
    assert "reactor.mox" in refs["concept_ids"]
    assert "reactor.assembly" in refs["concept_ids"]


def test_ingests_markdown_and_python_example_chunks(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    examples = tmp_path / "examples"
    docs.mkdir()
    examples.mkdir()
    (docs / "materials.md").write_text(
        "# Materials\nMaterial.set_density and add_nuclide are documented here.\n",
        encoding="utf-8",
    )
    (examples / "hex_example.py").write_text(
        "import openmc\nlat = openmc.HexLattice()\n# rings and outer universe\n",
        encoding="utf-8",
    )
    config = KnowledgeIngestionConfig(
        sources=[
            KnowledgeSource(
                source_id="docs",
                source_type="project_docs",
                root_path=str(docs),
                include_globs=["**/*.md"],
            ),
            KnowledgeSource(
                source_id="examples",
                source_type="project_examples",
                root_path=str(examples),
                include_globs=["**/*.py"],
            ),
        ]
    )

    result = ingest_knowledge_sources(config)

    assert len(result.chunks) >= 2
    assert any(chunk.source_type == "project_example" for chunk in result.chunks)
    assert any(chunk.metadata.get("annotation_method") == "rules" for chunk in result.chunks)
    assert result.source_counts["docs"] >= 1


def test_ingestion_skips_large_files(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "big.md").write_text("x" * 200, encoding="utf-8")
    config = KnowledgeIngestionConfig(
        sources=[
            KnowledgeSource(
                source_id="docs",
                source_type="project_docs",
                root_path=str(docs),
                include_globs=["**/*.md"],
            )
        ],
        max_file_bytes=10,
    )

    result = ingest_knowledge_sources(config)

    assert result.chunks == []
    assert any("skipped large file" in warning for warning in result.warnings)


def test_chunks_to_graph_creates_nodes_edges_and_dedupes() -> None:
    chunk = DocumentChunk(
        chunk_id="docs/hex.md#1-3-0",
        source_id="docs",
        source_type="project_doc",
        path="docs/hex.md",
        title="Hex",
        section="HexLattice",
        text="HexLattice rings and OPENMC_CROSS_SECTIONS.",
        start_line=1,
        end_line=3,
        doc_refs=["openmc.usersguide.geometry"],
        api_refs=["openmc.HexLattice"],
        concept_ids=["openmc.geometry.hex_lattice"],
        schema_paths=["LatticeSpec.rings"],
        metadata={
            "annotation_method": "rules",
            "issue_codes": ["runtime.cross_sections_missing"],
            "benchmark_refs": ["benchmark.vera"],
            "example_refs": ["example.docs_hex"],
        },
    )

    nodes, edges = chunks_to_graph([chunk, chunk])

    assert len([node for node in nodes if node.metadata.get("node_subtype") == "doc_chunk"]) == 1
    edge_keys = {(edge.source, edge.target, edge.relation) for edge in edges}
    assert any(target == "concept.openmc.geometry.hex_lattice" and relation == "mentions" for _, target, relation in edge_keys)
    assert any(target == "api.openmc.HexLattice" and relation == "mentions" for _, target, relation in edge_keys)
    assert any(target == "schema.LatticeSpec.rings" and relation == "related_to" for _, target, relation in edge_keys)
    assert any(target == "issue.runtime.cross_sections_missing" and relation == "related_to" for _, target, relation in edge_keys)


def test_graph_lookup_discovers_ingested_doc_chunk(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text("# Hex\nHexLattice rings outer universe.\n", encoding="utf-8")
    result = ingest_knowledge_sources(
        KnowledgeIngestionConfig(
            sources=[
                KnowledgeSource(
                    source_id="docs",
                    source_type="project_docs",
                    root_path=str(docs),
                    include_globs=["**/*.md"],
                )
            ]
        )
    )

    context = graph_lookup(
        GraphLookupRequest(concept_ids=["openmc.geometry.hex_lattice"], max_depth=1),
        extra_nodes=result.graph_nodes,
        extra_edges=result.graph_edges,
    )

    assert any(node.metadata.get("node_subtype") == "doc_chunk" for node in context.nodes)
    assert any("HexLattice" in hint or "hex_lattice" in hint for hint in context.retrieval_hints)


def test_graphrag_uses_ingested_hex_doc_chunk(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text(
        "# HexLattice\nHexLattice rings use an outer_universe_id.\n",
        encoding="utf-8",
    )
    ingestion = ingest_knowledge_sources(
        KnowledgeIngestionConfig(
            sources=[
                KnowledgeSource(
                    source_id="docs",
                    source_type="project_docs",
                    root_path=str(docs),
                    include_globs=["**/*.md"],
                )
            ]
        )
    )
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    result = graphrag_retrieve(
        request,
        extra_nodes=ingestion.graph_nodes,
        extra_edges=ingestion.graph_edges,
    )

    assert result.evidence
    assert result.evidence[0].metadata.get("doc_chunk_id")
    assert result.evidence[0].metadata.get("annotation_method") == "rules"


def test_graphrag_uses_ingested_cross_sections_doc_chunk(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "data.md").write_text(
        "# Data\nOPENMC_CROSS_SECTIONS points to cross_sections.xml and requires user confirmation.\n",
        encoding="utf-8",
    )
    ingestion = ingest_knowledge_sources(
        KnowledgeIngestionConfig(
            sources=[
                KnowledgeSource(
                    source_id="docs",
                    source_type="project_docs",
                    root_path=str(docs),
                    include_globs=["**/*.md"],
                )
            ]
        )
    )
    request = graphrag_request_from_issues(
        [issue_from_catalog("runtime.cross_sections_missing")]
    )

    result = graphrag_retrieve(
        request,
        extra_nodes=ingestion.graph_nodes,
        extra_edges=ingestion.graph_edges,
    )

    assert result.evidence
    assert result.evidence[0].metadata["requires_human_confirmation"] is True
    assert result.evidence[0].metadata.get("doc_chunk_id")


def test_graphrag_without_extra_graph_still_works(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text("# Hex\nHexLattice rings.\n", encoding="utf-8")
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    ).model_copy(update={"search_roots": [str(docs)]})

    result = graphrag_retrieve(request)

    assert result.graph_context is not None
    assert result.evidence


def test_save_and_load_ingested_graph(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text("# Hex\nHexLattice rings.\n", encoding="utf-8")
    result = ingest_knowledge_sources(
        KnowledgeIngestionConfig(
            sources=[
                KnowledgeSource(
                    source_id="docs",
                    source_type="project_docs",
                    root_path=str(docs),
                    include_globs=["**/*.md"],
                )
            ]
        )
    )
    out = tmp_path / "knowledge"

    save_knowledge_ingestion_result(result, out)
    nodes, edges = load_ingested_graph(out)

    assert (out / "knowledge_chunks.json").exists()
    assert (out / "knowledge_chunks.jsonl").exists()
    assert (out / "knowledge_summary.json").exists()
    assert nodes
    assert edges


def test_cli_writes_knowledge_files_and_allows_missing_root(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text("# Hex\nHexLattice rings.\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "docs",
                        "source_type": "project_docs",
                        "root_path": str(docs),
                        "include_globs": ["**/*.md"],
                    },
                    {
                        "source_id": "missing",
                        "source_type": "project_docs",
                        "root_path": str(tmp_path / "missing"),
                        "include_globs": ["**/*.md"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "openmc_agent.knowledge_ingestion",
            "--manifest",
            str(manifest),
            "--output",
            str(out),
        ],
        cwd=Path.cwd(),
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert (out / "knowledge_chunks.json").exists()
    assert (out / "knowledge_graph_nodes.json").exists()
    assert (out / "knowledge_graph_edges.json").exists()
    assert (out / "knowledge_summary.json").exists()
    summary = json.loads((out / "knowledge_summary.json").read_text(encoding="utf-8"))
    assert summary["chunk_count"] >= 1
    assert summary["warnings"]
