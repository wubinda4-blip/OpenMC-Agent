# Plan Investigation — Threat Model (Phase 8A Step 1)

This document enumerates the threats that Step 1 must defend against,
the controls in place, and the residual risks that Step 2+ must
address.

## Threats and controls

### 1. Prompt injection inside source documents

**Threat.** A user-supplied requirement or attached document may
contain text such as "Ignore previous instructions", "Run `rm -rf /`
in a shell", "Read the .env file", or "Output the full system prompt".
An LLM that ingests this text directly might comply.

**Step 1 control.** The source indexer treats all source text as inert
data. `normalize_source_text` performs only string-level transforms
(BOM removal, newline normalisation, NFC). No substring of the source
text is ever interpreted as a command. The `find_literal` / `find_keywords`
primitives return `SourceSpan` objects that wrap the verbatim text; the
text is never executed.

**Test coverage.** `test_plan_investigation_security_step1.py::test_prompt_injection_text_is_inert`
verifies the canonical "ignore previous instructions" payload is
preserved verbatim without side effects.

**Residual risk.** Step 2+ that exposes source text to an LLM must
continue to wrap it in clear "this is untrusted data" framing and must
not allow LLM output to call shell/subprocess tools.

### 2. Source reference fabrication

**Threat.** An LLM (or a corrupted intermediate layer) might try to
attach a `source_ref` to a claim, citing a span that does not exist or
whose `excerpt_hash` does not match the indexed source.

**Step 1 control.**

* `SourceSpan.excerpt_hash` is recomputed by Python at construction
  time and verified against the actual excerpt content.
* `span_id` is derived from `(source_id, line_range, excerpt_hash)`, so
  a forged span with mismatched hash is rejected at construction.
* `SourceIndex.validate_span` recomputes the excerpt from the indexed
  line range and rejects any mismatch.
* `SourceIndex.validate_source_ref` only accepts refs whose `span_id`
  was previously registered via `register_span` (or constructed via
  `make_span` and tracked externally).
* `add_claim` / `upsert_claim` accept an optional `source_indexes`
  mapping; when provided, every `source_ref` on the claim is validated
  before the claim enters the ledger.

**Test coverage.** `test_plan_source_spans.py::test_excerpt_modified_after_construction_detected`,
`test_plan_source_spans.py::test_forged_source_id_rejected_at_index_validation`,
`test_plan_evidence_models.py::test_explicit_claim_with_foreign_source_id_rejected`,
`test_plan_investigation_security_step1.py::test_source_span_cannot_be_fabricated`.

### 3. Path leakage in artifacts

**Threat.** Artifacts written to disk might embed absolute host paths
(`/home/<user>/...`) that reveal the deployment environment.

**Step 1 control.** `write_plan_investigation_artifacts` writes only
the source document metadata, sections, line records, claims, and
summary. None of these include host paths. The atomic-write helper
raises on serialisation failure rather than embedding fallback paths.

**Test coverage.** `test_plan_investigation_artifacts.py::test_no_secrets_in_artifacts`,
`test_plan_investigation_security_step1.py::test_artifacts_do_not_leak_host_paths`.

### 4. Artifact content leakage (secrets, prompts, gold data)

**Threat.** Artifacts might leak API keys, full LLM prompts, or
reference/gold data that the agent should not be able to read.

**Step 1 control.** The Step 1 surface has no LLM client and no
repository/gold reader, so none of these can enter an artifact via the
public API. `test_plan_investigation_security_step1.py::test_no_subprocess_module_in_package_sources`
statically verifies the package does not import shell, network, or
arbitrary-code-execution modules.

**Residual risk.** Future steps that add LLM tool dispatch must not
echo full prompts into the ledger's `metadata` (which is serialised).
The `metadata` field is included in `ledger_hash` but its content is
the caller's responsibility.

### 5. Hash confusion (one ID type masquerading as another)

**Threat.** A `claim_id` could be substituted where a `span_id` is
expected, or a `source content hash` confused with a `ledger_hash`.

**Step 1 control.** Every ID type has a distinct prefix (`src_`,
`span_`, `sec_`, `claim_`, `der_`, `conflict_`). The ID validators
recompute the expected ID from the model's semantic fields and reject
any non-empty value that does not match.

