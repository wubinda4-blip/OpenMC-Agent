# Phase 8B Step 4B-1 — Universe Fragment Transaction Qualification and Deterministic Merge Stabilization

**HEAD when started**: `7062724`
**HEAD when finished**: `<this commit>`
**Scope**: Universe fragmented generation pipeline only.  Does NOT touch the Material-Universe reviewer business logic, the Placement / Axial / Assembled gates, the Facts Gate, or the OpenMC renderer.

---

## 1. Investigation: run_004 root cause

The original failure (run_004, see `docs/phase8b_step4_canary_failure_analysis.md`)
reported a generic `patch_generation.merge_failed` after all 9 universe
fragments were marked `accepted`.  Replaying the persisted run_004 checkpoint
deterministically reproduces the failure with the artifacts already in the
repository:

```
$ python -c '...replay run_004 checkpoint through merge_universe_fragments...'
=== Session universes:12a4fca5b8d636df ===
  Built 9 fragments, 13 materials
  Merge result: patch=no, errors=1
    merge.unknown_material:REPLACE
```

**Root cause**: the `implicit_gas_gap` fragment contains the literal
placeholder `"material_id": "REPLACE"` because the LLM copied the prompt
template's example verbatim.  The pre-4B-1 pipeline accepted the fragment
because it only checked that:

1. the LLM output was JSON-parseable,
2. the first universe's `universe_id` matched the manifest item.

No schema, role, material reference, or placeholder check was performed
before marking the fragment `accepted`.  The placeholder only surfaced at
merge time as a generic `merge.unknown_material:REPLACE` string in a
list of opaque error codes.

**Evidence retained**:
- Session: `data/runs/phase8b_step4_mu_canary_run4/runs/run_001/workflow/incremental/plan_build_state.json`
  (key `metadata.large_patch_generation_sessions["universes:12a4fca5b8d636df"]`)
- Materials: `data/runs/phase8b_step4_mu_canary_run4/runs/run_001/workflow/incremental/valid_patches/materials.json`

The artifacts are sufficient to reproduce the failure class; no guesswork
was needed.

---

## 2. New transaction state machine

```
            ┌────────────────────────────────────────────────────┐
            │  accepted Facts + accepted Materials                │
            └──────────────────────────────┬─────────────────────┘
                                           │
                            extract_universe_requirements
                                           │
                                           ▼
                          UniverseGenerationRequirementSet
                                           │
                          build_manifest_from_requirements
                          (per-item contract_hash computed here)
                                           │
                                           ▼
                          UniverseManifest  (manifest_status=accepted)
                                           │
                                           │ resume verification
                                           │ (data + hash + contract + qualification)
                                           │
              ┌────────────────────────────┴──────────────────────────┐
              │ only invalid fragments are downgraded to pending       │
              └────────────────────────────┬──────────────────────────┘
                                           │
                          per manifest.generation_order:
                                           │
                            ┌──────────────┴───────────────┐
                            │ _call_llm_fragment            │
                            │  → _FragmentLLMOutcome        │
                            │      (ok | provider_exception │
                            │       | parse_exception |     │
                            │       empty)                  │
                            └──────────────┬───────────────┘
                                           │
                            parse_llm_patch_json (rejects >1 universe)
                                           │
                                           ▼
                            qualify_universe_fragment
                            (schema via UniversesPatch, identity, kind,
                             cell roles, material refs, material roles,
                             placeholder detection, duplicate cell IDs,
                             canonical fragment hash)
                                           │
                              ┌────────────┴────────────┐
                              │ ok                      │ fail
                              ▼                         ▼
                       AcceptedFragmentRecord      retry within
                       (data + hash + contract     max_fragment_attempts
                        + qualification)           else: fragment_failed
                                           │
                          merge_universe_fragments_structured
                          (pure Python, structured issues)
                                           │
                       ┌───────────────────┴────────────────────┐
                       │ ok                                     │ fail
                       ▼                                        ▼
                validate_merged_patch                  fragment-scoped?
                (UniversesPatch validator)             ├─ yes → targeted
                       │                               │        replay
                       │                               │        (only the
                       ▼                               │        invalid
                PlanPatchEnvelope                      │        fragments)
                (status=valid)                         └─ no  → fail closed
                                                         (manifest/global)
```

Key invariants:

