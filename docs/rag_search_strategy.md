# RAG Search Strategy

The RAG layer provides local documentation evidence for plan reflection and
repair. It turns structured issues and `GraphContext` hints into bounded
document chunks that can be shown to the LLM as context.

## Role

RAG evidence explains local documentation, examples, API usage, and nearby
project notes. It is evidence for syntax and interpretation, not an authority
for nuclear data, material densities, compositions, or benchmark-specific
physical constants.

## RAG vs Grep

Grep is precise locator retrieval. It searches code, tests, examples, and docs
for issue-specific patterns and returns exact line context.

RAG is document evidence retrieval. It chunks local documentation and examples,
then scores chunks using doc refs, API refs, concept ids, schema paths, issue
codes, and retrieval queries.

## RAG vs Graph

The graph layer maintains relationships between validation issues, schema
fields, OpenMC concepts, APIs, docs, examples, and repair policies. It produces
`related_doc_refs`, `related_api_refs`, `related_example_refs`, and
`retrieval_hints`.

The RAG layer consumes those hints and searches local files for relevant
documentation chunks. It does not generate graph nodes and does not infer new
facts from documents.

## Current Implementation

`openmc_agent.rag_search` builds an in-memory index from local files. Default
roots are:

- `docs/`
- `examples/`
- `openmc_docs/`
- `openmc_examples/`
- `README.md`

Supported file types are Markdown, reStructuredText, text, Python, JSON, YAML,
and TOML. The index excludes git metadata, virtual environments, caches, Python
bytecode, HDF5 statepoints, and large files.

Markdown and RST files are chunked by headings, Python files are chunked around
top-level functions/classes, and plain text files are split by bounded line
groups. Metadata is extracted with simple keyword rules for common OpenMC
concepts such as `HexLattice`, `Material.set_density`,
`OPENMC_CROSS_SECTIONS`, and geometry overlap diagnostics.

Scoring is lexical and deterministic. Explicit `doc_refs` and `api_refs` score
highest, followed by concept ids, retrieval hints, schema paths, issue-code
tokens, and general text matches. There are no embeddings, vector stores,
external services, or network calls.

## Safety Boundaries

RAG evidence is local documentation context only.

- Do not use RAG to invent nuclear data paths.
- Do not use RAG to invent material density, nuclide composition, or benchmark
  parameters.
- Fact gaps still require `ask_expert` or explicit user confirmation.
- Human-confirmation semantics are preserved even when documentation explains
  the surrounding API or configuration concept.

## Prompt Integration

Plain RAG is now one layer inside the Retrieval Orchestrator. The current
default ordering is:

1. grep evidence for direct code/test/example/document hits;
2. graph context for maintained relationships and retrieval hints;
3. GraphRAG query planning and GraphRAG evidence;
4. plain RAG evidence as a fallback or supplement;
5. evidence ranking, deduplication, and prompt budgeting.

When ranked evidence is available, `reflect_plan` usually sees `[Ranked
Evidence]` instead of a full raw `[RAG Evidence]` dump. If ranking is disabled
or unavailable, the prompt can still include `[RAG Evidence]` as a raw fallback
section.

## Relationship To GraphRAG

GraphRAG builds on the same local RAG primitives. It first uses graph start
nodes, planned paths, doc/API refs, concept ids, and schema paths to build a
more precise `RagSearchRequest`; then it converts retrieved chunks into
`RetrievedEvidence(source_type="graphrag")`.

Plain RAG remains useful when GraphRAG is disabled, when graph evidence is too
sparse, or when a direct document lookup is sufficient.

## Future Extensions

The current interfaces can still be extended with:

- OpenAI vector stores or file search;
- BM25 ranking;
- FAISS or LanceDB;
- automatic documentation concept tagging beyond deterministic ingestion rules;
- automatic graph construction from schema and docs.

This layer still does not implement vector search, OpenAI file search,
`HexAssemblyRenderer`, depletion, burnup, or pebble-bed rendering.
