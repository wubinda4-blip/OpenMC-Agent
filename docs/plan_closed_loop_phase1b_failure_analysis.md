# Facts Gate Phase 1B failure analysis

## Evidence inspected

The analysis uses the persisted real-model run at
`data/runs/phase1_vera4_facts_advisory_rerun/`, specifically its two
`facts_review_raw_*.json` artifacts and `incremental/plan_build_state.json`.
No API keys or hidden reasoning are copied into this document.

## What happened

The adapter requested its automatic structured-output mode.  The saved state
does not preserve the adapter's per-request mode metadata, so the precise
provider-side `json_schema` versus `json_object` fallback cannot be proven
retroactively.  The raw response proves that the effective critic content was
plain natural language: the first call produced a long explanation followed
by an incomplete JSON fragment, and the retry also explained that it had not
been given the output schema before appending invalid JSON.  Neither response
was an empty response or a truncated JSON document.

The pre-1B retry prompt was exactly a short format instruction with the prior
`schema_invalid` label.  It did not include the EvidencePack, output schema,
or the prior raw payload.  Consequently the second call could not retain the
source evidence and supplied fabricated evidence hashes.  The run made two
critic calls and persisted `plan_loop_additional_llm_calls=2`; that count is
accurate.  The old state recorded `review_count=0`, no issue fingerprint, and
no candidate hash because revision was never entered.  Its stage nevertheless
became `reviewed`, which was an incorrect success-like label.  No Graph route
existed for `awaiting_human`.

## Production-closure changes

Contract 0.3 adds `review_failed`.  Schema retries now repeat the complete
EvidencePack and JSON Schema, raw JSON extraction only accepts a complete
object, and each call stores requested/actual structured-mode metadata for
future diagnosis.  Review rounds, validation rounds, attempt fingerprints,
candidate hashes and global call budgets are persisted before they can affect
control flow.  A revision is now evaluated and independently re-reviewed on
a clone; only a clean re-review atomically invalidates the old facts envelope,
writes the repair envelope, and invalidates facts dependents.  Typed Facts
questions use a separate `ask_plan_expert → resume_plan_closed_loop` graph
route and never reuse capability-expert free text.

## Remaining qualification work

The old VERA4 run is evidence of a real failure, not a passed canary.  A new
network-enabled advisory and mutation/revision requalification must be run
against the 0.3 code before claiming a real-LLM Facts Gate canary.

On 2026-07-17 this was attempted through the available execution channel.
A minimal real `ds:deepseek-v4-flash` structured-output probe succeeded
(`json_schema`, 164 response characters), but every full VERA4 invocation was
terminated by the channel after it printed its start/import marker and before
the first plan artifact was written.  This is an execution-channel timeout,
not a failed or passed Facts Gate result; no canary claim is made from it.
