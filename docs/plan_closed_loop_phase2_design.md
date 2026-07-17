# P2 Plan Closed-Loop Phase 2 Design

## Scope and boundary

The Placement Gate verifies the static contract from accepted Facts placement
requirements to valid placement patches: required universe/profile, localized
intent, host coordinates, assembly scope, and core instance.  It does not
claim final root reachability in assembled OpenMC geometry.  The existing
post-assembly `localized_insert_placement_report.json` remains a diagnostic;
a future Final Plan Gate owns root-reachability closure.

## Reused kernel and state

Phase 2 uses the Phase 1B `PlanBuildState` ledger, stage state machine,
budgets, fingerprints, artifact writer, and typed human protocol.  The
provider-tolerant structured I/O is extracted into `review_io.py`, then the
Facts adapter uses the same bounded call/retry path.  Placement adds no second
top-level state store: its findings, decisions, candidate hashes, attempts and
human records remain in the existing plan-loop namespaces.

## Unified placement view

`PlacementBindingView` projects a single `PinMapPatch` into a stable
`single_assembly` scope and a catalog/core layout into `multi_assembly`
scopes.  The internal single scope is never persisted as an artificial
AssemblyCatalogPatch.  `PlacementContractMatrix` is Python-built and performs
counts, host subsets, profile references, universe existence, anchors and
control-state equality deterministically; the critic cannot recalculate or
override them.

## Ownership and repair boundary

Python maps deterministic issue codes to owner patches and issue-scoped JSON
paths.  Facts and Universes are always protected.  A missing universe becomes
a recorded dependency retry request and is blocked in controlled mode; this
phase deliberately does not execute generic dependency retry.  A placement
candidate is applied only to a deep clone, schema/validator/preflight checked,
independently re-reviewed, then atomically committed.  Superseded envelopes
are retained and stale dependents are invalidated conservatively.

## Gate modes

`off` builds no Placement evidence and calls no critic.  `advisory` reviews
only after inputs are ready and never mutates patches.  `controlled` requires
an accepted Facts stage, reorders only its own task sequence to establish the
placement-before-axial barrier, and blocks on unusable review, unresolved
dependency, or failed candidate.  Placement human questions are limited to
true semantic ambiguity and reuse the generic graph interrupt/resume route.
