# Knowledge Runtime Loader Strategy

The runtime loader connects persisted knowledge-graph assets (produced by the
[ingestion pipeline](knowledge_ingestion_strategy.md)) to the live retrieval
runtime so GraphRAG can fold them into graph expansion at workflow time.

## Purpose

Before this layer, ingestion wrote `data/knowledge/*.json` but the default
workflow never read it. GraphRAG only saw the hand-written registry in
`knowledge_graph_registry.py`. The runtime loader closes that gap: it loads
persisted `GraphNode`/`GraphEdge` records once per retrieval context and passes
them to GraphRAG as `extra_nodes`/`extra_edges`.

It is deliberately thin:

- No vector search, no embeddings, no neural reranker.
- No OpenAI file search, no graph database.
- No LLM annotation, no network calls, no new dependencies.
- Loading is optional; a missing or malformed knowledge directory never breaks
  the workflow.
- Loaded assets are evidence/context only and never confirm physics facts.

## How Ingestion Output Reaches The Workflow

1. The ingestion CLI writes `knowledge_graph_nodes.json`,
   `knowledge_graph_edges.json`, `knowledge_chunks.json(.jsonl)`, and
   `knowledge_summary.json` into a directory (default `data/knowledge`).
2. At retrieval time, `gather_retrieval_context_for_issues` builds a
   `KnowledgeGraphLoadConfig` from the active `RetrievalPolicy` and calls
   `load_knowledge_graph_store`.
3. The store is loaded **once per retrieval context** (not per issue). Its
   nodes/edges are passed only to the GraphRAG stage as
   `extra_nodes`/`extra_edges`.
4. Only a compact `summary` and `warnings` are stored on `RetrievalContext`;
   the full node/edge lists never reach `RetrievalContext` or the workflow
   trace.

The hand-written registry remains primary. Ingested nodes only expand visible
document knowledge and never override existing nodes.

## Configuring The Knowledge Directory

Resolution order (first wins):

1. `RetrievalPolicy.knowledge_graph_path`
2. The `OPENMC_AGENT_KNOWLEDGE_DIR` environment variable
3. Nothing — the store is returned unloaded with no warning (legacy behavior)

```bash
# Environment variable fallback
export OPENMC_AGENT_KNOWLEDGE_DIR=data/knowledge
```

```bash
# Inspect CLI
python -m openmc_agent.inspect --plan --knowledge-dir data/knowledge "..."
```

`build_plan_graph` also accepts the parameters directly:

```python
from openmc_agent.graph import build_plan_graph
from openmc_agent.retrieval_orchestrator import RetrievalPolicy

graph = build_plan_graph(
    knowledge_graph_path="data/knowledge",
    # or pass a full policy:
    # retrieval_policy=RetrievalPolicy(knowledge_graph_path="data/knowledge"),
)
```

When both are given, `knowledge_graph_path` is folded into the policy only if
the policy does not already specify one (explicit policy wins).

## RetrievalPolicy Fields

| Field | Default | Meaning |
|---|---|---|
| `enable_knowledge_graph_loading` | `True` | Gate the whole loader |
| `knowledge_graph_path` | `None` | Explicit asset directory |
| `max_knowledge_nodes` | `5000` | Truncate nodes above this |
| `max_knowledge_edges` | `20000` | Truncate edges above this |
| `allow_missing_knowledge_path` | `True` | Tone of the missing-path warning |

When GraphRAG is disabled (`enable_graphrag=False`) the loader is skipped
entirely and a `graphrag disabled by policy` step is recorded.

## Trace Summary Fields

`summarize_retrieval_context` (in `workflow_trace.py`) exposes:

- `knowledge_graph_attempted`
- `knowledge_graph_loaded`
- `knowledge_graph_node_count`
- `knowledge_graph_edge_count`
- `knowledge_graph_source_ids`
- `knowledge_graph_warning_count`

The `retrieval_completed` trace event also carries these counts in its
metadata. Full node/edge payloads are never written to the trace.

GraphRAG evidence coming from an ingested `doc_chunk` is stamped with
`knowledge_runtime_loaded` and `knowledge_graph_path` (without overwriting
existing keys), alongside the existing `knowledge_source`, `doc_chunk_id`,
`annotation_method`, and `ingested_graph_node_id` metadata.

## Fallback Behavior

| Situation | Result |
|---|---|
| No path and no env var | `loaded=False`, no warning, workflow unchanged |
| Missing directory | `loaded=False`, warning, GraphRAG falls back to registry |
| Malformed JSON / schema mismatch | `loaded=False`, warning, no crash |
| Nodes/edges exceed limits | truncated to limits, warning, `full_*_count` preserved |
| GraphRAG disabled | loader not invoked, `skipped_steps` records it |
| Any unexpected exception | captured as a warning, workflow continues |

## Safety Boundaries

- Knowledge assets are **evidence only**. They locate code, APIs, documentation
  interpretation, and example patterns.
- They do **not** confirm material density, composition, nuclear-data paths,
  benchmark constants, or loading maps. Fact gaps still require human
  confirmation.
- The renderer capability boundary is unchanged; unsupported subsystems remain
  skeleton or human-confirmation paths.

## Current Limitations

- No vector / embedding search; retrieval remains lexical and graph-guided.
- No OpenAI file search or external file store.
- No graph database (Neo4j, etc.); assets are plain JSON on local disk.
- No LLM annotation at load time; only the deterministic rule annotations from
  ingestion are available.
- Loaded nodes augment but never replace the hand-written registry.

## Future Extensions

- Vector backend adapter behind the same loader interface.
- OpenAI file search adapter for hosted knowledge.
- A lightweight real-evaluation case runner consuming the trace summary.
- Loading-map fidelity checks against ingested benchmark pin maps.
- Renderer expansion (hex assembly, depletion, pebble bed) as separate work.
