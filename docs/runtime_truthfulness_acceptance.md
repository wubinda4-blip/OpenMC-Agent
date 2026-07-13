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
- **T5-T6**: NOT RUN. Lane B executor not yet implemented.

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
