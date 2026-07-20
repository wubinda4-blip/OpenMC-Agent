# Phase 8B Step 3 — Facts Gate Analysis (Step 0)

> Produced by code analysis only. No code changes in this step.

## 1. Pipeline overview

```
Investigation evidence (EvidenceClaims + confirmed_facts)
    │
    ▼ compile_facts_requirement_skeleton()          [facts_requirement_skeleton.py]
FactsRequirementSkeleton (typed slots)
    │
    ▼ compile_facts_evidence_contract()             [facts_evidence_contract.py]
FactsEvidenceContract (flat contract)
    │
    ▼ (LLM generates FactsPatch, constrained by _facts_skeleton_block)
Valid FactsPatch envelope
    │
    ▼ _run_facts_gate()                             [executor.py:1964–2377]
    │
    ├─ facts_gate_input_hash()                      → short-circuit if unchanged
    ├─ run_facts_consistency_preflight()            → feature↔Facts checks (facts.* codes)
    ├─ run_facts_skeleton_preflight()               → skeleton↔Facts checks (facts_skeleton.* + facts_contract.*)
    ├─ build_facts_evidence_packs()                 → chunk requirement into ≤12KB packs
    │       └─ facts_review_preflight()             → per-pack schema-level checks (facts_review.* codes)
    ├─ run_facts_review()  ← LLM                    → per-pack FactsReviewModelOutput
    │       └─ _normalize()                         → reject malformed findings, bind evidence hashes
    ├─ consistency + contract_preflight + LLM findings aggregation
    ├─ compute_allowed_actions()                    → APPROVE / REVISE / ASK_HUMAN / RETRIEVE_EVIDENCE / FAIL_CLOSED
    │
    ├─ APPROVE      → accept stage, compile GeometryComponentInventory
    ├─ ASK_HUMAN    → AWAITING_HUMAN + typed questions
    └─ REVISE_CURRENT_PATCH
            ├─ build_facts_revision_prompt()  ← LLM (2 attempts per issue fingerprint)
            ├─ normalize_facts_revision()         → RFC6902 extraction
            ├─ evaluate_facts_revision()          → scope/clone/schema/confirmed-fact checks
            ├─ run_facts_review() re-review  ← LLM → defensive depth check
            ├─ run_facts_consistency_preflight()  → candidate deterministic checks
            └─ if pass → atomic commit + ACCEPTED
```

## 2. Key files

| File | Lines | Role |
|------|-------|------|
| `closed_loop/facts_reviewer.py` | 133 | Per-pack LLM evaluator; normalises findings |
| `closed_loop/facts_review_prompts.py` | 37 | Builds the review prompt (single-pass, 5 tasks) |
| `closed_loop/facts_consistency.py` | 43 | Deterministic feature↔Facts preflight |
| `closed_loop/facts_revision.py` | 403 | LLM repair + deterministic repair injection |
| `closed_loop/facts_revision_prompts.py` | 75 | Repair prompt builder |
| `closed_loop/facts_evidence.py` | 188 | Evidence packing + gate input hash |
| `closed_loop/review_io.py` | 327 | Shared structured-output I/O for all gates |
| `plan_builder/facts_requirement_skeleton.py` | 675 | Typed slot compilation from evidence |
| `plan_builder/facts_evidence_contract.py` | (large) | Skeleton↔Facts contract preflight |

## 3. Why the review prompt reaches ~38 KB

`build_facts_review_prompt(pack)` concatenates three parts:

1. **Static instructions** (~600 bytes) — 5-task role declaration (missing/contradiction/inference/scope/downstream).
2. **`FactsReviewModelOutput.model_json_schema()`** — nested schema with `FactsReviewFindingDraft`, `FactsInterpretationOption`, `FactsReviewCoverageSummary`, Literal enums.
3. **`pack.model_dump(mode="json")`** — the dominant factor:
   - `relevant_patches["facts"]` = **full FactsPatch** (all fields).
   - `source_excerpts[0].text` = up to 12 000 chars of requirement text.
   - `confirmed_facts` = human-confirmed records.
   - `patch_summaries["planning_mode_decision"]`.
   - `metadata`: `facts_summary`, `planning_feature_contract`, `resolved_planning_scope`, `facts_consistency_issues`, `expected_patch_family`.

