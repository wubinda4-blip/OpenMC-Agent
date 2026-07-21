# Phase 8C Step 2E — Facts Gate Closure Design

## Goal

Close a source-backed Facts finding set without treating a schema-valid partial revision as a successful gate. The change remains reactor-neutral and does not infer material constants, axial dimensions, or geometry facts.

## Protocol

- Facts revision uses the shared two-attempt structured-output transaction. Its canonical input hash covers the candidate Facts patch, unresolved findings, evidence excerpts, allowed paths, and confirmed facts. Raw/reasoning text is not persisted in closure telemetry.
- A gate invocation permits at most three closure rounds. Every round performs clone evaluation, duplicate-candidate detection, deterministic consistency, and a full rereview.
- Only a rereview with complete coverage and no error findings commits the candidate. Repeated unresolved sets, duplicate output, invalid structured output, human-only findings, or budget exhaustion block with an explicit failure code.
- The completeness reviewer may report downstream Materials, Placement, Axial, or Universe gaps, but those are warnings with `downstream_impact`; only FactsPatch-owned source-backed omissions are Facts Gate errors.

## Investigation boundary

Mandatory baseline coverage is checked before planner invocation. When it already covers every semantic target, the planner is skipped and the result records `skipped_after_coverage_complete`. Incomplete coverage remains controlled-mode fail-closed after the two-attempt planner transaction.

## Acceptance

Offline acceptance requires multi-round Facts closure, stable payload telemetry, no persisted reasoning, baseline planner early-stop, the non-OpenMC/non-LLM suite, compileall, fake benchmark, and regression diff where a baseline exists. Real LLM acceptance remains pending a local VERA4 canary: Facts must accept, Materials/Universes investigation must not block on recoverable JSON or completed-coverage budget exhaustion, MU Gate must be reached, and truth violations must remain zero.
