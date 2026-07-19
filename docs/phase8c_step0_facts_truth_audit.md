# Phase 8C Step 0 — Facts Truth Audit

**Date:** 2026-07-19
**Starting HEAD:** `3bcc95c` (carries Phase 8B Step 2 partial work)
**Baseline tests:** 3271 passed, 2 skipped, 392 deselected
**Compileall:** OK
**Fake benchmark:** 21/21 (100%)

This audit enumerates every node in the Facts production call chain, classifies
its real production-wired status, and answers the 20 mandatory audit questions
from the Phase 8C Step 0 charter.  It is the authoritative reference for the
emergency stopgap fixes that follow in the same commit.

---

## 1. Facts production call chain — node classification

Classification legend:
- **PRODUCTION_WIRED** — actually called on a real controlled/advisory run.
- **OFFLINE_ONLY** — defined and unit-tested, but no production caller.
- **LEGACY_PATH** — only executes when closed-loop is off.
- **DUPLICATED_PATH** — there is more than one entry point and they overlap.
- **BYPASS_PATH** — silently coerces a value, defeating the stated contract.
- **UNTESTED_REAL** — wired but no real canary exercises it.
- **REAL_CANARY_PROVEN** — wired and exercised end-to-end by a real canary.

| # | Node | Location | Classification |
|---|---|---|---|
| 1 | Requirement → planning mode decision | `graph.py:1221`, `plan_builder/mode.py:329` | PRODUCTION_WIRED |
| 2 | `PatchGenerationContext` build | `executor.py:431` `build_generation_context_from_state` | PRODUCTION_WIRED |
| 2a | `PatchGenerationContext.model_scope` default | `patch_generator.py:95` = `None` | clean |
| 2b | `FactsPatch.model_scope` schema default | `patches.py:203` = `"single_assembly"` | **BYPASS_PATH** |
| 3 | Facts investigation (controlled) | `executor.py:3538` → `executor_injection.py:452` | PRODUCTION_WIRED (controlled) / skipped when investigation disabled |
| 4 | Evidence injection into prompt | `executor.py:3604`, `patch_prompts.py:857` | PRODUCTION_WIRED |
| 5 | `build_patch_prompt("facts")` | `patch_prompts.py:656` | PRODUCTION_WIRED |
| 5a | Prompt rule text mentioning `single_assembly` | `patch_prompts.py:75,106-108` | contamination |
| 6 | LLM call | `patch_generator.py:1158` | PRODUCTION_WIRED |
| 7 | Parse / contract / schema validate | `patch_generator.py:1222-1377` | PRODUCTION_WIRED |
| 8 | Facts consistency preflight | `closed_loop/facts_consistency.py:16`, `executor.py:1993` | PRODUCTION_WIRED |
| 9 | Independent reviewer | `closed_loop/facts_reviewer.py:78`, `executor.py:2045,2239` | PRODUCTION_WIRED (controlled) |
| 10 | Action decision | `closed_loop/policy.py:79`, `executor.py:2078` | PRODUCTION_WIRED |
| 11a | Inline Facts Gate revision (RFC6902) | `executor.py:2155-2282`, `facts_revision.py:141` | PRODUCTION_WIRED |
| 11b | Retry-controller producer (`make_facts_producer`) | `retry_candidate_producers.py:131`, `graph.py:4152` | **DUPLICATED_PATH** — `facts_revision_fn` is never passed, always regenerates the full patch |
| 11c | `targeted_facts_repair` (skeleton-driven) | `facts_revision.py:190` | **OFFLINE_ONLY** — zero production callers |
| 11d | `run_clone_validation` | `facts_revision.py:318` | **OFFLINE_ONLY** — zero production callers |
| 12 | Owner commit | `executor.py:2246-2248` (inline), `retry_controller.py:260` (retry loop) | PRODUCTION_WIRED |
| 13 | Inventory compile on accept | `executor.py:2116,2258` `_maybe_compile_geometry_inventory` | PRODUCTION_WIRED when investigation enabled |
| 14 | Downstream resume | `downstream_resume.py:36,117` | **DUPLICATED_PATH** — never invoked; executor uses recursive `run_incremental_planning` at `executor.py:3203` |
| 15 | Facts Gate `accepted_input_hash` save | (missing for facts; present for placement/MU/AX/AS) | **OFFLINE_ONLY** |

Skeleton-specific nodes added in Phase 8B Step 2:

| Node | Location | Status |
|---|---|---|
| `compile_facts_requirement_skeleton` | `facts_requirement_skeleton.py:201` | PRODUCTION_WIRED but does not mine `evidence_ledger.claims` — only `confirmed_facts` populate slot values |
| `merge_facts_content_into_skeleton` | `facts_evidence_contract.py:189` | PRODUCTION_WIRED but no-op when `skeleton.status == unresolved` |
| `run_facts_skeleton_preflight` | `facts_evidence_contract.py:259` | PRODUCTION_WIRED but always returns OK when skeleton is unresolved |
| `FactsInvestigationCoverageMatrix` | `executor_injection.py:330` | PRODUCTION_WIRED, used to gate Investigation `completed` |
| `check_facts_semantic_coverage` | `executor_injection.py` | PRODUCTION_WIRED |

---

## 2. Answers to the 20 mandatory audit questions

1. **Where does `model_scope` default come from?**
   Two places:
   - `PatchGenerationContext.model_scope` (`patch_generator.py:95`) — clean (`None`).
   - `FactsPatch.model_scope` (`patches.py:203`) — **`"single_assembly"`**. This is the
     authoritative bypass: `parse_patch_content("facts", content)` invokes the
     Pydantic schema, which silently fills `"single_assembly"` whenever the LLM
     omits the field. Downstream code (`scoped_counts.py`, `assembler.py:1479`,
     `material_universe_binding.py:307`) treats the resulting value as the
     decided scope.

2. **Which renderer writes the default into the Facts prompt?**
   `_PATCH_RULES["facts"]` in `patch_prompts.py`:
   - Line 75: instruction text `"Determine model_scope: single_assembly for one assembly, multi_assembly_core for N×N cores."`
   - Line 106-108: minimal example with `"model_scope": "single_assembly"`
   - Lines 110-117: a multi-assembly example (counter-balanced).
   The Pydantic schema default at `patches.py:203` is the silent fallback.

3. **Do other fields have similar default contamination?**
   | field | default | location | risk |
   |---|---|---|---|
   | `selected_variant` | `None` | `patches.py:181` | clean |
   | `assembly_count` | `None` | `patches.py:204` | clean |
   | `core_lattice_size` | `None` | `patches.py:205` | clean |
   | `has_axial_geometry` | `False` | `patches.py:188` | soft default — can mask omission |
   | `has_spacer_grids` | `False` | `patches.py:189` | soft default — can mask omission |
   | `has_special_pin_map` | `False` | `patches.py:190` | soft default — can mask omission |
   | `model_scope` | `"single_assembly"` | `patches.py:203` | **CRITICAL — selects the entire patch family** |

   The `model_scope` default is uniquely dangerous because it actively selects
   the entire patch family (`pin_map` vs `assembly_catalog`+`core_layout`)
   through `assembler.py:1479-1481` (`assembly.model_scope_patch_family_conflict`).
   The boolean defaults are softer — they disable a feature rather than choosing
   a different patch family.

4. **What is the Facts Investigation `completed` condition?**
   `executor_injection.py:690`: `completed = result.completed and not result.blocked`,
   then in controlled mode `_passes_controlled_facts_coverage(coverage)` is
   required, which needs at least: 1 `inspect_requirement_structure`, 1
   `inspect_patch_schema`, 1 `search_source_index`, ≥1 source-backed claim, ≥1
   source span, and a non-zero `scope_indicator_claim_count`.
   **Defect:** Coverage is currently *tool-call-shaped*, not *semantic-kind-shaped*.
   A single irrelevant `search_source_index` hit can satisfy the coverage
   requirement even if mandatory semantic targets (e.g. fuel variants,
   localized inserts) have no supporting claim.

5. **Which critical VERA4 facts have an EvidenceClaim?**
   From `data/runs/phase8b_step2_vera4_facts/runs/run_001/workflow/incremental/plan_closed_loop/facts_evidence_pack_*.json`:
   - `model_scope` → yes, claim about "9 assemblies / 3×3 lattice".
   - `assembly_count=9` → yes, same claim.
   - `core_lattice_size=[3,3]` → yes.
   - `has_spacer_grids=true` → yes.
   - `fuel_variant_requirements` → yes, claims about 2.11 wt% and 2.619 wt%.
   - `localized_insert_requirements` → partial — Pyrex/thimble-plug presence is
     mentioned but coordinate counts are not source-backed.

6. **Which facts live only in the requirement text?**
   - Reactor type ("PWR-like").
   - Operating state ("cold, all rods out").
   These do not drive Facts slot values directly; they drive the planning
   feature detector.

