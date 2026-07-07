"""Runtime loader for persisted knowledge-graph assets.

This module wires offline ingestion output (e.g. ``data/knowledge``) into the
live retrieval runtime. Loading is optional, deterministic, and side-effect
free: a missing path, malformed JSON, oversized graph, or schema mismatch
degrades to a warning and never breaks the workflow. Loaded assets are evidence
and graph context only; they never confirm material density, composition,
nuclear-data paths, benchmark constants, or loading maps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import Field

from openmc_agent.knowledge_graph import GraphEdge, GraphNode
from openmc_agent.knowledge_ingestion import load_ingested_graph
from openmc_agent.schemas import AgentBaseModel


KNOWLEDGE_DIR_ENV_VAR = "OPENMC_AGENT_KNOWLEDGE_DIR"
_SUMMARY_FILENAME = "knowledge_summary.json"


class KnowledgeGraphLoadConfig(AgentBaseModel):
    """Controls whether and how persisted knowledge assets are loaded."""

    knowledge_graph_path: str | None = None
    enable_knowledge_graph_loading: bool = True
    max_knowledge_nodes: int = 5000
    max_knowledge_edges: int = 20000
    allow_missing_knowledge_path: bool = True


class KnowledgeGraphStore(AgentBaseModel):
    """Holds loaded knowledge nodes/edges plus a compact summary.

    Nodes and edges stay on this object (never on RetrievalContext or the
    workflow trace) so large graphs do not leak into persisted state.
    """

    path: str | None = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    loaded: bool = False


def load_knowledge_graph_store(
    config: KnowledgeGraphLoadConfig | None = None,
) -> KnowledgeGraphStore:
    """Load persisted knowledge graph nodes/edges for runtime retrieval.

    Resolution order: explicit ``config.knowledge_graph_path`` first, then the
    ``OPENMC_AGENT_KNOWLEDGE_DIR`` environment variable. When neither is set the
    store is returned unloaded with no warning, preserving legacy workflow
    behaviour. Any failure (missing path, parse error, schema mismatch) is
    captured as a warning rather than raised.
    """
    active_config = config or KnowledgeGraphLoadConfig()

    if not active_config.enable_knowledge_graph_loading:
        return KnowledgeGraphStore(
            summary=_empty_summary(attempted=False),
            warnings=["knowledge graph loading disabled by config"],
        )

    raw_path = active_config.knowledge_graph_path or os.environ.get(KNOWLEDGE_DIR_ENV_VAR)
    if not raw_path:
        return KnowledgeGraphStore(summary=_empty_summary(attempted=False))

    path = Path(raw_path)
    if not path.exists():
        if active_config.allow_missing_knowledge_path:
            message = f"knowledge graph path does not exist: {path}"
        else:
            message = (
                f"knowledge graph path missing and allow_missing_knowledge_path=False: {path}"
            )
        return KnowledgeGraphStore(
            path=str(path),
            summary=_empty_summary(attempted=True, path=str(path)),
            warnings=[message],
        )

    try:
        nodes, edges = load_ingested_graph(path)
    except Exception as exc:  # pragma: no cover - defensive: covered by invalid-JSON test path
        return KnowledgeGraphStore(
            path=str(path),
            summary=_empty_summary(attempted=True, path=str(path)),
            warnings=[f"failed to load knowledge graph from {path}: {exc}"],
        )

    warnings: list[str] = []
    truncated_nodes = nodes
    if len(nodes) > active_config.max_knowledge_nodes:
        truncated_nodes = nodes[: active_config.max_knowledge_nodes]
        warnings.append(
            f"knowledge graph nodes truncated: {len(nodes)} -> {len(truncated_nodes)}"
        )
    truncated_edges = edges
    if len(edges) > active_config.max_knowledge_edges:
        truncated_edges = edges[: active_config.max_knowledge_edges]
        warnings.append(
            f"knowledge graph edges truncated: {len(edges)} -> {len(truncated_edges)}"
        )

    ingestion_summary = _read_summary(path)
    store_summary = _build_store_summary(
        path=str(path),
        nodes=truncated_nodes,
        edges=truncated_edges,
        ingestion_summary=ingestion_summary,
        full_node_count=len(nodes),
        full_edge_count=len(edges),
    )
    return KnowledgeGraphStore(
        path=str(path),
        nodes=truncated_nodes,
        edges=truncated_edges,
        summary=store_summary,
        warnings=warnings,
        loaded=True,
    )


def knowledge_graph_load_config_from_policy(policy: Any) -> KnowledgeGraphLoadConfig:
    """Build a load config from a RetrievalPolicy-like object.

    Accepts any object exposing the knowledge-graph policy fields so the
    orchestrator does not need to import the concrete policy type.
    """
    return KnowledgeGraphLoadConfig(
        knowledge_graph_path=getattr(policy, "knowledge_graph_path", None),
        enable_knowledge_graph_loading=bool(
            getattr(policy, "enable_knowledge_graph_loading", True)
        ),
        max_knowledge_nodes=int(getattr(policy, "max_knowledge_nodes", 5000)),
        max_knowledge_edges=int(getattr(policy, "max_knowledge_edges", 20000)),
        allow_missing_knowledge_path=bool(
            getattr(policy, "allow_missing_knowledge_path", True)
        ),
    )


def _empty_summary(*, attempted: bool, path: str | None = None) -> dict[str, Any]:
    return {
        "attempted": attempted,
        "loaded": False,
        "path": path,
        "node_count": 0,
        "edge_count": 0,
        "source_ids": [],
        "chunk_count": 0,
        "concept_count": 0,
    }


def _build_store_summary(
    *,
    path: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    ingestion_summary: dict[str, Any],
    full_node_count: int,
    full_edge_count: int,
) -> dict[str, Any]:
    source_counts = ingestion_summary.get("source_counts", {})
    source_ids = sorted(
        {str(value) for value in source_counts.keys() if value}
        if isinstance(source_counts, dict)
        else set()
    )
    concept_counts = ingestion_summary.get("concept_counts", {})
    concept_count = (
        int(sum(concept_counts.values())) if isinstance(concept_counts, dict) else 0
    )
    return {
        "attempted": True,
        "loaded": True,
        "path": path,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "full_node_count": full_node_count,
        "full_edge_count": full_edge_count,
        "source_ids": source_ids,
        "chunk_count": int(ingestion_summary.get("chunk_count", 0) or 0),
        "concept_count": concept_count,
    }


def _read_summary(path: Path) -> dict[str, Any]:
    summary_path = (
        path / _SUMMARY_FILENAME if path.is_dir() else path.parent / _SUMMARY_FILENAME
    )
    if not summary_path.exists():
        return {}
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
