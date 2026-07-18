# Phase 7A Pre-existing Failure Baseline

**HEAD at start**: `5c44da7fdb25594b5d4c97276fbb7f79204dbe72`
**Date**: 2026-07-18

## Suite

```
conda run -n openmc-env python -m pytest -q -m "not openmc and not requires_llm"
```

## Result (recorded live)

```
2494 passed, 2 skipped, 392 deselected in 83.42s (0:01:23)
```

- **0 failed**.
- 2 skipped are non-OpenMC tests with unrelated skip markers (unchanged
  from Phase 6 / pre-existing).
- 392 deselected carry `openmc` or `requires_llm` markers and need real
  infrastructure to run.

## Fake workflow benchmark

```
conda run -n openmc-env python scripts/run_workflow_benchmark.py \
    --cases tests/fixtures/evaluation_cases.json \
    --model fake --mode plan-only \
    --out data/evals/workflow/fake_current
```

Result: **21/21 cases pass (100%)** — recorded in
`data/evals/workflow/fake_current/benchmark_summary.md`.

## Notes

- The HEAD `5c44da7` is the tip of Phase 6 / P0 closure; no test or
  fixture regressions are present at the start of Phase 7A.
- The user-side modification in `openmc_agent/inspect.py`
  (`patch_output_mode` knob, fully self-contained in `inspect.py`) is left
  untouched and is **not** part of any Phase 7A commit.
- `Plan阶段LLM智能化闭环.md` (untracked) is preserved and never staged.
