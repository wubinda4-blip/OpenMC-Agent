# Plan Investigation — Phase 8A Step 1 Design

## Status

**Foundation ready.** Step 1 ships the data layer only; no LLM, no graph
wiring, no tool surface. Subsequent steps (Step 2: local read-only
investigation tools; Step 3+: research-aware gate retry) build on this
layer.

## Why a new evidence ledger (and not the existing retrieval outcome)

The existing `openmc_agent.retrieval.RetrievalOutcome` is a free-form
`findings: str` plus a JSON Patch — optimised for the existing
generate/reflect LLM loop. It cannot serve as the substrate for
research-aware planning because:

1. **No provenance.** Findings are prose; there is no machine-checkable
   link back to a specific span of a specific source document.
2. **No typed status.** "What the document says", "what was derived",
   "what we assumed", "what is unresolved", and "what is in conflict"
   are indistinguishable.
3. **No conflict model.** Two LLM calls can silently disagree with no
   record left behind.
4. **No deterministic re-computation.** Arithmetic in findings text
   cannot be re-verified by Python.
5. **No hash.** Findings have no stable identity, so resume / checkpoint
   semantics cannot reason about them.

Step 1 introduces a typed `PlanningEvidenceLedger` that closes all five
gaps while remaining reactor-neutral and pure-Python.

## Data model relationships

```
SourceDocument ─┬─ SourceSection (heading-delimited region; always has a
                │                    synthetic root)
                └─ SourceSpan       (hash-verified line range excerpt)

EvidenceClaim ──┬─ EvidenceSourceRef     (points to a SourceSpan)
                ├─ EvidenceDerivation    (deterministic re-computation)
                └─ status enum           (explicit / derived /
                                          assumption / unresolved /
                                          external_official / conflict)

PlanningEvidenceLedger ─┬─ claims       (dict[claim_id, EvidenceClaim])
                        ├─ derivations  (dict[derivation_id, ...])
                        ├─ conflicts    (dict[conflict_id, EvidenceConflict])
                        └─ ledger_hash  (deterministic SHA-256 over the
                                         sorted canonical JSON payload)
```

## Source normalization rules

`normalize_source_text(text)` applies, in order:

1. UTF-8 BOM removal (`\ufeff`).
2. CRLF and lone CR → LF.
3. Unicode NFC.
4. Trailing-newline normalisation: the canonical text always ends with
   exactly one `\n` when non-empty (zero trailing newlines or many
   trailing newlines both collapse to one).

The normalizer does **not** fold whitespace, strip per-line content,
delete blank lines, or change case.  Line numbering is therefore stable
across platform newline variants while preserving every byte that
matters for code blocks and Markdown tables.

## ID generation rules

| ID            | Prefix   | Determined by                                                            |
| ------------- | -------- | ------------------------------------------------------------------------ |
| `source_id`   | `src_`   | `sha256(source_kind · normalized_title · normalized_content_hash)[:16]` |
| `span_id`     | `span_`  | `sha256(source_id · [start,end] · excerpt_hash)[:16]`                    |
| `section_id`  | `sec_`   | `sha256(source_id · level · section_path · [start,end])[:16]`            |
| `claim_id`    | `claim_` | `sha256(subject · predicate · qualifiers · value · status · source_refs · derivation_present · criticality)[:16]` |
| `derivation_id` | `der_` | `sha256(operation · input_claim_ids · parameters · result_hash)[:16]`    |
| `conflict_id` | `conflict_` | `sha256(semantic_key · sorted claim_ids)[:16]`                       |

Empty-string IDs are the "auto-fill" sentinel: callers may pass `""` and
let Python compute the deterministic value.  Any non-empty ID MUST
match the recomputed value or construction is rejected.

## Section indexing behaviour

