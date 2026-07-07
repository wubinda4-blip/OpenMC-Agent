# GraphRAG Retriever Strategy

## Responsibility

The GraphRAG retriever adds a graph-guided document retrieval layer for repair
and reflection prompts. It starts from structured `ValidationIssue` fields,
expands the maintained local knowledge graph, converts the subgraph into a
bounded RAG request, and returns `RetrievedEvidence` with `source_type="graphrag"`.

GraphRAG evidence is context, not authority. It can explain API usage, schema
relationships, and documented repair constraints, but it must not confirm or
invent nuclear data paths, material densities, material compositions, or
benchmark-specific constants.

## Relationship To Existing Layers

- grep evidence locates direct code, tests, examples, and text matches.
- graph context explains maintained relationships between issues, schema fields,
  OpenMC concepts, APIs, docs, examples, and repair policies.
- plain RAG retrieves local documentation from graph hints and issue queries.
- GraphRAG combines graph expansion and RAG retrieval into one evidence layer:
  graph first, document retrieval second.

The Retrieval Orchestrator keeps these layers separate and orders prompt
evidence as grep, graph, GraphRAG, then plain RAG.

## Why Local Deterministic GraphRAG First

The current MVP is offline and reproducible. It uses the hand-maintained graph
registry and the existing lexical RAG index. There is no vector store, no graph
database, no network access, and no LLM-driven tool ordering. This keeps the
retrieval behavior testable and makes later vector or file-search backends
pluggable without changing prompt semantics.

## Flow

1. Build a `GraphRagRequest` from issues, grep evidence, and optional
   `GraphContext`.
2. Optionally run the GraphRAG query planner to classify intent, choose start
   nodes, set expansion policy, rank planned paths, and build preferred
   queries/filters.
3. Resolve issue codes, schema paths, concept ids, and graph start nodes.
4. Expand a bounded graph subcontext with `graph_lookup(...)`.
5. Extract short graph path explanations for prompt display.
6. Build a graph-guided `RagSearchRequest` using related doc refs, API refs,
   example refs, concept ids, schema paths, retrieval hints, and query-plan
   filters.
7. Run local lexical `rag_search(...)`.
8. Convert chunks to `RetrievedEvidence(source_type="graphrag")` and attach
   graph metadata.

## RetrievalPolicy Switches

- `enable_graphrag`: defaults to `True`; GraphRAG runs after graph context and
  before plain RAG unless a caller disables it explicitly.
- `enable_graphrag_query_planner`: defaults to `True`; planning is skipped only
  when explicitly disabled or when GraphRAG itself is disabled.
- `prefer_graphrag_over_rag`: when `True`, plain RAG is skipped if GraphRAG
  produced evidence.
- `max_graphrag_evidence`: bounds GraphRAG prompt evidence.
- `max_planned_graph_paths`: bounds planned path summaries kept for prompt and
  trace.

If `enable_rag=False` and `enable_graphrag=True`, GraphRAG may still run because
its document retrieval is part of the graph-guided layer. The orchestrator
records this distinction in `skipped_steps`.

## Current Limits

- No vector search.
- No OpenAI file search.
- No Neo4j or external graph database.
- Graph path scoring is deterministic and shallow; no community detection or
  global graph summarization.
- No automatic ontology generation.
- No fact confirmation for material density, composition, nuclear data paths,
  or benchmark constants.
- No renderer expansion; HexAssemblyRenderer, depletion, and pebble-bed
  workflows remain out of scope.

## Future Extensions

- Runtime loading of persisted knowledge ingestion assets.
- Vector store or hybrid lexical/vector retrieval.
- OpenAI file search backend.
- Learned or benchmark-calibrated graph path reranking.
- VERA and benchmark documentation ingestion.
- Benchmark and evaluation platform for GraphRAG comparisons.
