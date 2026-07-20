# Phase 8C Step 2D — LLM Robustness Closure

## Purpose

Close the remaining model-side structured-output and investigation-budget
failure paths before Material-Universe skeleton-first generation. This step
hardens the protocol without changing physical-model semantics, renderer
capability boundaries, or the plan contract schema.

## Protocol

Gate reviewers and investigation planning use one bounded structured-output
transaction. A transaction receives a canonical, immutable business payload,
the target schema, a role identifier, a caller, and the existing campaign
budget counter. It permits at most two model attempts:

1. request the provider's structured mode and apply only semantic-free JSON
   extraction/normalization;
2. if parsing or schema validation still fails, resend a schema-repair prompt
   that carries the same business payload hash.

Each attempt records prompt hash, payload hash, raw-output hash, extraction
strategy, schema/parse errors, output-mode fallback, and budget accounting.
Reasoning content is never persisted. Payload-hash drift, stale-output reuse,
unbudgeted calls, or a second failed attempt are deterministic fail-closed
outcomes.

Transport retries remain owned by the provider client. Domain normalizers stay
in the reviewer/investigation adapters; the shared transaction cannot invent
facts, findings, material values, or geometry.

## Investigation completion

The mandatory baseline runs first. After every supplemental action, Python
recomputes a patch-type-specific semantic coverage matrix:

- Facts targets come from the feature/evidence contract.
- Materials targets come from material requirement role/variant identifiers.
- Universes targets come from universe requirement/profile identifiers.

Targets are satisfied only by source-backed evidence, human confirmation, or a
deterministically recorded unresolved state. Once all required targets are
satisfied, remaining model-suggested actions are skipped and recorded rather
than consuming budget. If coverage is incomplete when the budget is exhausted,
controlled mode remains blocking.

## Compatibility and audit

New telemetry fields have backward-compatible defaults. The campaign resume
fingerprint includes a structured-output policy hash so old artifacts cannot
silently resume under a changed protocol. Truthfulness auditing covers payload
hash drift, stale output reuse, unbudgeted retries, false completion, and
budget blocking after semantic completion.

## Acceptance

The implementation must pass the repository's full deterministic test suite,
compileall, fake workflow benchmark and regression diff. A real VERA4 run must
keep Facts accepted, complete Facts/Materials/Universes investigation without
repairable JSON or post-coverage budget blocking, reach the Material-Universe
gate with zero truth violations, and leave only deterministic MU findings if
that gate remains blocked.