- A fragment can only enter the accepted set after deterministic
  qualification.
- `accepted ⇒ fragment data exists AND canonical hash matches AND manifest
  contract hash matches AND qualification_status == "passed"`.
- One fragment failure never invalidates another accepted fragment.
- Merge failures are returned as a structured `UniverseMergeResult`; the
  legacy `merge_universe_fragments(…)` wrapper preserves backward
  compatibility.
- Fragment-scoped merge failures trigger targeted replay only for the
  offending fragments; manifest/global failures fail closed.
- The downstream consumer always sees exactly one authoritative
  `UniversesPatch` `PlanPatchEnvelope`; partial fragments are never
  exposed.

---

## 3. Fragment qualification contract

Implemented in `openmc_agent/plan_builder/universe_fragment_qualification.py`.

### 3.1 Output boundary

- Exactly one universe in the fragment payload.  Two-or-more universes
  are rejected with `qualification.fragment_not_single_universe`.  The
  pipeline also rejects this at parse time before qualification, so the
  diagnostic is observable in both places.
- A single-universe payload wrapped as a full `{"patch_type": ...,
  "universes": [u]}` object is unwrapped with a warning
  (`qualification.fragment_wrapped_as_patch`) — the LLM occasionally
  echoes the wrapper despite the prompt.
- `universe_id` must equal the manifest item's `universe_id`.

### 3.2 Schema

- The single universe is wrapped as `{"patch_type": "universes",
  "universes": [<u>]}` and parsed with the authoritative
  `parse_patch_content("universes", …)`.
- The parsed `UniversesPatch` is then run through the existing
  `validate_patch` so all universes-level checks (radial order, fuel-cell
  presence, kind-specific contracts) apply.
- No hand-written parallel schema is maintained; the authoritative
  `UniversesPatch` model is the single source of truth.

### 3.3 Manifest contract

- `kind` matches the manifest item.
- Every `required_cell_role` declared by the manifest item is present
  in the fragment's cells.
- Every non-empty `material_id` refers to a material declared in the
  accepted MaterialsPatch.
- Placeholder tokens (`REPLACE`, `<material_id>`, `TBD`, `XXX`, …) are
  rejected unconditionally, even if the token were somehow in
  `known_material_ids`.  This is the run_004 fix.
- `required_material_roles` are reachable via the materials referenced
  in the cells (using the `material_id → role` mapping).
- Protected-through-path roles are *not* aggregated across manifest
  items; a fragment satisfies its own manifest item's scope or fails.
- Source requirement / profile binding is enforced by the per-item
  `contract_hash`: any drift is detected on resume (qualification cannot
  pass against a stale contract).

### 3.4 Internal integrity

- No duplicate cell IDs inside the universe.
- The canonical fragment hash is recomputed from the universe data;
  an LLM-claimed hash is never trusted (stored as
  `metadata.claimed_fragment_hash` for diagnostics only).

### Failure handling

```
fragment status = failed
→ does NOT enter accepted_fragments
→ does NOT enter the merge
→ structured FragmentQualificationIssue list is fed back to the
  next generation attempt as `prior_failures`
```

---

## 4. Manifest contract (Part 2)

Implemented in `openmc_agent/plan_builder/universe_fragment_generation.py`.

`UniverseManifestItem` now carries the full contract surface:

| Field                                | Source requirement field                | Hashed |
|--------------------------------------|-----------------------------------------|--------|
| `universe_id`                        | `requirement.universe_id`               | ✓      |
| `kind`                               | `requirement.kind`                      | ✓      |
| `required_cell_roles`                | `requirement.required_cell_roles`       | ✓      |
| `required_material_ids`              | `requirement.required_material_ids`     | ✓      |
| `required_material_roles`            | `requirement.required_material_roles`   | ✓      |
| `fuel_variant_id`                    | `requirement.fuel_variant_id`           | ✓      |
| `localized_insert_requirement_id`    | NEW (was dropped before 4B-1)           | ✓      |
| `base_path_component_profile_id`     | NEW (was dropped before 4B-1)           | ✓      |
| `protected_through_path_roles`       | NEW (was dropped before 4B-1)           | ✓      |
| `source_requirement_ids`             | extended to include `requirement_id`    | ✓      |
| `dependency_ids`                     | `requirement.dependency_ids`            | ✓      |
| `expected_cell_count`                | kept for diagnostics                    | –      |
| `assumptions_allowed`                | kept for diagnostics                    | –      |
| `contract_hash`                      | computed by `recompute_contract_hash()` | –      |
| `metadata`                           | freeform, not hashed                    | –      |

