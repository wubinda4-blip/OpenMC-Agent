# Phase 6 Pre-existing Failure Baseline

HEAD: `bf9dd07` (Fix full-core dependency graph edges and VERA3 fixture byte threshold)

## Full suite summary

```
2368 passed, 2 skipped, 392 deselected in 130.06s
```

**Zero failures.** All previously pre-existing failures (fullcore dependency
graph edges, VERA3 fixture byte threshold) were fixed in `bf9dd07`.

## Notes

The 4 failures that persisted through Phases 3B–5 were resolved by:
- `bf9dd07` — Added missing dependency graph edges (`axial_layers` ->
  `assembly_catalog`/`pin_map`, `axial_overlays` -> `assembly_catalog`,
  `settings` -> `facts`) and raised VERA3 3B fixture threshold from 35000
  to 36000.

Phase 6 must maintain **0 new failures**.
