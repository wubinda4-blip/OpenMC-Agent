# Phase 5 Pre-existing Failure Baseline

HEAD: `91e8dcc` (Fix controlled facts gate retry bypass)

## Full suite summary

```
5 failed, 2258 passed, 2 skipped, 392 deselected in 148.10s
```

## Failures

### 1. `tests/test_axial_overlay_semantic.py::test_P_max_retry_fail_closed`

- **Error**: `AssertionError: assert 'patch_generation.max_attempts_exceeded' in ['patch.axial_overlays.mode_semantic_contradiction', 'patch_generation.no_progress_duplicate_candidate']`
- **Root cause**: Phase-3B no-progress duplicate-candidate detection was added after this test was written.  When the fake LLM returns the same contradictory overlay patch twice, the generator now emits `patch_generation.no_progress_duplicate_candidate` instead of the older `patch_generation.max_attempts_exceeded`.  The test assertion has not been updated.
- **Phase 5 relevance**: None.  This is a pre-existing test-assertion drift caused by Phase-3B no-progress enforcement, not an axial-geometry-gate issue.
- **Introduced by**: `70532c4` (Add Graph retry/human resume and cycle-budget closure) or later Phase-3B commit.

### 2. `tests/test_fullcore_patch_dependencies.py::test_patch_dependents_assembly_catalog`

- **Error**: `_PATCH_DEPENDENTS` mapping missing `axial_layers` / `settings` entries.
- **Root cause**: The fullcore dependency-expansion test predates the axial patch family and does not know about `base_path_axial_profiles`, `axial_layers`, `axial_overlays` dependents.
- **Phase 5 relevance**: None.  Pre-existing since Phase 3B.

### 3. `tests/test_fullcore_patch_dependencies.py::test_expand_repair_targets_includes_core_layout`

- Same root cause as #2.

### 4. `tests/test_fullcore_patch_dependencies.py::test_expand_repair_targets_from_facts`

- Same root cause as #2.

### 5. `tests/test_vera3_patch_fixtures.py::TestVERA3BAssembly::test_3b_assembles_without_25k_json`

- **Error**: `assert 35496 < 35000` — VERA3 3B patch fixture bytes exceed the 35 000 threshold.
- **Root cause**: The VERA3 3B fixture grew past the byte budget in Phase 3B.
- **Phase 5 relevance**: None.

## Diff vs Phase 4 baseline

Phase 4 baseline had 4 failures (the last 4 above).  HEAD `91e8dcc` adds one new failure (#1, `test_P_max_retry_fail_closed`) caused by Phase-3B no-progress enforcement landing after the Phase 4 baseline was recorded.  This is **not** caused by any Phase 5 change.
