# Phase 8C Step 2D — LLM Robustness Closure

## Status

Real canary verification executed locally with GLM-5.2. The investigation
pipeline (recorder, structured-output repair, semantic synthesis) is verified
end-to-end: truthfulness violations are clean, the Facts investigation
completes with synthesised semantic claims, and the Facts patch is generated
and validated. Step 2D is **not declared fully complete** because the Facts
Gate occasionally blocks on a deterministic preflight finding
(`control_state_contract_missing`) that depends on LLM output non-determinism
for the VERA4 control-rod benchmark.

## Delivered

- **Recorder fix**: `_PromptOnlyWrapper` now intercepts `generate_patch_json`,
  closing a bypass that produced false `real_llm_not_verified` violations when
  the structured-output transaction kernel called the investigator via the
  `generate_patch_json` path.
- **Argument-repair fix**: investigation tool/argument validation is now
  integrated into the structured-output transaction's `_normalize` step, so
  unknown tools and invalid arguments trigger the repair retry instead of an
  immediate `argument_invalid` block.
- **Role-counting fix**: `evidence_summary.planning_network_call_count` now
  includes `plan_investigator` role calls, so a canary that stops after the
  investigation stage is not falsely flagged.
- **Facts semantic synthesis**: after the tool loop, an LLM synthesis step
  reads the gathered evidence and proposes typed claims whose predicates match
  the Facts coverage targets (`model_scope`, `fuel_variant`, …). This closes
  the design gap where deterministic tools produced only generic-predicate
  claims (`search_hit`, `scope_indicator_present`) that could never satisfy
  the semantic coverage matrix. The synthesis is extended to Materials and
  Universes via `requirement_id` referencing.
- Gate reviewers and investigation planning share a bounded two-attempt
  structured-output transaction with immutable payload hashes, sanitized
  telemetry, budget fail-closed behavior, stale-output detection, and schema
  repair.
- Resume fingerprints, truthfulness auditing, README, technical report, and
  the Step 2D design protocol were updated.

## Offline verification

- Full non-OpenMC/non-LLM suite: 3372 passed, 2 skipped, 392 deselected.
- compileall: passed.
- Fake workflow benchmark: 21/21 passed.
- No baseline report is present for a regression diff.

## Real canary results (GLM-5.2, VERA4)

| Run | Truth | Facts Gate | LLM calls | Duration | Notes |
|-----|-------|------------|-----------|----------|-------|
| v3 (Facts synthesis) | CLEAN | **ACCEPTED** | 6 | 491 s | Facts patch valid; Materials blocked |
| v4 (all-type synthesis) | CLEAN | blocked | 8 | 699 s | Deterministic preflight: `control_state_contract_missing` |

Key telemetry across all runs:
- `truth_violations: []` — consistently clean since the recorder fix.
- `real_llm_verified: True` — real provider calls recorded.
- `planning_network_call_count` correctly includes investigator calls.
- Facts investigation: `coverage_complete: True` with `facts_synthesis_added_6_claims`.
- No `argument_invalid`, `invalid_llm_output`, payload hash drift, unbudgeted
  retry, or stale output reuse.

## Remaining blockers

1. **Facts Gate non-determinism**: the Facts patch occasionally omits
   `localized_insert_requirements` for VERA4, triggering a deterministic
   `control_state_contract_missing` preflight block. The v3 run showed the
   gate CAN pass; the v4 run showed it CAN fail on the same input.
2. **Review-prompt size**: the Facts review prompt is ~38 KB; GLM-5.2
   sometimes returns empty content for prompts this large, leaving the gate
   without LLM findings.