`compute_manifest_item_contract_hash(data)` canonicalizes (sorted-keys
JSON) only the hashed fields above.  Order of items in the manifest does
not affect any single item's hash (verified by
`test_contract_hash_stable_across_order_changes`).

No benchmark names, fixture names, or reactor-specific identifiers are
stored on or hashed into the manifest.

---

## 5. Checkpoint / resume integrity

Implemented via the new `AcceptedFragmentRecord` type and the
`_verify_resume_fragments` helper in
`openmc_agent/plan_builder/universe_patch_pipeline.py`.

Each accepted fragment now persists:

```
AcceptedFragmentRecord:
  universe_id
  universe               (canonical data, qualified)
  fragment_hash          (canonical hash of `universe`)
  manifest_contract_hash (echoes the manifest item's contract hash)
  qualification_status   ("passed" required to enter merge)
  qualification_issues   (structured list, possibly with warnings)
  accepted_at_attempt    (which attempt within the transaction)
```

`LargePatchGenerationSession` keeps both:

- `accepted_fragments: dict[str, AcceptedFragmentRecord]` (authoritative)
- `accepted_fragment_hashes: dict[str, str]` (legacy quick lookup)

and the legacy `metadata._accepted_fragments` blob is still written for
backward compatibility with older tooling.  On resume, the typed
`accepted_fragments` is preferred; if missing, a legacy record is
migrated on the fly.

Resume re-verifies every accepted fragment:

1. `status == "accepted"`
2. `AcceptedFragmentRecord` exists
3. `universe_id` matches the current manifest item
4. canonical hash matches the stored `fragment_hash`
5. `manifest_contract_hash` matches the current manifest item
6. `qualification_status == "passed"` and the qualification still passes
   against the current MaterialsPatch (material IDs + roles)

Any single failure downgrades that fragment to `pending` so it is
regenerated.  Other accepted fragments are NOT touched.

Stale sessions (input hash changed) continue to be detected via the
existing `input_hash` mechanism; the per-fragment contract hash gives
the additional signal needed to diagnose *which* fragment went stale.

---

## 6. Structured deterministic merge (Part 5)

Implemented in
`openmc_agent.plan_builder.universe_fragment_generation.py`.

`merge_universe_fragments_structured(…)` returns a `UniverseMergeResult`:

```python
class UniverseMergeResult:
    ok: bool
    merged_patch: dict | None
    issues: list[UniverseMergeIssue]
    invalid_fragment_ids: list[str]
    merged_patch_hash: str | None
    manifest_id: str
    manifest_input_hash: str
```

`UniverseMergeIssue` attributes every failure to a fragment, manifest,
or global scope:

```python
class UniverseMergeIssue:
    code: str
    severity: "error" | "warning"
    universe_id: str | None
    fragment_hash: str | None
    json_path: str | None
    message: str
    retry_scope: "fragment" | "manifest" | "global"
    retryable: bool
    expected: Any | None
    actual: Any | None
    metadata: dict
```

Merge verifies (in order):

1. Manifest self-consistency: no duplicates in `generation_order`,
   `expected_universe_count == len(items)`, `set(generation_order)`
   equals `set(item.universe_id)`.
2. No duplicate fragments.
3. Every manifest item has exactly one fragment.
4. Every fragment's `universe_id` matches its slot.
5. Every fragment's `kind` matches its manifest item's `kind`.
6. Every cell's `material_id` (when non-empty) is in
   `known_material_ids`.
7. When `qualification_records` is supplied, each fragment's saved
   `qualification_status == "passed"` AND its
   `manifest_contract_hash` matches the current item's hash.
8. No extra undeclared fragments.

The merged patch is rebuilt strictly in manifest canonical order;
same input always produces the same `merged_patch_hash`.

### Issue classification