A typical multi-assembly patch + 12 KB chunk + schema → 30–40 KB.

## 4. Current chunking vs. stage-splitting

| Mechanism | Exists? | Details |
|-----------|---------|---------|
| Source-text chunking | **Yes** | `_paragraphs()` splits requirement into ≤12 KB line-aligned chunks (max 8 packs). |
| Per-topic review split | **No** | Each chunk gets a single-pass review over ALL 5 tasks with the FULL FactsPatch. |
| Cross-pack synthesis | **Wired but dead** | `enable_facts_review_synthesis` policy flag + `build_facts_synthesis_prompt` exist but `run_facts_review` never calls the synthesis step — it only deduplicates by `finding_id` fingerprint. |

## 5. Current deterministic checks

| Check | File | When | What it compares |
|-------|------|------|------------------|
| `facts_review_preflight` | `facts_evidence.py:168` | Per evidence-pack build | Schema-level: patch_type, count mismatch, axial ordering, duplicate IDs |
| `run_facts_consistency_preflight` | `facts_consistency.py:16` | Gate entry + revision candidate | `PlanningFeatureContract` ↔ `FactsPatch` fields |
| `run_facts_skeleton_preflight` | `facts_evidence_contract.py:135` | Inside `run_clone_validation` | `FactsRequirementSkeleton` ↔ candidate `FactsPatch` |
| `evaluate_facts_revision` | `facts_revision.py:141` | Revision candidate | Path scope, clone apply, `validate_patch`, confirmed-fact preservation |
| `targeted_facts_repair` | `facts_revision.py:190` | Revision | Deterministic value injection from skeleton |

**Gap:** There is NO check that compares the **evidence ledger claims** (search_hit, scope_indicator_present, synthesised model_scope, etc.) against the **FactsPatch field values**. The existing checks compare feature_contract ↔ FactsPatch and skeleton ↔ FactsPatch, but not evidence ↔ FactsPatch directly. This is the gap Step 2 addresses.

## 6. Gate decision logic

The decision point is `_run_facts_gate()` in `executor.py:1964` (413 lines, fully inline).

**Finding aggregation** (line 2131):
```python
all_findings = consistency_findings + contract_preflight_findings + list(review.findings)
```

**Action routing** (line 2140) via `compute_allowed_actions()` (policy.py:79):
1. Budget exhausted → `[FAIL_CLOSED]`
2. Any error finding `requires_human` → `[ASK_HUMAN, FAIL_CLOSED]`
3. No error findings + no blocking deterministic issues → `[APPROVE]`
4. `enable_research` + finding matches RETRIEVE_EVIDENCE codes → `[RETRIEVE_EVIDENCE, REVISE_CURRENT_PATCH, ...]`
5. Any error finding `repairable_by_llm` → `[REVISE_CURRENT_PATCH, RETRY_DEPENDENCY]`
6. Otherwise → `[FAIL_CLOSED]`

Always takes `actions[0]`.

## 7. All Facts-related finding/issue codes

### `facts.*` — deterministic consistency preflight
- `facts.model_scope_conflicts_with_planning_features`
- `facts.multi_assembly_contract_incomplete`
- `facts.spacer_grid_contract_missing`
- `facts.localized_insert_contract_missing`
- `facts.localized_insert_profile_contract_missing`
- `facts.control_state_contract_missing`
- `facts.fuel_variant_contract_missing`
- `facts.assembly_count_inconsistent`
- `facts.core_lattice_size_inconsistent`
- `facts.review_source_too_large` (executor.py:2048)
- `facts.count_scope_ambiguous` (scoped_counts.py)
- `facts.source_value_missing` (research_router.py)
- `facts.human_confirmation` (executor.py:1887)

