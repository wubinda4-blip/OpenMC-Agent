# Knowledge Ingestion Strategy

Knowledge ingestion converts local project documents, examples, input case notes,
and optional downloaded OpenMC documentation into structured assets that local
GraphRAG can use.

## Responsibilities

- Scan configured local sources such as `docs/`, `examples/`, `Input/`,
  `openmc_docs/`, `openmc_examples/`, and `README.md`.
- Reuse the existing local RAG `DocumentChunk` model and chunking behavior.
- Apply deterministic annotation rules for OpenMC concepts, APIs, schema paths,
  issue codes, examples, and benchmark/input references.
- Convert annotated chunks into graph-compatible `GraphNode` and `GraphEdge`
  records.
- Persist chunks, graph nodes, graph edges, and summary counts as JSON/JSONL.

## Relationship To Local RAG

Local RAG indexes files directly and scores chunks lexically at query time.
Knowledge ingestion prepares the same kind of chunks ahead of time and enriches
them with metadata. The output can still be searched by the existing RAG layer,
but it also creates graph nodes that GraphRAG can traverse from concepts,
schema fields, APIs, or issue codes.

## Relationship To GraphRAG

GraphRAG can receive ingested graph nodes and edges as optional `extra_nodes`
and `extra_edges`. The maintained hand-written registry remains primary. The
ingested graph only expands visible document knowledge and does not override
existing nodes. When GraphRAG reaches a `doc_chunk` node, its path, concepts,
API refs, schema paths, issue codes, and annotation metadata are carried into
GraphRAG evidence.

## Annotation Rules

The first version uses high-precision local rules. It covers:

- Materials: `openmc.Material`, `set_density`, nuclide/element composition,
  thermal scattering, macroscopic materials, and cross sections.
- Geometry: cells, universes, surfaces, region boolean expressions, and
  boundary types.
- Lattices: `RectLattice`, `HexLattice`, pitch, universe patterns, pin maps,
  rings, and outer universes.
- Settings/execution: settings, batches, inactive, particles, export XML,
  smoke tests, and geometry debug.
- Runtime/export issues: cross sections missing, geometry overlap, lost
  particles, dangling references, missing materials, and pin map/count
  mismatches.
- Benchmarks/input cases: VERA, CASL, C5G7, BEAVRS, Watts Bar, pin-cell,
  assembly/core, MOX/UO2, guide tubes, fission chambers, control rods, and
  reflectors.

The rules intentionally prefer precision over recall.

## Supported Sources And Files

Supported file extensions are `.md`, `.rst`, `.txt`, `.py`, `.json`, `.yaml`,
`.yml`, and `.toml`. The scanner skips `.git`, Python caches, virtualenv-like
directories, HDF5 files, XML files, large files, and non-UTF-8/binary files.

## Manifest

The default manifest is `Input/knowledge_sources.json`:

```json
{
  "sources": [
    {
      "source_id": "project_docs",
      "source_type": "project_docs",
      "root_path": "docs",
      "include_globs": ["**/*.md"]
    }
  ]
}
```

Missing source roots are warnings, not hard failures.

## CLI

```bash
python -m openmc_agent.knowledge_ingestion \
  --manifest Input/knowledge_sources.json \
  --output data/knowledge
```

Useful options:

- `--root PATH` adds CLI-defined sources instead of the manifest sources.
- `--include PATTERN` and `--exclude PATTERN` control glob matching.
- `--max-file-bytes N` skips larger files.
- `--no-json` skips JSON chunk/node/edge files.
- `--no-jsonl` skips JSONL chunk output.

## Output Files

- `knowledge_chunks.json`
- `knowledge_chunks.jsonl`
- `knowledge_graph_nodes.json`
- `knowledge_graph_edges.json`
- `knowledge_summary.json`

The summary includes chunk, node, edge, source, concept, API, schema path, and
warning counts.

## Boundaries

Knowledge ingestion is documentation context only. It does not confirm nuclear
data paths, material densities, compositions, benchmark constants, or other fact
gaps. Those still require explicit source confirmation or human review.

Current limitations:

- No network access.
- No embeddings or vector database.
- No OpenAI file search.
- No Neo4j or external graph database.
- No LLM-based annotation.
- No automatic renderer expansion.

## Future Extensions

- Vector search over ingested chunks.
- OpenAI file search integration.
- Ontology refinement and broader concept coverage.
- Graph path reranking.
- VERA and benchmark document ingestion after local PDF/text extraction.
- Benchmark and evaluation platform integration.