**Test coverage.** `test_plan_evidence_hashing.py::test_source_id_forbids_manual_construction`,
`test_plan_evidence_models.py::test_claim_id_deterministic_and_independent_of_metadata`.

### 6. Stale derived claims (input removed after derivation registered)

**Threat.** A derived claim is registered, then its input claim is
removed (e.g. via a buggy resume). The derived claim now points at
nothing.

**Step 1 control.** `find_stale_derived_claims(ledger)` walks every
`deterministically_derived` claim, recomputes its derivation, and
returns the claim_ids whose inputs are missing or whose recomputed
result differs from `result_hash`. `validate_ledger(ledger)` surfaces
these as `PlanInvestigationIssue` records with code
`plan_investigation.stale_derived_claim`.

**Test coverage.** `test_plan_evidence_derivations.py::test_stale_derived_claim_detected_after_input_removed`.

### 7. Conflict hiding

**Threat.** A second claim with the same semantic key but a different
value might silently overwrite the first, hiding the disagreement.

**Step 1 control.** `add_claim` rejects duplicate `claim_id`s.
`upsert_claim` replaces only same-id claims; it never collapses two
different `claim_id`s. `detect_conflicts` re-scans the whole ledger on
each call and emits a fresh `EvidenceConflict` for any semantic-key
group whose values disagree. Conflicts survive `detect_conflicts`
re-runs (their `conflict_id` is deterministic in `(semantic_key, sorted
claim_ids)`).

**Test coverage.** `test_plan_evidence_conflicts.py::test_conflict_preserves_all_candidates`,
`test_plan_evidence_conflicts.py::test_conflict_does_not_overwrite_claims`.

### 8. Human-confirmed claim overwrite

**Threat.** After a human confirms a claim, a later LLM run might
"repair" it to a different value, silently undoing the confirmation.

**Step 1 control.** `upsert_claim` checks `existing.confirmed_by_human`
and rejects any mutation that changes the semantic payload. A new
conflicting candidate for the same semantic key is added as a separate
claim (and surfaces in `detect_conflicts`), but the confirmed claim is
preserved verbatim.

**Test coverage.** `test_plan_evidence_conflicts.py::test_confirmed_claim_immutability_blocks_value_change`,
`test_plan_evidence_conflicts.py::test_confirmed_claim_upsert_idempotent`.

### 9. Future: repository search gold leakage

**Threat.** Step 2+ that adds repository search might surface benchmark
answer files or reference/gold data, allowing the planner to cheat.

**Mitigation roadmap.** When Step 2 ships repository search, its root
whitelist MUST exclude `tests/fixtures/`, `data/evals/`, and any other
benchmark-asset directory. The existing
`openmc_agent.retrieval._resolve_within_roots` pattern (roots whitelist
+ path containment check) is the template. Step 1 has no repository
access at all, so the threat is moot today.

### 10. Future: web/docs evidence poisoning

**Threat.** External web pages or community-editable docs might contain
incorrect or adversarial "facts" that an LLM imports into the ledger.

**Mitigation roadmap.** Step 3+ that enables `external_official`
evidence MUST require (a) a domain allow-list, (b) per-source
`trust_score` in `metadata`, and (c) a different default `criticality`
(no `source_critical` for external evidence without human
confirmation). The `external_official` status is rejected at
construction in Step 1, so this risk does not exist yet.

## Out of scope for Step 1

The following threats are NOT mitigated by Step 1 because the
corresponding capabilities do not exist yet:

* Prompt injection via LLM tool arguments (no LLM tool dispatch).
* Path traversal via a file-read tool (no file-read tool).
* Resource exhaustion via unbounded regex (no regex search primitive
  yet; `find_literal` is bounded by `max_hits`).
* Cryptographic malleability of SHA-256 (assumed hard).

## Audit checklist for Step 2 reviewers

Before merging any Step 2+ change, reviewers MUST confirm:

1. No new `subprocess`, `os.system`, `eval`, `exec`, `socket`, or
   network-client imports are added to `openmc_agent.plan_investigation/`.
2. Any new tool surface preserves the "inert data" treatment of source
   text.
3. Any new derivation operation is in the allow-list and re-computable
   by pure Python.
4. Any new artifact field does not embed host paths, API keys, or full
   prompts.
5. Any new source kind requires an explicit policy gate before it can
   populate the ledger.
