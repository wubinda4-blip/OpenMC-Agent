# Phase 6 — Final / Assembled Plan Review Gate Design

## Purpose

Promote `PlanGateId.ASSEMBLED_PLAN` from a placeholder to a real executable
Review Gate that closes the static plan loop: after assembly succeeds, the
gate validates the assembled object graph, root reachability, renderer
capability, static source feasibility, and plot/execution-check coherence
**before** any render/export/runtime workflow begins.

## Contract version

`0.7 -> 0.8`.  A legacy `skipped + review_not_implemented` assembled_plan
stage is migrated to `pending`.  No other gate history is reset.

## Gate position

```
FACTS -> MATERIAL_UNIVERSE -> PLACEMENT -> AXIAL_GEOMETRY -> ASSEMBLED_PLAN
```

The gate owns zero patch types (it is a derived gate).  Its input is the
assembled `SimulationPlan` plus all accepted upstream gate hashes.

## Existing capability audit (reused, not duplicated)

| Capability | Location | Reuse |
|---|---|---|
| Deterministic assembly + diagnostics | `assembler.assemble_simulation_plan_from_patches` | Returns `PlanAssemblyResult` with structured issues |
| Plan-level structural validation | `validator.validate_simulation_plan` | Pin counts, lattice loading, mixed-percent, 3D guard |
| Active-graph reachability | `reachability.collect_active_dependencies` | Root reachability partition |
| Renderer capability assessment | `registry.choose_renderer` + `can_render` | `RenderCapabilityReport` with structured issues |
| Renderer registry | `renderers.registry.RENDERERS` | Ordered list of renderers |
| Assembly3D structural guard | `assembly3d_guard.validate_assembly3d_plan` | Through-path / slab diagnostics |
| Gate enum + ordering + invalidation | `policy._GATES`, `dependency_graph` | Already wired |
| Generic terminal-outcome check | `graph._incremental_gate_outcome_is_terminal` | Already generic |
| Phase-3B retry protocol | `retry_controller`, `retry_owner_policy` | Typed owner retry |
| Structured review I/O | `review_io.run_structured_review_call` | JSON schema / object / fenced |

## New in Phase 6

| Module | Responsibility |
|---|---|
| `assembled_plan_binding.py` | `AssembledPlanBindingView` — object graph, root candidates, reachability, renderer matrix, source feasibility |
| `assembled_plan_evidence.py` | Evidence pack, contract matrix, applicability / ready / input-hash |
| `assembled_plan_preflight.py` | Deterministic preflight reusing assembler + validator + renderer |
| `assembled_plan_review_prompts.py` | Critic prompt builder |
| `assembled_plan_reviewer.py` | Critic invocation + strict normalisation |
| `assembled_plan_issue_policy.py` | Python owner registry |

## Static reachability boundary

The gate validates:

> assembled SimulationPlan -> object graph -> selected roots -> deterministic
> graph traversal -> required objects reachable -> renderer capability
> assessed -> static source feasible -> plot/execution-check coherent

The gate does **not** validate OpenMC runtime correctness, lost particles,
source rejection fraction, cross-section availability, or keff convergence.

## Commit plan

1. Add Assembled Plan binding view, object graph, and contract matrix.
2. Add Assembled Plan evidence critic and owner policy.
3. Integrate controlled barrier, Phase-3B retry, and Gate replay.
4. Add VERA3/VERA4 Phase 6 offline qualification.