| Failure                                  | retry_scope | retryable | Action          |
|------------------------------------------|-------------|-----------|-----------------|
| `merge.duplicate_fragment`               | fragment    | yes       | replay fragment |
| `merge.missing_fragment`                 | fragment    | yes       | replay fragment |
| `merge.universe_id_mismatch`             | fragment    | yes       | replay fragment |
| `merge.kind_mismatch`                    | fragment    | yes       | replay fragment |
| `merge.unknown_material`                 | fragment    | yes       | replay fragment |
| `merge.qualification_not_passed`         | fragment    | yes       | replay fragment |
| `merge.manifest_contract_drift`          | fragment    | yes       | replay fragment |
| `merge.extra_fragment`                   | fragment    | no        | fail closed     |
| `merge.manifest_duplicate_in_order`      | manifest    | no        | fail closed     |
| `merge.manifest_count_mismatch`          | manifest    | no        | fail closed     |
| `merge.manifest_order_items_mismatch`    | manifest    | no        | fail closed     |
| Merged `UniversesPatch` schema failure   | global      | no        | fail closed     |

The top-level error code remains `patch_generation.merge_failed` so the
existing retry owner policy keeps routing the request to the
`universes` owner.  The structured metadata is carried in
`issue.metadata`:

```
merge_issue_codes
invalid_fragment_ids
affected_json_paths
fragment_hashes
manifest_id
manifest_input_hash
retry_scopes
issues          (full structured issue list)
```

---

## 7. Targeted fragment replay (Part 6)

Implemented in `_attempt_merge_with_replay` inside
`universe_patch_pipeline.py`.

Transaction-level replay loop:

```
1.  Generate all pending fragments (skipping valid accepted ones).
2.  Run merge_universe_fragments_structured with qualification_records.
3.  If ok → validate_merged_patch → emit PlanPatchEnvelope.
4.  If merge fails with ONLY fragment-scoped issues:
       downgrade ONLY the invalid fragments
       regenerate them within max_fragment_attempts
       re-run merge
       (bounded by max_merge_replays rounds)
5.  If merge fails with ANY manifest or global scope issue → fail closed.
6.  If any replay round fails → fail closed with full diagnostics.
```

Budget guards:

- `max_fragment_attempts` per fragment per round.
- `max_total_llm_calls` across the whole transaction.
- `max_merge_replays` bounds the merge → replay → merge loop.

Already-accepted fragments are NEVER re-called during a fragment-scoped
replay.  This is verified by `test_scenario_d_merge_finds_fragment_scoped_issue_then_replays`.

This transaction-level replay is distinct from the closed-loop
Material-Universe Gate retry path; the latter is unchanged.

---

## 8. Artifacts and observability (Part 7)

Persisted on `LargePatchGenerationSession`:

- `manifest` (with per-item `contract_hash`)
- `manifest_status`
- `fragment_statuses` (per-universe, with `fragment_hash`,
  `manifest_contract_hash`, `qualification_status`,
  `qualification_issues`, `accepted_at_attempt`)
- `accepted_fragments` (full `AcceptedFragmentRecord` with universe data
  + integrity metadata)
- `accepted_fragment_hashes` (legacy quick-lookup map)
- `failed_fragment_issues` (structured qualification issues)
- `provider_telemetry` (per-call: `outcome_kind`, `exception_class`,
  `note`, `finish_reason`, `output_mode_used`, `completion_tokens`,
  `reasoning_tokens`)
- `merge_history` (per-round: `ok`, `merged_patch_hash`,
  `issue_codes`, `invalid_fragment_ids`, full structured `issues`)
- `llm_call_count`, `merged_patch_hash`, `completed`

The pipeline no longer wraps `_call_llm_fragment` in a broad
`except Exception: pass`; each call returns a classified
`_FragmentLLMOutcome` (`ok | provider_exception | parse_exception |
empty`).  Provider exceptions, schema exceptions, and content
validation failures are recorded separately in `provider_telemetry`,
and a single logical fragment call no longer silently triggers more
than one provider request.

---

## 9. Test matrix (Part 8)

New tests:

| File                                                  | Tests | Coverage area                                                |
|-------------------------------------------------------|-------|--------------------------------------------------------------|
| `test_universe_fragment_qualification.py`             | 22    | Qualification contract (3.1–3.4, resume verification)        |
| `test_universe_manifest_contract.py`                 | 15    | Manifest field preservation + contract hash                  |
| `test_universe_merge_structured.py`                  | 13    | Structured merge result, scope classification, drift        |
| `test_universe_patch_pipeline_integration.py`        | 7     | End-to-end pipeline scenarios A–F + legacy compatibility     |

