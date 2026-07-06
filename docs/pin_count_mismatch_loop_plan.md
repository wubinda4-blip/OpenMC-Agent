# Pin Count Mismatch Loop Plan

## Summary

For dense natural-language pin maps such as `Input/case3.md`, the agent must
turn hard input counts into IR constraints, validate the expanded lattice
against those constraints, and block rendering until mismatches are repaired.
The executor remains a faithful renderer and must not rewrite
`universe_pattern`.

## Implementation Plan

- Extract hard count constraints from the original requirement and inject them
  into generation and reflection prompts.
- Treat `LatticeSpec.expected_counts` as authoritative when it comes from the
  input document.
- Share lattice pin-count diagnostics between `validator.py` and renderers so
  core plans cannot bypass mismatch checks.
- Route `lattice.pin_count_mismatch` to `reflect_plan` with concrete actual vs
  expected diffs.
- Stop before render/export/run when mismatch remains after retries.

## Regression Target

`case3` MOX assembly must validate at `mox43/mox7/mox87/guide/fission =
64/100/100/24/1`, and rendered XML must preserve the same counts as IR.
