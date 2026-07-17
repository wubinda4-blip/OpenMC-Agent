# P2 Plan Closed-Loop Phase 3 Design

## Scope

Phase 3 makes only registered, owner-scoped dependency recovery executable.
It does not add a reviewer, supervisor, monolithic fallback, renderer repair,
or an OpenMC runtime repair path.  Ownership is derived from issue codes and
typed evidence, never from a model-generated message.

## Existing retry entry points

`route_retry` remains the local patch-generation router: it selects a current
patch or immediate dependency after schema/validator failure.  Validation
repair is a local RFC6902 clone/evaluate path.  Facts and Placement gate
revision are current-gate transactions.  A Placement dependency request is a
cross-patch request.  `PlanningRootCause` is a deterministic assembly/readiness
classifier.  Graph `generate_plan` retries, `reflect_plan`, semantic-audit
repair, monolithic fallback, and runtime repair are separate workflows and are
not called by the Phase 3 controller.

## Transaction boundary

The controller normalizes all supported sources into an
`ExecutablePlanRetryRequest`, compiles the dependency closure, and produces
the owner candidate in a deep clone.  Schema and owner acceptance checks run
before commit.  Commit replaces only owner envelopes atomically with
`source="retry"`; on exception the original state is restored.  Only after
that commit are true dependents invalidated.  A downstream rebuild is a
separate resumable transaction: its failure preserves the validated new owner
and leaves only downstream patches invalid.

## Replay and limits

Facts changes invalidate downstream semantic patches and reset affected gate
stages to pending.  Materials changes invalidate Universes and its descendants;
Universe changes invalidate profiles, placement owners, and affected axial
patches.  The graph is the single source for those closures.  Retry request
fingerprints use issue codes, owner hashes, required IDs/properties, scope,
task-plan, and gate hashes; messages, timestamps, and artifact paths are not
semantic inputs.  Duplicate candidates stop the request rather than triggering
a third full planning pass.  Advisory compiles and records a plan only;
controlled may execute one deterministic owner path at a time; off remains
inactive.
