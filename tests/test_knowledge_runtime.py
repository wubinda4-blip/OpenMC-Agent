"""Tests for the knowledge-graph runtime loader and retrieval config.

Covers: loader fallback behaviour, orchestrator integration (extra graph
injected into GraphRAG), runtime-loaded evidence provenance, trace summary
fields, and the build_plan_graph / CLI wiring.
"""

from __future__ import annotations
import pytest

pytestmark = pytest.mark.openmc
openmc = pytest.importorskip(
    "openmc", reason="OpenMC is required for this integration test"
)

from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graph import build_plan_graph
from openmc_agent.graphrag_retriever import (
    GraphRagResult,
    graphrag_request_from_issues,
    graphrag_retrieve,
)
from openmc_agent.knowledge_ingestion import (
    KnowledgeIngestionConfig,
    KnowledgeSource,
    ingest_knowledge_sources,
    save_knowledge_ingestion_result,
)
from openmc_agent.knowledge_runtime import (
    KNOWLEDGE_DIR_ENV_VAR,
    KnowledgeGraphLoadConfig,
    load_knowledge_graph_store,
)
from openmc_agent.retrieval_orchestrator import (
    RetrievalContext,
    RetrievalPolicy,
    gather_retrieval_context_for_issues,
)
from openmc_agent.workflow_trace import summarize_retrieval_context


def _build_knowledge_dir(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hex.md").write_text(
        "# HexLattice\nHexLattice rings use an outer_universe_id and orientation.\n",
        encoding="utf-8",
    )
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
    return out, result


# --- loader tests ---------------------------------------------------------


def test_loader_no_path_no_env_returns_unloaded(monkeypatch) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    store = load_knowledge_graph_store(KnowledgeGraphLoadConfig())

    assert store.loaded is False
    assert store.nodes == []
    assert store.edges == []
    assert store.warnings == []
    assert store.summary["attempted"] is False
    assert store.summary["loaded"] is False


def test_loader_valid_dir_loads_nodes_and_edges(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    out, result = _build_knowledge_dir(tmp_path)

    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(out))
    )

    assert store.loaded is True
    assert len(store.nodes) == len(result.graph_nodes)
    assert len(store.edges) == len(result.graph_edges)
    assert store.summary["loaded"] is True
    assert store.summary["node_count"] == len(result.graph_nodes)
    assert store.summary["edge_count"] == len(result.graph_edges)
    assert store.summary["source_ids"] == ["docs"]
    assert store.summary["chunk_count"] >= 1


def test_loader_missing_dir_warns_without_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    missing = tmp_path / "does_not_exist"

    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(missing))
    )

    assert store.loaded is False
    assert store.warnings
    assert any("does not exist" in warning for warning in store.warnings)
    assert store.summary["attempted"] is True
    assert store.summary["loaded"] is False


def test_loader_invalid_json_warns_without_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    bad = tmp_path / "knowledge"
    bad.mkdir()
    (bad / "knowledge_graph_nodes.json").write_text("{not valid json", encoding="utf-8")
    (bad / "knowledge_graph_edges.json").write_text("[]", encoding="utf-8")

    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(bad))
    )

    assert store.loaded is False
    assert store.warnings
    assert any("failed to load" in warning for warning in store.warnings)


def test_loader_truncates_to_max_limits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    out, result = _build_knowledge_dir(tmp_path)
    assert len(result.graph_nodes) > 1

    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(
            knowledge_graph_path=str(out),
            max_knowledge_nodes=1,
            max_knowledge_edges=1,
        )
    )

    assert store.loaded is True
    assert len(store.nodes) == 1
    assert len(store.edges) == 1
    assert any("nodes truncated" in w for w in store.warnings)
    assert any("edges truncated" in w for w in store.warnings)
    assert store.summary["node_count"] == 1
    assert store.summary["full_node_count"] == len(result.graph_nodes)