7. **Which facts live only in the planning feature detector?**
   - `multi_assembly_feature` (inferred from `core_lattice_size`/`assembly_count`
     in requirement).
   - `multiple_fuel_variants` (inferred from enrichment text).
   - `spacer_grid_feature`.
   - `localized_insert_feature`.
   These flow into `planning_feature_contract` and are visible to the Facts
   consistency preflight, but **not** into `FactsRequirementSkeleton` slot
   values, because the skeleton compiler only reads `confirmed_facts`.

8. **Which facts entered the final Facts Prompt?**
   Investigation evidence block (free text from claims), feature contract
   summary (in `_planning_constraints_block`), and skeleton block — but the
   skeleton is empty because no `confirmed_facts` exist for VERA4. So the
   prompt carries evidence as natural language, not as authoritative slots.

9. **Which facts entered the final Facts Patch?**
   All of the values listed in §5. But the *initial* LLM output had
   `model_scope="single_assembly"` (wrong). The *revision* LLM corrected it to
   `multi_assembly_core` via RFC6902 operations. The revision was accepted.

10. **Which consistency findings triggered?**
    On the initial `single_assembly` patch: 6 findings from
    `run_facts_consistency_preflight`, codes:
    - `facts.model_scope_conflicts_with_planning_features`
    - `facts.multi_assembly_contract_incomplete`
    - `facts.fuel_variant_contract_missing`
    - `facts.localized_insert_contract_missing`
    - `facts.assembly_count_inconsistent`
    - `facts.core_lattice_size_inconsistent` (when scope overridden)

11. **Is finding metadata preserved end-to-end?**
    No. `run_facts_consistency_preflight` returns rich metadata
    (`json_path`, `expected_value`, `actual_value`, `slot_ids`,
    `source_claim_ids`), but the lift into `PlanReviewFinding` at
    `executor.py:2058-2067` only carries `code`, `severity`, `confidence=1.0`,
    and `metadata={"deterministic": True}`. The downstream retry request
    builder only sees `code + message`. This is the **finding-metadata-loss**
    defect Phase 8C Step 1 must fix.

12. **Are Facts retry requests actually executed?**
    The inline Facts revision (node 11a) is real and executed in production
    (`executor.py:2155-2282`). The retry-controller path (node 11b) is
    *registered* but the producer fallbacks to full regeneration, never calling
    the targeted `facts_revision_fn`. So targeted repair is **registered but
    not executed** through the unified retry loop.

13. **Is Facts revision a targeted JSON Patch or a full regeneration?**
    Inline path: targeted RFC6902 JSON Patch (real).
    Retry-controller path: full patch regeneration (defeats the targeted design).

14. **After commit, does the Reviewer actually re-run?**
    Yes. `executor.py:2239` invokes `run_facts_review` again on the candidate;
    the commit at `executor.py:2248` is conditional on the re-review passing
    (`rereview.ok and rereview.coverage_complete and no severity=error`).

15. **After accept, is Inventory re-compiled?**
    Yes, *iff* investigation is enabled (`executor.py:2115,2257`).
    When investigation is off, the inventory is never compiled and downstream
    generators fall back to legacy text-only context.

16. **Are there multiple Facts repair entry points?**
    Yes — 4 (see node 11a–11d in the table). Two are dead code, one is a
    duplicated fallback that defeats the targeted design.

17. **Is there a bypass that invalidates Facts directly?**
    `state.invalidate_patch_types(["facts"], reason="facts human confirmation")`
    at `executor.py:1887` and `executor.py:2246`. These do not skip the gate —
    the next run still executes `_run_facts_gate` — but they bypass the
    `accepted_input_hash` replay gate that exists for placement/MU/AX.

18. **How is `accepted_input_hash` computed/saved?**
    Each non-Facts gate computes a deterministic input hash (e.g.
    `material_universe_gate_input_hash`) and saves it on
    `stage.metadata["accepted_input_hash"]` at the moment of acceptance.
    **Facts Gate does not save `accepted_input_hash`** — there is no replay
    protection; resuming a run with the same Facts input re-executes the full
    reviewer + revision cycle.

19. **Can the hash change authoritatively replay?**
    For placement/MU/AX/AS: yes — the next run compares the new input hash to
    the stored one and skips the gate if unchanged.
    For Facts: **no** — there is no stored hash to compare against.

