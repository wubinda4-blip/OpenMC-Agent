# Runtime Bounded Recovery Strategy (P1-RUNTIME-R5/R6)

## Scope

R5/R6 adds an independent **post-execution** runtime supervisor. It does not
extend the planning `RunSupervisorAction` enum and cannot generate patch
content, alter failure classification, invoke full-plan regeneration, or route
to monolithic reflection.

## Actions and precedence

1. Success: `finish_success`
2. User cancellation / no progress / exhausted budget: `stop`
3. Environment or human fact blocker: `request_human_confirmation`
4. Plan-fixable with deterministic policy: `attempt_deterministic_repair`
5. Plan-fixable with safe LLM diagnosis: `attempt_llm_repair`
6. Pure transient failure: one `retry_same_plan`
7. Everything else: `stop`

The Python policy computes the allowlist before a client sees the state. LLM
output is vetoed when it selects a disallowed action, bypasses environment or
human blockers, or requests a route outside the bounded runtime loop.

## Budgets

Default `RuntimeLoopBudget` is deliberately small: 4 iterations, 3 commits,
3 re-executions, 3 deterministic attempts, 2 LLM diagnoses/proposals, one
transient retry, 4 candidate OpenMC checks, and 2 no-progress steps. Planning
`retry_count` is never consumed by runtime recovery.

## Progress and loop safety

The runtime state fingerprint includes plan/build-state hashes, primary failure
fingerprint and code, stage, last action, and budget summary; it excludes time,
paths, PIDs, UUIDs, and full stdout. A repeated primary fingerprint after a
commit is converted into no-progress and only `stop` remains allowed.

## Artifacts and resume

Each supervisor decision writes compact JSON under
`runtime_loop/iteration_NNN/`; `runtime_loop_manifest.json` records the last
committed-source-safe state. Candidate artifacts remain under
`runtime_repair/iteration_NNN/candidate/`. Neither path treats a candidate nor
a statepoint as formal runtime output.

## Limits

Ambiguous geometry remains diagnose-only until a rendered-object provenance
map supplies a concrete patch-relative allowlist. The supervisor cannot expand
that authority. Material facts, nuclear data, physical dimensions, axial
bounds, pin maps, and keff remain outside recovery authority.
