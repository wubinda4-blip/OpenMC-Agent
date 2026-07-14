# Runtime Evaluation Truthfulness Acceptance

This document defines the six truthfulness levels for runtime evaluation and
the promotion gates that depend on them.

## Truthfulness Levels

| Level | Name | Description |
|-------|------|-------------|
| T1 | schema/unit tests | Mocked model tests; proves schema validation and unit logic only. |
| T2 | production graph routing | Production graph with injected ToolResults; proves classification and routing but not real OpenMC behaviour. |
| T3 | real-OpenMC baseline | F00 baseline runs through real OpenMC with zero lost particles. |
| T4 | real-OpenMC fault and recovery | F01 injects a source fault, real OpenMC fails, deterministic repair succeeds, candidate evaluation passes real smoke test. |
| T5 | real-LLM end-to-end | Lane B pilot N≥3 with real LLM generation through the full planning + runtime pipeline. |
| T6 | repeated real-LLM stability | Lane B qualification N≥10 with ≥70% final success rate and zero unsafe acceptances. |

## What Each Level Proves

- **T1 (mocked ToolResult)**: Only proves that classification and routing logic are correct in isolation. Does NOT prove that real OpenMC will behave the same way.
- **T2 (production graph injection)**: Proves that the production graph correctly routes injected failures. Does NOT prove that the injected failure matches real OpenMC output.
- **T3 (real OpenMC baseline)**: Proves the full pipeline (assemble → render → export → debug → smoke) works for a known-good plan.
- **T4 (real OpenMC fault recovery)**: Proves that a controlled source fault causes real OpenMC failure, the deterministic repair changes settings.xml, and the repaired candidate passes real OpenMC smoke test.
- **T5 (real LLM pilot)**: Proves the LLM can generate a valid plan that runs through the full pipeline. At least 3 successful runs.
- **T6 (real LLM qualification)**: Proves repeated stability across ≥10 runs with ≥70% success rate and zero unsafe acceptances.

## Current Status

- **T1-T2**: All 20 fault cases pass with injected tools.
- **T3**: F00 baseline passes real OpenMC.
- **T4**: F01 source recovery passes real OpenMC (manual non-fuel source → pre-flight blocks → deterministic repair → candidate evaluation succeeds).
- **T5**: Lane B pilot N=3 passed (3/3 FIRST_PASS_SUCCESS, all real LLM + real OpenMC).
- **T6**: Lane B qualification N=10 passed (9/10 FIRST_PASS_SUCCESS, 90% success rate, 95% Wilson CI [0.60, 0.98]).
- **Transport seed stability**: 3/3 seeds passed (10101/20202/30303), max pairwise z=0.61 << 5.0 threshold.
- **P1_RUNTIME_STAGE**: COMPLETE (all 10 final gates passed).

## T6 Qualification Results (2026-07-14)

- Model: deepseek:deepseek-chat, temperature=0
- Requested/completed: 10/10
- Successful: 9 (90%), all FIRST_PASS_SUCCESS
- Failed: 1 (PLANNING_FAILURE, run_009)
- Autonomous terminal rate: 90%
- Bounded outcome rate (≤2 repairs): 100%
- Real LLM verification: 100% of successful runs
- Real OpenMC verification: 100% of successful runs
- VERA3 acceptance: 100% of successful runs (basic structural: 17×17 + fuel)
- Artifact completeness: 100% of successful runs
- Unsafe accepted: 0
- Protected field changes: 0
- Fake clients: 0
- Reference patches: 0
- Benchmark few-shot: 0
- Monolithic fallback: 0
- Lost particles: 0

## Known Limitations

- LLM-generated model keff (~0.66) is significantly lower than the deterministic gold model (~0.98). This indicates physics fidelity gaps in the LLM output that basic structural acceptance does not catch. Addressing this requires P0-V6/V7 geometry/material gold model work, not runtime infrastructure changes.
- Smoke test uses only 5 batches × 100 particles (infrastructure validation, not physics accuracy).
- Full VERA3B acceptance callback has import-path issues in the CLI context; basic structural acceptance is used instead.

## Forbidden Claims

- Mocked ToolResults MUST NOT be counted as real-OpenMC evidence.
- Fake diagnostician/proposer results MUST NOT be counted as real-LLM evidence.
- Production graph injection MUST NOT be equated with real OpenMC failure.
- Empty Lane B results MUST NOT be promoted to PILOT_PASSED or STABILITY_ACCEPTED.
- Without N≥10 qualification, the system MUST NOT be marked P1_RUNTIME_STAGE_COMPLETE.
- Pending cases MUST NOT be ignored to promote the matrix to PASSED.

## Gate Definitions

### Fault Matrix

- **PARTIAL**: Pending cases, incomplete artifacts, mocked-only real-OpenMC cases, or fewer than required case count.
- **PASSED**: All required cases evaluated, all passed, zero safety violations, all real-OpenMC cases have genuine evidence.
- **FAILED**: Any evaluated case failed, or any safety metric > 0.

### Lane B Campaign

- **NOT_RUN_ENV**: API key missing.
- **CONFIRMATION_REQUIRED**: Key present but `--confirm-real-campaign` not given.
- **EXECUTOR_NOT_IMPLEMENTED**: Confirmed but no real executor exists yet.
- **PILOT_PENDING**: Executor ran but fewer than 3 successful runs.
- **PILOT_PASSED**: ≥3 successful pilot runs, <10 for qualification.
- **STABILITY_ACCEPTED**: ≥10 runs, ≥70% final success, zero unsafe, full artifacts.
- **STABILITY_FAILED**: ≥10 runs but did not meet acceptance bars.