Total new: **57 tests**.

Scenario coverage in `test_universe_patch_pipeline_integration.py`:

- **A** `test_scenario_a_all_success` — all fragments accepted first try.
- **B** `test_scenario_b_one_fragment_first_attempt_bad_then_good` — one
  fragment fails first attempt (unknown material), succeeds on retry;
  other fragments not re-called.
- **C** `test_scenario_c_checkpoint_corruption_only_regen_corrupt_fragment` —
  resume detects a corrupted accepted record and regenerates only that
  fragment.
- **D** `test_scenario_d_merge_finds_fragment_scoped_issue_then_replays` —
  merge detects a fragment-scoped drift and triggers a targeted replay
  only for the offending fragment.
- **E** `test_scenario_e_manifest_failure_fails_closed` — manifest-level
  failure fails closed and does not produce an envelope.
- **F** `test_scenario_f_run004_replace_placeholder_diagnosed_precisely` —
  the run_004 class failure is rejected at qualification with a
  structured `qualification.placeholder_material_id` issue and a JSON
  path, instead of a generic merge error.
- Plus `test_legacy_fragment_payload_with_two_universes_is_rejected`
  covering the boundary that a fragment payload with two universes is
  rejected (not silently dropped).

### Verification results

```
$ pytest -q tests/test_universe_fragment_qualification.py tests/test_universe_manifest_contract.py tests/test_universe_merge_structured.py tests/test_universe_patch_pipeline_integration.py
57 passed

$ pytest -q tests/test_universe_*.py tests/test_vera4_*.py tests/test_real_campaign_fragmented_universes.py
250 passed

$ python -m compileall -q openmc_agent scripts
clean

$ pytest -q tests/ -m "not openmc and not requires_llm"
3464 passed, 2 skipped

$ python scripts/run_workflow_benchmark.py --cases tests/fixtures/evaluation_cases.json --model fake --mode plan-only --out data/evals/workflow/fake_current
Cases: 21 pass_rate=100.0%
```

No existing tests were deleted, skipped, or weakened.

---

## 10. Items NOT addressed by Step 4B-1

These are explicitly out of scope (per the task's "non-goals") and remain
open:

- **Materials JSON truncation** — Materials fragmentation is the next
  independent step.  Step 4B-1 does not change Materials generation,
  JSON truncation detection, or Materials-side partial-output handling.
- **Three consecutive real MU Canary passes** — Step 4B-1 stabilizes the
  Universe transaction but does not claim MU Canary completion.  No
  `VERA4_REAL_MATERIAL_UNIVERSE_CANARY_PASSED` declaration is made.
- **Closed-loop MU Gate retry path** — unchanged.  This step implements
  transaction-internal fragment replay; it does not refactor the
  closed-loop retry owner / acceptance check logic.
- **Placement / Axial / Assembled gates** — unchanged.
- **Facts Gate** — unchanged.
- **OpenMC renderer** — unchanged.

---

## 11. Acceptance checklist

| # | Requirement                                                                       | Status |
|---|-----------------------------------------------------------------------------------|--------|
| 1 | Fragment acceptance requires deterministic qualification                          | ✓      |
| 2 | Manifest item preserves requirement / profile / protected-path fields             | ✓      |
| 3 | Accepted checkpoint has data + hash + contract + qualification integrity          | ✓      |
| 4 | Resume detects and only repairs corrupted fragments                               | ✓      |
| 5 | Merge remains pure-Python deterministic                                           | ✓      |
| 6 | Merge failure returns structured, attributable diagnostics                        | ✓      |
| 7 | Fragment-scoped merge failure replays only the relevant fragments                 | ✓      |
| 8 | Manifest / global failure fails closed                                            | ✓      |
| 9 | Downstream sees exactly one authoritative UniversesPatch envelope                 | ✓      |
| 10| MU Reviewer business logic unchanged                                              | ✓      |
| 11| No VERA3 / VERA4 benchmark hardcoding                                              | ✓      |
| 12| Targeted tests, compileall, and full test suite pass                              | ✓      |
| 13| run_004 class failure has a deterministic regression test                         | ✓      |
| 14| Documentation distinguishes completed (Universe transaction) vs not completed     | ✓      |

Step 4B-1 is complete; Placement Gate development is not in scope and
must not be started on the basis of this step alone.
