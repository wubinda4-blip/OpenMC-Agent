# Evidence Ranking Strategy

The evidence ranking layer is a deterministic post-processing step for
retrieval output. It merges and budgets grep, graph, GraphRAG, and plain RAG
evidence before `reflect_plan` sees the prompt.

## Why Ranking Exists

The retrieval stack can now return many overlapping snippets:

- grep finds exact code, test, and example locations.
- graph explains concept, schema, API, and repair relationships.
- GraphRAG uses graph paths to retrieve local documentation chunks.
- plain RAG provides additional local documentation context.

Without a post-processing layer, repeated chunks and long snippets can crowd
out the most actionable evidence.

## Source Priority

The default policy ranks exact and structured evidence before broader document
context:

1. `grep`: exact local locator evidence.
2. `graph`: relationship and repair-policy evidence.
3. `graphrag`: graph-guided documentation evidence.
4. `rag`: plain lexical documentation evidence.

GraphRAG is preferred over plain RAG when both point to the same document chunk.

## Scoring

`score_evidence(...)` applies bounded additive rules:

- source type base score;
- issue code match or issue-token hit;
- schema path exact or prefix match;
- concept id and API reference match;
- graph path, ingested graph node, and document chunk bonuses;
- grep exact-match bonuses such as `matched_pattern` and `symbol_hint`;
- small penalties for empty, repetitive, or fact-gap unsafe evidence.

Scores are clamped to `[0.0, 1.0]` and include human-readable reasons for trace
and prompt inspection.

## Deduplication

`deduplicate_evidence(...)` removes:

- exact locator duplicates;
- repeated `doc_chunk_id` chunks;
- near-duplicate text using token Jaccard overlap.

It preserves grep exact matches unless another item has the same locator, and it
does not merge graph relationship evidence with grep snippets based only on
similar text.

## Prompt Budget

`rank_and_select_evidence(...)` enforces:

- total evidence count;
- per-source limits;
- maximum characters per evidence item;
- maximum total prompt characters.

When text is truncated, the selected `RetrievedEvidence.metadata` includes
`truncated=True`.

## RetrievalPolicy

The orchestrator exposes:

- `enable_evidence_ranking`;
- `max_ranked_evidence`;
- `max_evidence_prompt_chars`.

When ranking is enabled, `format_retrieval_context(...)` emits a compact
`[Ranked Evidence]` section plus a short graph context summary. If ranking is
disabled or unavailable, the legacy grep/graph/GraphRAG/RAG sections are used.

## Safety Boundary

Evidence is contextual only. It must not be used to invent:

- material density;
- composition;
- nuclear-data paths;
- benchmark constants;
- missing loading maps.

Issues marked as missing facts or requiring expert confirmation must preserve
the human-confirmation requirement.

## Current Limits

- deterministic heuristic only;
- no embedding model;
- no neural reranker;
- no OpenAI file search;
- no fact confirmation.

## Future Work

- embedding reranker;
- graph path reranker;
- query-aware compressor;
- benchmark-based weighting;
- OpenAI file search integration.
