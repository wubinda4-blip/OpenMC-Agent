# GraphRAG Query Planner Strategy

The GraphRAG query planner is a deterministic pre-retrieval layer. It decides
what the retrieval is trying to accomplish before graph expansion and lexical
RAG run.

## Why A Planner

The first GraphRAG MVP used a common bounded graph expansion and generic query
generation for all issue types. That works, but runtime diagnostics, export XML
reference repairs, lattice-map mismatches, renderer capability boundaries, and
fact gaps need different graph and document context.

The planner narrows retrieval before the evidence ranker sees results.

## Intent Classification

`classify_graphrag_intent(...)` maps structured `ValidationIssue` records to
one intent:

- `schema_repair`
- `runtime_diagnosis`
- `export_xml_repair`
- `lattice_map_repair`
- `renderer_capability`
- `documentation_lookup`
- `fact_gap_review`
- `benchmark_interpretation`
- `unknown`

When multiple issues are present, the highest-priority intent wins. Fact gaps
are highest priority so missing material data, nuclear data paths, composition,
or benchmark constants are not converted into auto-fill retrieval tasks.

## Expansion Policy

`expansion_policy_for_intent(...)` sets:

- graph depth and node budget;
- preferred node types;
- preferred relations;
- avoided node types and relations;
- whether examples, API docs, repair policies, and benchmark docs are useful;
- `fact_gap_safe_mode`.

For example, `runtime_diagnosis` can search deeper and include examples/API
docs, while `fact_gap_review` stays shallow and documentation-only.

## Start Nodes

`start_nodes_for_intent(...)` combines:

- `issue.<code>`;
- `schema.<schema_path>`;
- `concept.<concept_id>`;
- previous `GraphContext.start_nodes`;
- targeted supplements for lattice maps, hex lattices, geometry overlap, and
  cross-section/nuclear-data issues.

The list is bounded and deduplicated.

## Graph Path Scoring

`score_graph_path(...)` scores short paths using:

- issue/schema/concept hits;
- preferred node types;
- preferred repair policies for repair intents;
- allowed doc/API/example targets;
- path length;
- avoided node/relation penalties;
- fact-gap unsafe penalties.

Scores are deterministic and clamped to `[0, 1]`.

## Query And Filter Generation

`build_queries_from_plan(...)` produces:

- `preferred_queries`;
- `required_filters` for concept ids, schema paths, doc refs, and API refs;
- `avoided_queries` for fact-gap unsafe language.

Examples:

- lattice-map repair asks for pin maps, loading patterns, overrides, and
  expected counts;
- runtime diagnosis asks for geometry overlap, region expressions, surfaces, or
  lost-particle context;
- fact-gap review asks for documentation context and avoids guessing density,
  composition, paths, or benchmark constants.

## RetrievalPolicy

The orchestrator exposes:

- `enable_graphrag_query_planner`;
- `max_planned_graph_paths`.

Planner output is stored in `GraphRagRequest.query_plan` and summarized in
retrieval/trace statistics.

## Prompt Output

`format_retrieval_context(...)` can include:

```text
[GraphRAG Query Plan]
intent=lattice_map_repair
start_nodes=...
preferred_queries=...
selected_paths=...
safety=fact_gap_safe_mode false
```

The query plan explains why GraphRAG searched a topic. It is not evidence by
itself.

## Relationship To Evidence Ranking

The Query Planner runs before retrieval. It narrows graph expansion and RAG
queries.

The Evidence Ranker runs after retrieval. It deduplicates, scores, and budgets
the returned evidence.

## Current Limits

- heuristic only;
- no embedding;
- no neural reranker;
- no graph database;
- no fact confirmation.

## Future Work

- vector search;
- OpenAI file search;
- learned graph path reranker;
- benchmark-driven weights;
- ontology refinement.
