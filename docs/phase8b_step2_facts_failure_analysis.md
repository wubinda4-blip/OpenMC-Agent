# Phase 8B Step 2: VERA4 Facts Gate Failure Analysis

## Context Default Contamination

### Root Cause

`PatchGenerationContext.model_scope` defaulted to `"single_assembly"` at
`openmc_agent/plan_builder/patch_generator.py:77`.

Because the `_context_block` function in
`openmc_agent/plan_builder/patch_prompts.py:1039-1073` checks `if val is
None: continue` but `"single_assembly"` is a non-None non-empty string,
**every** Facts prompt included:

```
model_scope: single_assembly
```

regardless of the actual problem scope.  This contaminated the LLM's
prior, making it highly likely to copy `single_assembly` into its
output without reasoning about the requirement.

### Evidence from Canary

The VERA4 canary (`data/runs/phase8b_step1_vera4_mu_clean`) produced:

- Facts patch `model_scope`: `"single_assembly"`
- All other multi-assembly fields: `null` or empty

The 6 consistency findings (all `blocking`):

| Code | Path | Expected | Actual |
|---|---|---|---|
| `facts.model_scope_conflicts_with_planning_features` | `/model_scope` | `multi_assembly_core` | `single_assembly` |
| `facts.multi_assembly_contract_incomplete` | `/assembly_count` | `9`, etc. | `null` |
| `facts.spacer_grid_contract_missing` | `/has_spacer_grids` | `true` | `false` |
| `facts.localized_insert_contract_missing` | `/localized_insert_requirements` | list with entries | empty |
| `facts.localized_insert_profile_contract_missing` | `/localized_insert_requirements` | profiles | none |
| `facts.fuel_variant_contract_missing` | `/fuel_variant_requirements` | >=2 variants | empty |

The `resolve_planning_scope` correctly detected conflict:
- feature contract: `multi_assembly_core` (from feature detector)
- facts patch: `single_assembly` (from LLM output contaminated by default)

## Investigation Coverage Gap

The FactsInvestigationCoverage at the time of the canary checked only
**tool call presence** (requirement_structure_inspected,
patch_schema_inspected, source_search_executed, source_backed_claim_count).
It did NOT verify:

- Was `model_scope` resolved with source-backed evidence?
- Were `has_spacer_grids`, `fuel_variant_requirements`,
  `localized_insert_requirements` searched?
- Does the evidence ledger contain claims semantically matching
  mandatory targets?

The old check would pass with **one unrelated source-backed claim** even
if no mandatory target was covered.

## Failure Classification

| Category | Involved? | Details |
|---|---|---|
| **A. Context contamination** | **Yes** | `model_scope: single_assembly` default leaked into prompt |
| B. Investigation coverage failure | Partial | Investigation ran but had no mandatory-target check |
| C. Evidence transfer failure | No | Evidence was not consulted because LLM copied default |
| D. Skeleton compiler failure | N/A | No skeleton existed |
| E. Structured output failure | No | JSON was valid |
| **F. Semantic completion failure** | **Secondary** | Even without default, weak LLM may still produce wrong output |

**Primary root cause: Context default contamination (A).**

## What Phase 8B Step 2 Fixes

1. **`model_scope` default changed to `None`** — prompt is never
   pre-contaminated.
2. **`_context_block` skips `None`/`unknown`/empty** — silent about
   unresolved fields.
3. **`ContextFactValue` provenance model** — every context field that
   enters the prompt carries provenance_kind.
4. **`FactsRequirementSkeleton`** — source-backed and
   deterministically-derived facts are locked; LLM cannot modify them.
5. **`FactsEvidenceContract`** — hard constraints compiled from
   skeleton; LLM output must satisfy.
6. **`check_facts_semantic_coverage()`** — mandatory targets per
   feature contract (model_scope, assembly_count, spacer_grids,
   fuel_variants, localized_inserts must be covered).
7. **`FactsDeterministicRepair`** — skeleton-locked fields are
   repaired deterministically without LLM involvement.
8. **`run_facts_skeleton_preflight()`** — pre-review check that
   catches scope contradictions, missing slots, and immutable-field
   modifications.
