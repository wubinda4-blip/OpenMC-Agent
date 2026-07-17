# Plan Closed-Loop Phase 3B Gap Analysis

Audit baseline: HEAD `bc45c65` (with `1be501e` axial replay on top).  Contract
version stays at `0.5` unless explicitly noted.

## 1. Issues with a registry entry but no producer

| Owner       | Registered codes                                                                 | Producer wired?                                                                                                |
| ----------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `facts`     | `_FACTS_CODES`                                                                   | NO.  `retry_owner_policy` lists them; `executor.py` only constructs a producer for the Placement gate branch.  |
| `materials` | `_MATERIAL_CODES` + `fullcore.grid_density_missing`                              | NO.  Material readiness runs `record_targeted_retry_attempt` (logging only); no `ExecutablePlanRetryRequest`.  |
| `universes` | `_UNIVERSE_CODES`                                                                | PARTIAL.  Only reachable when Placement gate emits `localized_insert.required_universe_missing`.                |
| task plan   | `_TASK_PLAN_CODES`                                                               | YES (deterministic `RECOMPUTE_TASK_PLAN`), but never re-classified afterwards.                                  |
| placement   | `_PLACEMENT_CODES`                                                               | YES via the in-line `_produce_owner_candidate` nested inside the executor.                                     |

There is no `RetryCandidateProducerRegistry`; the only producer is a nested
closure local to `executor.py:1699`.

## 2. Acceptance checks that are only string declarations

`RetryOwnerPolicy.required_acceptance_checks` is a `list[str]` carried into
`RetryExecutionPlan.validation_steps`.  Nothing ever invokes them.  The only
real check is the in-line `_validate_owner_candidate` in the executor, which
only tests universe IDs.

Concrete missing checks:

- Facts: schema, consistency preflight, resolved scope, feature coverage,
  Facts Critic, canonical task-plan reconstruction.
- Materials: schema, species, composition basis, fuel-variant identity,
  density policy, material readiness re-run.
- Universes: schema, material references, required exact IDs, fuel-variant
  reachability, Placement preflight.
- Placement: preflight delta, Critic re-review, blocking-set shrink.

## 3. Request fields lost in legacy dict conversion

`normalize_retry_request` reads generic keys (`required_ids`,
`affected_json_paths`, `required_properties`, `finding_ids`).  Concrete
sources that lose information:

- `PlacementDependencyRetryRequest` carries `dependency_patch_type`,
  `required_ids`, `downstream_patch_types`.  Only `required_ids` survives,
  and only because the source dict happens to use that key.  `reason`,
  `dependency_patch_type`, gate input hash are dropped.
- Placement preflight issues carry `expected`/`actual` and `requirement_id`
  in `_issue(...)` but `normalize_retry_request` never maps them into
  `targets[].required_ids` or `metadata`.
- Material readiness issues carry `material_id`, `consumer_id`,
  `required_property`.  Only the generic normalization path would see them,
  and that path is never invoked from readiness.
- Facts critic findings carry `affected_json_paths`, `evidence_hashes`,
  `expected_value`, `current_value`.  None of these are propagated.

There are also no typed builders; every caller is expected to hand-craft a
dict that matches `normalize_retry_request`'s expectations.

## 4. Gates marked replayed without actually running

`invalidate_gates_for_patch_change` mutates `PlanStageStatus.PENDING` and
increments `plan_retry_gate_replay_counts`.  That counter is named "replay"
but no replay occurred — only invalidation.  `RetryRoundRecord.gates_replayed`
is then populated from this same list, so the artifact lies about whether a
preflight/critic/decision actually ran.

Real replay would require:

- rebuild `PlanEvidencePack` / `PlacementEvidencePack`,
- run deterministic preflight,
- invoke Critic,
- record `PlanReviewDecision`,
- transition the stage to `accepted` / `reviewed` / `review_failed`.

None of that exists in the controller today.

## 5. Budget fields that never increment

`plan_retry_budget` is initialized empty and read in
`compile_retry_execution_plan` (`budget_snapshot`) but never written.  Owner
regeneration counts (`plan_retry_owner_regenerations`) are bumped only after
a successful commit, so a failed generation attempt is invisible to the
budget.  `max_total_retry_llm_calls`, `max_owner_regenerations_per_patch`,
`max_gate_replays_per_gate` are declared on the policy but never enforced.