def test_loader_reads_env_var(tmp_path: Path, monkeypatch) -> None:
    out, _ = _build_knowledge_dir(tmp_path)
    monkeypatch.setenv(KNOWLEDGE_DIR_ENV_VAR, str(out))

    store = load_knowledge_graph_store(KnowledgeGraphLoadConfig())

    assert store.loaded is True
    assert store.path == str(out)


def test_loader_explicit_path_overrides_env_var(tmp_path: Path, monkeypatch) -> None:
    out, _ = _build_knowledge_dir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(KNOWLEDGE_DIR_ENV_VAR, str(other))

    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(out))
    )

    assert store.loaded is True
    assert store.path == str(out)


def test_loader_disabled_returns_unloaded_with_warning() -> None:
    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(enable_knowledge_graph_loading=False)
    )

    assert store.loaded is False
    assert store.warnings == ["knowledge graph loading disabled by config"]


# --- orchestrator integration tests --------------------------------------


def test_orchestrator_passes_knowledge_graph_to_graphrag(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    out, _ = _build_knowledge_dir(tmp_path)
    captured: dict = {}

    def fake_graphrag_retrieve(request, **kwargs):
        captured["extra_nodes"] = kwargs.get("extra_nodes")
        captured["extra_edges"] = kwargs.get("extra_edges")
        captured["runtime_knowledge"] = dict(request.runtime_knowledge)
        return GraphRagResult(request=request, evidence=[])

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")],
        policy=RetrievalPolicy(enable_rag=False, knowledge_graph_path=str(out)),
    )

    assert context.knowledge_graph_summary["loaded"] is True
    assert context.knowledge_graph_summary["node_count"] >= 1
    assert captured["extra_nodes"]
    assert captured["extra_edges"]
    assert captured["runtime_knowledge"]["knowledge_runtime_loaded"] is True
    assert captured["runtime_knowledge"]["knowledge_graph_path"] == str(out)


def test_orchestrator_without_knowledge_dir_uses_handwritten_graph(
    monkeypatch,
) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    captured: dict = {}

    def fake_graphrag_retrieve(request, **kwargs):
        captured["extra_nodes"] = kwargs.get("extra_nodes")
        return GraphRagResult(request=request, evidence=[])

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        fake_graphrag_retrieve,
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")]
    )

    assert context.knowledge_graph_summary.get("attempted") is False
    assert context.knowledge_graph_summary.get("loaded") is False
    assert captured["extra_nodes"] is None
    assert context.graphrag_request is not None


def test_orchestrator_invalid_knowledge_dir_warns_without_crash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    bad = tmp_path / "knowledge"
    bad.mkdir()
    (bad / "knowledge_graph_nodes.json").write_text("{bad", encoding="utf-8")
    (bad / "knowledge_graph_edges.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        lambda request, **_kw: GraphRagResult(request=request, evidence=[]),
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(knowledge_graph_path=str(bad)),
    )

    assert context.knowledge_graph_warnings
    assert context.knowledge_graph_summary["attempted"] is True
    assert context.knowledge_graph_summary["loaded"] is False
    assert context.graphrag_request is not None


def test_orchestrator_graphrag_disabled_skips_knowledge_load(
    tmp_path: Path, monkeypatch
) -> None:
    out, _ = _build_knowledge_dir(tmp_path)
    load_calls = {"count": 0}
    real_load = load_knowledge_graph_store

    def tracking_load(config):
        load_calls["count"] += 1
        return real_load(config)

    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.load_knowledge_graph_store",
        tracking_load,
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(enable_graphrag=False, knowledge_graph_path=str(out)),
    )

    assert load_calls["count"] == 0
    assert "graphrag disabled by policy" in context.skipped_steps
    assert context.knowledge_graph_summary == {}


# --- evidence metadata tests ---------------------------------------------


