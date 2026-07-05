# Retrieval Orchestrator Strategy

## Role

The Retrieval Orchestrator coordinates issue-triggered retrieval for repair and
reflection prompts. It turns structured `ValidationIssue` records into a bounded
`RetrievalContext` containing grep requests/results, graph context, RAG document
evidence, merged evidence, warnings, skipped steps, and a compact trace summary.

The orchestrator does not modify `SimulationPlan`, does not choose renderer
capabilities, and does not turn retrieved text into confirmed nuclear facts.

## Relationship To Retrieval Tools

The pipeline is deterministic:

```text
issues -> grep -> graph -> RAG -> merge -> prompt sections
```

The orchestrator coordinates these tools:

- `grep_search` locates nearby source, test, example, and document text. It is
  locator context, not a physical fact source.
- `graph_lookup` expands stable issue codes, schema paths, concepts, and grep
  anchors into maintained relationships, repair policies, doc refs, API refs,
  and retrieval hints.
- `rag_search` searches local documentation chunks using graph-guided lexical
  signals. It provides documentation evidence for API usage, syntax, and
  explanations.

Tool internals remain independent so each layer can be tested and replaced
without changing the orchestrator contract.

## Issue-Triggered Decisions

`decide_retrieval_for_issue` decides whether an issue should run grep, graph, or
RAG.

Grep runs for issues with grep patterns, repair/retrieval/manual routes, and
runtime/export/hex lattice error codes. Cross-section fact gaps skip grep by
default to avoid prompting the LLM to invent local paths.

Graph runs when an issue has stable anchors: code, schema path, concept id, or
grep evidence.

RAG runs for explicit retrieval issues, runtime geometry overlap, lost particle,
unknown runtime diagnostics, hex lattice issues, or graph contexts with doc/API
refs or retrieval hints. Fact gaps such as cross-section configuration or
material composition skip RAG by default when they require human confirmation.

## Evidence Merge

Evidence is merged in prompt priority order:

```text
grep evidence -> graph evidence -> RAG evidence
```

This keeps direct code/test/example locators ahead of relationship metadata, and
keeps RAG document context from crowding out more direct evidence. Duplicates are
removed by source type, locator, and text similarity.

## reflect_plan Integration

`graph.py` now calls:

```python
retrieval_context = gather_retrieval_context_for_issues(report.issues)
retrieval_prompt = format_retrieval_context(retrieval_context)
```

The formatted prompt can contain:

```text
[Grep Evidence]
[Graph Context]
[RAG Evidence]
```

Empty sections are omitted. Legacy state fields (`grep_evidence`,
`graph_context`, `rag_evidence`) are still populated from `RetrievalContext` for
compatibility with existing traces and tests.

## auto-repair And ask_expert Boundaries

Deterministic auto-repair still runs before retrieval-backed reflection. If
auto-repair succeeds, no LLM repair is forced.

Issues routed to `ask_expert`, especially fact gaps that require human
confirmation, are not widened into reflect-plan repair by the orchestrator. The
graph may still preserve human-confirmation relationships, but retrieval
evidence must not be used to invent nuclear data paths, material densities,
nuclide compositions, or benchmark constants.

## Current Limits

- No GraphRAG.
- No vector store.
- No OpenAI file search.
- No network access.
- No persistent trace store.
- No automatic schema/docs-to-graph generation.
- No automatic document concept tagging.
- No `SimulationPlan` mutation.
- No fact-gap confirmation.
- No `HexAssemblyRenderer`, depletion/burnup, or pebble-bed renderer changes.

## Future Extensions

The `RetrievalContext` shape is intended to support:

- GraphRAG over maintained concepts and local documents.
- OpenAI file search.
- Local vector stores such as FAISS or LanceDB.
- BM25 scoring.
- Retrieval evaluation dashboards.
- Persistent trace storage.
- Automatic document concept annotation.