### `facts_review.*` — reviewer preflight + normalisation
- `facts_review.patch_type_invalid`
- `facts_review.assembly_count_mismatch`
- `facts_review.active_fuel_region_cm_invalid`
- `facts_review.axial_domain_cm_invalid`
- `facts_review.duplicate_fuel_variant_id`
- `facts_review.duplicate_localized_insert_requirement`
- `facts_review.invalid_finding_contract`
- `facts_review.unknown_evidence_hash`
- `facts_review.path_out_of_scope`
- `facts_review.budget_exhausted`
- `facts_review.schema_invalid`
- `facts_review.coverage_incomplete`

### `facts_revision.*` — revision evaluation
- `facts_revision.path_out_of_scope`
- `facts_revision.apply_failed`
- `facts_revision.root_replacement_forbidden`
- `facts_revision.duplicate_candidate`
- `facts_revision.schema_invalid`
- `facts_revision.validator_failed`
- `facts_revision.confirmed_fact_changed`
- `facts_revision.no_progress`
- `facts_revision.consistency_errors`
- `facts_revision.reviewer_coverage_incomplete`
- `facts_revision.skeleton_preflight: <code>`

### `facts_skeleton.*` / `facts_contract.*` — skeleton/contract preflight
- `facts_skeleton.missing`, `facts_skeleton.immutable_field_modified`, `facts_skeleton.count_mismatch`, `facts_skeleton.fuel_variant_modified`
- `facts_contract.locked_field_modified`, `facts_contract.assembly_count_inconsistent`, `facts_contract.feature_flag_contradiction`, `facts_contract.fuel_variant_missing`, `facts_contract.localized_insert_missing`, `facts_contract.source_critical_unresolved`, `facts_contract.conflict_unresolved`

## 8. LLM call sites

| Location | Client method | Role |
|----------|---------------|------|
| `facts_reviewer.py:85` | `run_structured_review_call` → `client.generate_patch_json(...)` or `client(prompt)` | Review — once per evidence pack |
| `executor.py:2263` | `plan_repair_client.generate_patch_json(...)` or `client(prompt)` | Repair — up to 2 attempts per issue fingerprint |
| `executor.py:2302` | Same as review | Re-review after repair (defensive depth check) |

## 9. Complexity concerns for stabilisation

1. **Single-pass review prompt with no task split** — the biggest liability. All 5 review tasks in one prompt with the full FactsPatch.
2. **Cross-pack error isolation** — one malformed pack aborts the entire review.
3. **Per-pack redundant preflight** — `facts_review_preflight` recomputes the same pack-agnostic issues per pack.
4. **Two parallel finding-code families** (`facts_skeleton.*` legacy + `facts_contract.*` Phase 8C Step 2) — confusing for stabilisation.
5. **`gate_transaction.py` unused** — 590-line generic kernel is wired but never imported.
6. **`build_facts_synthesis_prompt` + `enable_facts_review_synthesis` dead** — wired but never called.
7. **Inline `_run_facts_gate` 413 lines** — budget, 4 preflights, pack, review, aggregate, route, revise, re-review, commit, inventory all inline.

## 10. Stabilisation plan (Steps 1–4)

| Step | What | Goal |
|------|------|------|
| 2 | `facts_evidence_consistency.py` | Catch evidence↔FactsPatch conflicts deterministically (no LLM needed). Codes: `facts.scope_evidence_conflict`, `facts.fuel_variant_missing`, `facts.localized_insert_missing`, `facts.grid_feature_missing`. |
| 1 | Typed review stages | Split the single-pass review into per-topic stages (SCOPE, FUEL_VARIANT, ASSEMBLY_STRUCTURE, LOCALIZED_INSERT, GRID_AXIAL, COMPLETENESS). Each stage sees only its target fields + relevant evidence. |
| 3 | Repair coverage contract | Repair prompt must emit all required coverage fields; incomplete repair → `planning.facts_repair_incomplete` block. |
| 4 | Reviewer output stability | Reject empty responses (`facts.reviewer_empty_response`), JSON recovery for fenced/embedded, no free-text approve. |

Principle: **reduce what the LLM needs to judge; convert factual consistency into Python-verifiable problems.**