20. **Is there a no-progress protection for the same error?**
    The retry controller has a `progress_fingerprint` mechanism
    (`retry_controller.py`), but it operates at the granularity of
    `state.validation_issues` only. Findings that live only in
    `state.plan_review_findings` or in `facts_consistency` artifacts are not
    part of the fingerprint, so the loop can repeatedly re-discover the same
    consistency finding without triggering `SAFE_STOP_NO_PROGRESS`.

---

## 3. Facts capability truth matrix

| Capability | Model exists | Unit tests | Main path wired | Mutation test | Real canary |
|---|---:|---:|---:|---:|---:|
| Neutral `model_scope` default | ❌ (schema still `single_assembly`) | ❌ | ❌ | ❌ | ❌ |
| Prompt omits unknown context | partial | ❌ | partial | ❌ | ❌ |
| Facts investigation runs before LLM | ✅ | ✅ | ✅ | ❌ | ✅ |
| Semantic coverage gates Investigation `completed` | partial (tool-call-shaped) | ✅ | ✅ | ❌ | ❌ |
| Consistency preflight runs before reviewer | ✅ | ✅ | ✅ | ❌ | ✅ |
| Independent reviewer re-runs after commit | ✅ | ✅ | ✅ | ❌ | ✅ |
| Targeted Facts repair (RFC6902) | ✅ | ✅ | ✅ (inline) | ❌ | ✅ (variance-driven) |
| Targeted Facts repair via retry loop | ✅ | ✅ | ❌ (`facts_revision_fn` not wired) | ❌ | ❌ |
| Skeleton-driven deterministic restore | ✅ | ✅ | ❌ (no callers) | ❌ | ❌ |
| Clone validation | ✅ | ✅ | ❌ (no callers) | ❌ | ❌ |
| `accepted_input_hash` replay protection | ✅ (other gates) | ✅ (other gates) | ❌ (Facts gate) | ❌ | ❌ |
| Atomic owner commit | ✅ | ✅ | ✅ | ❌ | ✅ |
| Downstream Inventory recompile on accept | ✅ | ✅ | ✅ | ❌ | ✅ |
| Downstream Material/Universe requirement sets | ✅ | ✅ | ✅ | ❌ | ✅ |
| No-progress loop protection | partial | ✅ | partial (validation_issues only) | ❌ | ❌ |
| `model_scope=multi_assembly_core` deterministic on VERA4 | ❌ | ❌ | ❌ | ❌ | variance-only |

---

## 4. Emergency stopgap fixes (this commit)

The following minimum fixes are applied together with this audit:

1. `FactsPatch.model_scope` default changes from `"single_assembly"` to
   `"unknown"`. The Pydantic schema no longer silently forces a patch family.
2. `FactsPatch.has_axial_geometry`, `has_spacer_grids`, `has_special_pin_map`
   become `bool | None` (default `None`) — the prompt and consistency preflight
   already treat missing booleans correctly, but a `None` default makes the
   "LLM omitted this" state observable.
3. `_PATCH_RULES["facts"]` updated:
   - Instruction text no longer hints `single_assembly` as a default; instead
     it explicitly says `"unknown"` is the safe default and must be upgraded
     only when source evidence or planning feature contract justifies it.
   - Minimal example uses `"model_scope": "unknown"` until evidence upgrades it.
4. `ContextFactValue` already exists (Phase 8B Step 2); rendering rule
   "unknown / compatibility_default must NOT enter the prompt" is added as a
   unit test (`tests/test_facts_prompt_omits_unknown_context.py`).
5. New truth-violation codes added to `closed_loop/truthfulness.py`:
   - `facts_default_scope_contamination`
   - `facts_default_value_rendered_as_authoritative`
   - `facts_unknown_context_rendered`
   - `facts_real_canary_claim_without_acceptance`

The remaining structural fixes (Gate transaction kernel, evidence-ledger-backed
contract compiler, deterministic targeted repair via retry loop, clone
validation, accepted_input_hash replay, downstream recompilation hash chain)
are deferred to Phase 8C Step 1 and Step 2.

---

## 5. Current real canary failure mode

Last successful VERA4 Facts canary: `data/runs/phase8b_step2_vera4_facts/runs/run_001`.
The run *passed* only because the revision LLM happened to generate the
correct `multi_assembly_core` patch. The initial LLM output was
`single_assembly`. The deterministic skeleton did not contribute (empty), the
merge did not contribute (no-op on unresolved skeleton), and the targeted
repair did not contribute (not wired). The pass is therefore **LLM-variance
driven, not deterministic**.

This is the central defect that Phase 8C Step 1 + Step 2 must eliminate.
