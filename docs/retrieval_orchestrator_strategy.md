# Retrieval Orchestrator Strategy

## Role

The Retrieval Orchestrator coordinates issue-triggered retrieval for repair and
reflection prompts. It turns structured `ValidationIssue` records into a bounded
`RetrievalContext` containing grep, graph, GraphRAG, plain RAG, ranked evidence,
warnings, skipped steps, and compact trace statistics.

The orchestrator does not modify `SimulationPlan`, choose renderer capability,
or turn retrieved text into confirmed nuclear facts.

## Current Pipeline

The default pipeline is deterministic:

```text
issues
  -> grep
  -> graph
  -> GraphRAG query planner
  -> GraphRAG retriever
  -> plain RAG
  -> merge evidence
  -> evidence ranker / dedup / prompt budget
  -> prompt sections
```

Tool ordering is not LLM-controlled.

## Relationship To Retrieval Tools

- `grep_search` locates direct source, test, example, and document snippets. It
  is locator context, not a physical fact source.
- `graph_lookup` expands stable issue codes, schema paths, concepts, and grep
  anchors into maintained relationships, repair policies, doc refs, API refs,
  examples, and retrieval hints.
- `graphrag_query_planner` classifies issue intent, chooses graph start nodes,
  selects graph expansion policy, scores graph paths, and builds preferred
  queries/filters.
- `graphrag_retriever` runs graph-guided document retrieval and returns
  `RetrievedEvidence(source_type="graphrag")`.
- `rag_search` performs plain local lexical document retrieval from graph and
  issue hints.
- `evidence_ranker` deduplicates, scores, truncates, and budgets all retrieved
  evidence before prompt formatting.

## Issue-Triggered Decisions

`decide_retrieval_for_issue` decides whether an issue should run grep, graph, or
document retrieval.

Grep runs for issues with grep patterns, repair/retrieval/manual routes, and
runtime/export/hex lattice error codes. Cross-section fact gaps also run grep by
default when they have safe patterns such as `OPENMC_CROSS_SECTIONS` or
`cross_sections.xml`, but the result is locator/documentation context only.

Graph runs when an issue has stable anchors: code, schema path, concept id, or
grep evidence.

RAG/GraphRAG run for explicit retrieval issues, manual-review issues, runtime
geometry overlap, lost particle, unknown runtime diagnostics, hex lattice
issues, graph contexts with doc/API/example refs or retrieval hints, and fact
gap documentation lookups. Fact gaps such as cross-section configuration or
material composition preserve human confirmation semantics.

## Evidence Merge And Ranking

Raw evidence is merged in priority order:

```text
grep evidence -> graph evidence -> GraphRAG evidence -> plain RAG evidence
```

When `enable_evidence_ranking=True`, the merged list is passed to
`rank_and_select_evidence(...)`. Ranked evidence is then preferred for prompts.
Ranking preserves exact grep matches, prefers GraphRAG over duplicate plain RAG
chunks, applies issue/schema/concept relevance, and enforces per-source and
total prompt budgets.

## Prompt Formatting

When ranked evidence is available, `format_retrieval_context(...)` emits:

```text
[GraphRAG Query Plan]
[Graph Context]
[Ranked Evidence]
[Evidence Safety Constraints]
```

If ranking is disabled or unavailable, it falls back to the legacy raw sections:

```text
[GraphRAG Query Plan]
[Grep Evidence]
[Graph Context]
[GraphRAG Evidence]
[RAG Evidence]
[Evidence Safety Constraints]
```

Empty sections are omitted.

## Policy Switches

Important `RetrievalPolicy` fields:

- `enable_grep`
- `enable_graph`
- `enable_graphrag`
- `enable_graphrag_query_planner`
- `prefer_graphrag_over_rag`
- `enable_rag`
- `enable_evidence_ranking`
- `run_rag_for_manual_review`
- `skip_rag_for_fact_gap`
- `skip_grep_for_cross_sections_missing`
- `max_grep_evidence`
- `max_graph_evidence`
- `max_graphrag_evidence`
- `max_rag_evidence`
- `max_ranked_evidence`
- `max_evidence_prompt_chars`

## auto-repair And ask_expert Boundaries

Deterministic auto-repair still runs before retrieval-backed reflection. If
auto-repair succeeds, no LLM repair is forced.

Issues routed to `ask_expert`, especially fact gaps that require human
confirmation, are not widened into reflect-plan repair by the orchestrator.
Retrieval evidence may explain the API or documentation context, but it must not
invent nuclear data paths, material densities, nuclide compositions, benchmark
constants, or missing loading maps.

## Current Limits

- Knowledge ingestion output is not yet automatically loaded into the runtime
  orchestrator by default.
- Fact-gap retrieval is documentation-only. It reduces unnecessary questions
  about API/configuration mechanics, but still cannot confirm missing physical
  facts.
- GraphRAG and evidence ranking are deterministic heuristics, not learned
  rerankers.
- No vector store or OpenAI file search.
- No external graph database.
- No persistent trace store.
- No `SimulationPlan` mutation inside the orchestrator.
- No fact-gap confirmation.
- No `HexAssemblyRenderer`, depletion/burnup, or pebble-bed renderer changes.

## Next Extensions

- Runtime loader for ingested knowledge assets (`data/knowledge`).
- Retrieval configuration through CLI/env vars.
- Real workflow case runner for benchmark and ablation studies.
- Vector / OpenAI file-search backend behind the same evidence contract.
- Learned or benchmark-calibrated graph path and evidence weights.
