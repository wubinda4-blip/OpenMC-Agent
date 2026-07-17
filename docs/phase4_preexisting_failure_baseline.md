# Phase 4 Pre-existing Failure Baseline

Recorded before starting Phase 4 development on HEAD `37f4eb1`.

## Full suite result

```
4 failed, 2187 passed, 2 skipped, 392 deselected
```

## Failures

### 1. tests/test_fullcore_patch_dependencies.py::test_patch_dependents_assembly_catalog
- **Error**: `assert 'axial_layers' in ('core_layout',)` — the `_PATCH_DEPENDENTS["assembly_catalog"]` mapping does not include `axial_layers`.
- **First appearing commit**: pre-dates Phase 3B (confirmed via `git stash` on `bc45c65`).
- **Stable**: reproduces on every run.
- **Phase 4 related**: NO.  This is a separate `_PATCH_DEPENDENTS` dict used by validation-repair cascade, not by the closed-loop dependency graph or the Material-Universe gate.
- **Root cause**: the repair-cascade mapping was not updated when the canonical dependency graph (`dependency_graph.py`) was made the single source of truth.

### 2. tests/test_fullcore_patch_dependencies.py::test_expand_repair_targets_includes_core_layout
- **Error**: same root cause as #1 — `axial_layers` not in expanded repair targets.
- **Phase 4 related**: NO.

### 3. tests/test_fullcore_patch_dependencies.py::test_expand_repair_targets_from_facts
- **Error**: `assert 'settings' in [...]` — `settings` is not a dependent of `facts` in the repair-cascade mapping.
- **Phase 4 related**: NO.

### 4. tests/test_vera3_patch_fixtures.py::TestVERA3BAssembly::test_3b_assembles_without_25k_json
- **Error**: `assert 35496 < 35000` — total VERA3B patch bytes exceed the threshold by 496 bytes.
- **Phase 4 related**: NO.  Pure size threshold, unrelated to gate logic.

## Acceptance principle for Phase 4

- Phase 4 must not introduce any new failure.
- These 4 pre-existing failures must remain identical (same node IDs, same error shape).
- The final report will include a before/after failure-set diff proving no new failure was introduced.