def test_graphrag_evidence_marks_runtime_loaded_source(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    out, _ = _build_knowledge_dir(tmp_path)
    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(out))
    )

    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    ).model_copy(
        update={
            "runtime_knowledge": {
                "knowledge_runtime_loaded": True,
                "knowledge_graph_path": str(out),
            },
        }
    )

    result = graphrag_retrieve(
        request, extra_nodes=store.nodes, extra_edges=store.edges
    )

    assert result.evidence
    metadata = result.evidence[0].metadata
    assert metadata.get("knowledge_runtime_loaded") is True
    assert metadata.get("knowledge_graph_path") == str(out)
    assert metadata.get("doc_chunk_id")
    assert metadata.get("annotation_method") == "rules"


def test_graphrag_evidence_without_runtime_metadata_leaves_flag_false(
    tmp_path: Path,
) -> None:
    out, _ = _build_knowledge_dir(tmp_path)
    store = load_knowledge_graph_store(
        KnowledgeGraphLoadConfig(knowledge_graph_path=str(out))
    )
    request = graphrag_request_from_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    result = graphrag_retrieve(
        request, extra_nodes=store.nodes, extra_edges=store.edges
    )

    if result.evidence:
        assert result.evidence[0].metadata.get("knowledge_runtime_loaded") is False


# --- trace summary tests -------------------------------------------------


def test_trace_summary_includes_knowledge_graph_counts() -> None:
    context = RetrievalContext(
        knowledge_graph_summary={
            "attempted": True,
            "loaded": True,
            "node_count": 10,
            "edge_count": 20,
            "source_ids": ["docs"],
        },
        knowledge_graph_warnings=["a warning"],
    )

    summary = summarize_retrieval_context(context)

    assert summary["knowledge_graph_attempted"] is True
    assert summary["knowledge_graph_loaded"] is True
    assert summary["knowledge_graph_node_count"] == 10
    assert summary["knowledge_graph_edge_count"] == 20
    assert summary["knowledge_graph_source_ids"] == ["docs"]
    assert summary["knowledge_graph_warning_count"] == 1


def test_trace_summary_empty_context_defaults() -> None:
    summary = summarize_retrieval_context(None)

    assert summary["knowledge_graph_attempted"] is False
    assert summary["knowledge_graph_loaded"] is False
    assert summary["knowledge_graph_node_count"] == 0
    assert summary["knowledge_graph_edge_count"] == 0
    assert summary["knowledge_graph_source_ids"] == []
    assert summary["knowledge_graph_warning_count"] == 0


def test_retrieval_context_does_not_persist_full_graph_nodes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(KNOWLEDGE_DIR_ENV_VAR, raising=False)
    out, _ = _build_knowledge_dir(tmp_path)
    monkeypatch.setattr(
        "openmc_agent.retrieval_orchestrator.graphrag_retrieve",
        lambda request, **_kw: GraphRagResult(request=request, evidence=[]),
    )

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("runtime.geometry_overlap")],
        policy=RetrievalPolicy(knowledge_graph_path=str(out)),
    )

    dump = context.model_dump(mode="json")
    assert "nodes" not in dump
    assert "edges" not in dump
    kg_summary = dump["knowledge_graph_summary"]
    assert "nodes" not in kg_summary
    assert "edges" not in kg_summary
    assert "node_count" in kg_summary


# --- env var / build_plan_graph wiring tests -----------------------------


def test_env_var_drives_orchestrator_load(tmp_path: Path, monkeypatch) -> None:
    out, _ = _build_knowledge_dir(tmp_path)
    monkeypatch.setenv(KNOWLEDGE_DIR_ENV_VAR, str(out))

    context = gather_retrieval_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")],
        policy=RetrievalPolicy(enable_rag=False),
    )

    assert context.knowledge_graph_summary["loaded"] is True
    assert context.knowledge_graph_summary["path"] == str(out)


def test_build_plan_graph_accepts_knowledge_graph_path(tmp_path: Path) -> None:
    out, _ = _build_knowledge_dir(tmp_path)

    compiled = build_plan_graph(knowledge_graph_path=str(out))

    assert compiled is not None


def test_build_plan_graph_accepts_retrieval_policy() -> None:
    compiled = build_plan_graph(
        retrieval_policy=RetrievalPolicy(enable_graphrag=False)
    )

    assert compiled is not None
