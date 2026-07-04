# Knowledge Graph Strategy

## Role

The lightweight graph layer connects stable diagnostic codes, schema paths,
OpenMC concepts, grep evidence, docs, APIs, examples, and repair policies.
It is a relationship and routing layer only.

It does not replace validation, grep, RAG, or expert confirmation. It must not
be treated as a source of physical facts such as material density, composition,
benchmark parameters, or cross section paths.

## Node Types

- `schema_model` and `schema_field`: maintained links to Pydantic IR fields.
- `validation_rule` and `validation_issue`: stable validator/error-code entry
  points.
- `openmc_concept`: OpenMC or OpenMC-agent concepts such as hex lattices,
  material density, cell fill, or renderability.
- `openmc_api`, `doc_ref`, and `example_ref`: retrieval filters and references
  for later RAG.
- `renderer_capability`: capability boundary nodes such as skeleton/runnable.
- `runtime_error`: OpenMC runtime diagnostics.
- `repair_policy`: routing policies such as `auto_repair`, `reflect_plan`,
  `ask_expert`, `manual_review`, and `capability_downgrade`.

## Edge Types

Edges use a small controlled relation set: `represents`, `validated_by`,
`raises`, `related_to`, `documented_in`, `implemented_by`, `demonstrated_by`,
`supports`, `downgrades_to`, `routes_to`, `repairs_with`, `mentions`, and
`aliases`.

The current implementation uses bounded BFS over this local registry. It does
not perform complex reasoning.

## Error Code To Concept Example

`lattice.hex.renderer_unsupported` expands to:

- `schema.LatticeSpec.kind`
- `schema.LatticeSpec.rings`
- `openmc.geometry.hex_lattice`
- `openmc_agent.renderability`
- repair policy `capability_downgrade`
- doc/API hints `openmc.usersguide.geometry` and `openmc.api.HexLattice`

This tells reflection that hex lattice support remains skeleton-only and should
not be promoted to runnable.

## Grep Match To Graph Node Example

A grep match like:

```text
openmc_agent/schemas.py:430-450
matched_pattern: outer_universe_id
```

can resolve to `schema.LatticeSpec.outer_universe_id`, which expands to
`openmc.geometry.hex_lattice`, `openmc.geometry.lattice`, and the relevant
OpenMC docs/API hints.

## Reflect Plan Integration

`reflect_plan` now builds graph context from:

- `ValidationIssue.code`
- `ValidationIssue.schema_path`
- `ValidationIssue.concept_id`
- `ValidationIssue.grep_patterns`
- grep evidence locators, matched patterns, and symbol hints

The prompt receives a compact `[Graph Context]` section with start nodes,
related schema paths, concepts, error codes, docs, API refs, example refs,
repair policies, and retrieval hints.

Graph context is explicitly labeled as relationship metadata, not final physics
truth.

## Future RAG Integration

The graph layer already exposes these fields for later retrieval:

- `GraphContext.related_doc_refs`
- `GraphContext.related_api_refs`
- `GraphContext.related_example_refs`
- `GraphContext.retrieval_hints`

A later RAG step can use these as filters before reading OpenMC docs, API pages,
or examples.

## Current Limits

- The registry is manually maintained Python data.
- Expansion is bounded BFS only.
- No external graph database is used.
- No vector search, RAG, or GraphRAG is implemented.
- The graph does not auto-generate nodes from schema or docs.
- The graph never modifies `SimulationPlan`.
- Unsupported renderer capabilities, including hex lattice rendering, remain
  skeleton-only unless a renderer is implemented in a later step.