## 6. Failures that still clear PlanBuildState

`graph.py` sets `incremental_regeneration_pending=True` and rewrites
`requirement` via `_incremental_regeneration_requirement` whenever the
incremental executor returns `ok=False`.  For Phase 3 registered issues this
is wrong: the typed retry path should preserve the state and the original
requirement.  Capability failure (`planning.capability_invalid`) takes a
separate branch that also rebuilds the requirement from scratch.

## 7. Terminal requests left in the pending list

`execute_plan_retry_loop` removes a request id from
`plan_retry_pending_request_ids` only on `RESUMED` (line 319) and
`RECOMPUTE_TASK_PLAN` (line 256).  Terminal outcomes (`NO_PROGRESS`,
`FAILED`, `BUDGET_EXHAUSTED`, `UNSUPPORTED_REQUEST`, `BLOCKED`,
`AWAITING_HUMAN`) leave the id in the pending list, so the next loop
invocation re-selects the same dead request.

## 8. Paths that may mix `pin_map` and `assembly_catalog`

Two places:

- `placement_issue_policy.py` lines 10-20: multiple codes declare
  `["assembly_catalog", "pin_map"]` as owner — exactly the forbidden
  simultaneous selection of two mutually exclusive patch families.
- `retry_owner_policy.py` line 103: when the Placement issue dict has no
  `owner_patch_type`, the policy returns `["assembly_catalog", "pin_map"]`
  as the default fallback instead of failing closed.

The canonical scope (`single_assembly` vs `multi_assembly`/`full_core`) is
available on `state.resolved_planning_scope` but is not consulted.

## 9. Tests that cover protocol but not executor end-to-end

- `test_retry_protocol_models.py` — only model fingerprint validators.
- `test_retry_request_normalization.py` — only `normalize_retry_request`.
- `test_retry_advisory_mode.py` — advisory short-circuit only.
- `test_plan_closed_loop_executor_integration.py` — checks that
  `plan_build_state` is emitted, not that a retry actually commits a patch.

No test exercises: readiness issue → typed request → candidate → acceptance
→ atomic commit → downstream resume → Gate replay → reclassification →
resolved.  The VERA3/VERA4 fixtures are not used for mutation replay.

## 10. Other observed defects

- `RetryExecutionOutcome.workflow_behavior_changed=True` is set on `RESUMED`
  even when the downstream resumer did not actually run (no
  `downstream_resumer` was supplied).
- `_atomic_owner_commit` requires the old envelope to exist; there is no
  `allow_create_owner_patch` path for a canonical task plan that legitimately
  lacks the owner patch yet.
- `requires_human=True` requests return `AWAITING_HUMAN` but never create a
  `HumanPlanQuestion`; the graph has no `execute_plan_retry` /
  `resume_plan_retry` nodes, so the request is silently lost.
- `reclassify_retry_outcome` does not exist; `resolved_issue_codes` is
  populated with `[request.reason_code]` unconditionally on commit
  (`retry_controller.py:320`), so the artifact can claim resolution even if
  the downstream rebuild reintroduces the same code.
- No cycle detection beyond duplicate candidate hashes; Facts↔Universes
  loops and task-plan hash oscillation are invisible.
- `RetryPatchGenerationContext` does not exist; the producer closure calls
  `generate_patch` with a vanilla `PatchGenerationContext`, so the LLM never
  sees required IDs, protected invariants, or prior failure codes.

## Priority order for Phase 3B

1. Typed request builders + idempotent registration (Sections 3, 3.1, 3.2).
2. Owner policy scope-aware fail-closed (Section 4).
3. Retry-aware patch prompt context (Section 6).
4. Producer registry with Facts / Materials / Universes / Placement entries
   (Sections 5, 5.1-5.6).
5. Acceptance registry that actually runs checks (Section 7).
6. Bounded retry loop with reclassification (Sections 9, 12).
7. Downstream resume helper (Section 10).
8. Gate invalidation / replay separation with real replay (Section 11).
9. Budget, no-progress and cycle enforcement (Sections 13, 14).
10. Graph `execute_plan_retry` / `resume_plan_retry` + routers (Section 16).
11. VERA3/VERA4 offline mutation replay (Sections 20, 21).