* ATX headings (`#`..`######`) start new sections.
* Fenced code blocks (``` or ~~~) suppress heading detection inside.
* Headings inside fences are not indexed.
* Empty/whitespace headings are recorded as empty-string headings.
* Repeated headings get distinct `section_id`s (range differs).
* Markdown tables do not perturb line numbering.
* Documents without any heading get a single synthetic root spanning
  the whole document.
* Empty documents get zero sections; span construction against them is
  rejected.

## EvidenceClaim types and what each one means

| Status                    | Requires                                                                      | What it represents |
| ------------------------- | ---------------------------------------------------------------------------- | ------------------ |
| `explicit`                | `source_refs` non-empty + all refs validate; no `derivation`.                | "Source document says X at lines Y–Z." |
| `deterministically_derived` | `derivation` present + inputs exist + no cycle + result re-computes.       | "Python computed this from other claims." |
| `external_official`       | **Disabled in Step 1** (rejected at construction).                          | Reserved for future web/docs retrieval. |
| `assumption`              | `confirmed_by_human=False`; never satisfies `source_critical`.              | "Modeler assumes X; must be confirmed." |
| `unresolved`              | No fabricated `source_refs`; never satisfies `source_critical`.             | "Predicate is unknown." |
| `conflict`                | Only emitted by `detect_conflicts`; cannot be constructed directly.         | "Two or more candidates disagree." |

## Semantic key and claim ID

The **semantic key** of a claim is `sha256(subject · predicate · canonical_qualifiers)`.
Two claims with the same semantic key but different values are *different
claims* (their `claim_id`s differ because `value` is part of the
`claim_id` payload) and become candidates for a conflict.

`claim_id` is intentionally sensitive to `value` so that conflicting
candidates never collapse into a single record. Metadata, timestamps,
artifact paths and run ids are NOT part of `claim_id`.

## Derivation verification

Each `EvidenceDerivation` carries an `operation` from the allow-list:

| Operation          | Recompute rule |
| ------------------ | -------------- |
| `integer_product`  | product of input claim values (each must be int) + optional `parameters.operands` |
| `integer_sum`      | sum of input claim values + optional `parameters.operands` |
| `matrix_shape`     | `[rows, cols]` of one input claim whose value is a list-of-lists |
| `count_by_label`   | `{label: count}` of one input claim; supports one-level nesting; optional `parameters.only` filter |
| `equality_alias`   | asserts all inputs equal; returns the shared value |
| `interval_length`  | `hi - lo` of one input claim shaped as `[lo, hi]` or `{"lo", "hi"}` |

`add_derivation` recomputes the result by Python and rejects the
derivation if `result_hash != sha256(recomputed_value)`. `eval`, `exec`,
and arbitrary code execution are never permitted.

## Conflict detection

`detect_conflicts(ledger)` groups claims by semantic key and emits one
`EvidenceConflict` per group whose candidate values disagree. All
candidates are preserved verbatim; Step 1 performs **no** auto-resolution
by source precedence. Conflicts start with
`resolution_status=unresolved` and stay that way until a later step
applies explicit policy or human confirmation.

## Human-confirmed immutability

When a claim has `confirmed_by_human=True`:

* `human_confirmation_id` MUST be non-empty.
* `upsert_claim` rejects any subsequent mutation that changes the
  semantic payload (`subject`, `predicate`, `qualifiers`, `value`,
  `status`, `source_refs`, `criticality`).
* Re-upserting an identical payload is idempotent.
* A new conflicting candidate for the same semantic key is recorded as
  a separate claim and surfaces via `detect_conflicts`; it does NOT
  overwrite the confirmed one.

## Ledger hash

`recompute_ledger_hash(ledger) = sha256(canonical_json({ledger_version,
requirement_hash, sorted(source_index_hashes), sorted claims by id,
sorted derivations by id, sorted conflicts by id,
sorted(unresolved_claim_ids), sorted(source_critical_claim_ids),
sorted(tool_call_ids), sorted(human_confirmation_ids)}))`.

The hash is **independent of insertion order, timestamps and artifact
paths**. After `finalize_ledger`, `ledger.ledger_hash` equals
`recompute_ledger_hash(ledger)`. Loading the finalized ledger from JSON
and recomputing yields the same hash.

## Artifact layout

`write_plan_investigation_artifacts(output_dir, source_indexes, ledger)`
writes six canonical JSON files under
`<output_dir>/workflow/investigation/`:

| File                       | Contents                                                       |
| -------------------------- | -------------------------------------------------------------- |
| `source_manifest.json`     | Per-source metadata (no body text).                            |
| `source_index.json`        | Sections + per-line text/hashes per source.                    |
| `evidence_ledger.json`     | Full ledger (claims, derivations, conflicts, summary fields). |
| `evidence_conflicts.json`  | Conflicts list (may be empty).                                 |
| `unresolved_claims.json`   | Claims with `status=unresolved`.                               |
| `investigation_summary.json` | `EvidenceLedgerSummary` roll-up + hashes.                    |

Writes are atomic (tmp file + `replace`); failures raise
`PlanInvestigationIssue` rather than being swallowed. The writer refuses
to emit a ledger whose `ledger_hash` is stale.

## PlanBuildState compatibility

Three optional fields were added to `PlanBuildState`:

* `planning_source_manifest: dict | None`
* `planning_evidence_ledger: dict | None`
* `plan_investigation_schema_version: str | None`

All default to `None`; legacy checkpoints load unchanged. Central
access helpers live in `openmc_agent.plan_investigation.state_compat`;
callers MUST go through those helpers rather than hand-writing
`metadata["..."]` keys. Nothing in the graph, gate lifecycle, patch
generator or renderer reads or writes these fields in Step 1.

## Security boundaries (Step 1 guarantees)

* No LLM client is imported or invoked.
* No `subprocess`, `os.system`, `eval`, `exec`, or network code in the
  package.
* Prompt-injection text inside a source document is treated as inert
  data; the indexer never interprets or executes it.
* `external_official` evidence is rejected at construction.
* `repository`, `openmc_docs`, `official_web` source kinds are rejected
  at `build_source_index`.
* Source spans cannot be fabricated: `excerpt_hash` is recomputed by
  Python and `span_id` is derived from `(source_id, range, hash)`.
* Artifacts do not embed host home-directory paths, API keys, or full
  prompts.

## How this supports Facts, Inventory and Gate Research (later steps)

* The Facts generator will consume `explicit` + `assumption` claims and
  promote them to confirmed facts once a human signs off (the
  immutability rule protects signed facts).
* GeometryComponentInventory will consume `deterministically_derived`
  claims about layout shape and label counts (Step 2 will add the
  operations needed for more complex aggregations).
* Research-aware gate retry will read `unresolved_source_critical_claims`
  to decide which gates need additional investigation and will record
  new evidence via `add_claim`.

## Why Step 1 does not wire the Graph

The Graph nodes, gate lifecycle, patch generator and renderer all have
hard contracts (Plan contract 0.8, five-gate lifecycle, BLOCKED
transitions). Wiring a new evidence surface into them in the same step
as introducing it would force a multi-axis change with compounding
risk. Step 1 ships the substrate; Step 2+ can iterate on integration
with confidence that the data layer is stable.

## Privacy and artifact policy

Artifacts embed the user-supplied source text (because it is the
user's own problem statement, not a host secret). They do not embed:

* API keys or other credentials.
* Absolute host paths (no `/home/...` leakage).
* Full LLM prompts.
* Gold/reference data (Step 1 cannot read repositories).

If a future step adds attached-document uploads beyond the requirement
text, the design calls for a separate `sources/<source_id>.txt` file
with an explicit privacy boundary note.

## Step 2 extension hooks

The following surfaces are intentionally reserved for Step 2+:

* `SourceKind.REPOSITORY`, `OPENMC_DOCS`, `OFFICIAL_WEB`: enabled by
  adding read-only tool functions that produce source indexes.
* `EvidenceStatus.EXTERNAL_OFFICIAL`: enabled by lifting the
  construction-time policy guard (e.g. when an allow-listed web/docs
  retriever is present).
* `tool_call_ids` ledger slot: populated when LLM tool dispatch is wired
  in.  The slot is already part of `ledger_hash` so adding it does not
  break resume.
* Regex search and tree-sitter parsing: additive primitives on top of
  `SourceIndex`; the existing `find_literal` / `find_keywords` show the
  pattern.
