# Phase 5 — Axial Geometry Review Gate Design

## Purpose

Promote `PlanGateId.AXIAL_GEOMETRY` from a registered placeholder to a real
executable Review Gate that closes the static loop between accepted upstream
contracts (Facts / Material-Universe / Placement) and the downstream
assembler.

The gate validates that source axial semantics are faithfully represented by
`base_path_axial_profiles`, `axial_layers`, and `axial_overlays` patches
**before** assembly, using deterministic preflight plus an independent LLM
critic whose output is normalised and routed through Phase-3B retry.

## Contract version

`0.6 -> 0.7`.  A legacy `skipped + review_not_implemented` Axial stage is
migrated to `pending`.  No other gate history is reset.

## Existing capability audit

### Reused as-is (no duplication)

| Capability | Location | What the gate reuses |
|---|---|---|
| AxialLayersPatch local validator | `validators._validate_axial_layers` | `validate_patch` for schema / range / fill / overlap / loading-unattached |
| AxialOverlaysPatch local validator | `validators._validate_axial_overlays` | `validate_patch` for overlay schema / mode / mass / volume |
| BasePathAxialProfilesPatch schema | `patches.BasePathAxialProfilesPatch` | structural parse only (no semantic validator exists yet) |
| Axial segmentation | `axial_overlay.compute_axial_segments` | unified finite-segment derivation |
| Overlay z-overlap detection | `axial_overlay.detect_overlay_z_overlaps` | deterministic overlap pairs |
| Overlay renderability | `axial_overlay.overlay_is_structurally_renderable` | structural preflight |
| Derived overlay universe plan | `axial_overlay.derive_overlay_universe_plan` | universe derivation check |
| Assembly3D structural guard | `assembly3d_guard.assembly3d_structural_issues` | through-path / slab / grid diagnostics |
| Material execution readiness | `material_execution_readiness.validate_material_execution_readiness` | overlay density requirements |
| Placement reachability (localized insert) | `placement_reachability.build_localized_insert_placement_report` | insert segment / anchor / layer overlap |
| Phase-2 structured review I/O | `review_io.run_structured_review_call` | JSON schema / object / fenced / retry |
| Phase-3B retry protocol | `retry_controller.normalize_retry_request` + `execute_plan_retry_loop` | typed owner retry |
| Phase-3B owner acceptance | `retry_acceptance` | owner commit gates |
| Controlled gate terminal protection | `graph._incremental_gate_outcome_is_terminal` | generic — already covers AXIAL_GEOMETRY |

### New in Phase 5 (this gate only)

| Module | Responsibility |
|---|---|
| `axial_geometry_binding.py` | `AxialGeometryBindingView` — source contracts, profiles, layers, loadings, overlays, localized-insert axial records, through-path records, derived segments |
| `axial_geometry_evidence.py` | Evidence pack, contract matrix (9 row kinds), applicability / ready / input-hash |
| `axial_geometry_preflight.py` | Deterministic cross-patch preflight reusing validators + axial_overlay + assembly3d_guard |
| `axial_geometry_review_prompts.py` | Critic prompt builder |
| `axial_geometry_reviewer.py` | Critic invocation + strict normalisation |
| `axial_geometry_issue_policy.py` | Python owner registry (Facts / Materials / Universes / Placement / Axial-owned / Task-plan / Human) |

### Boundary: what stays out

- Final / Assembled Plan Gate — not implemented.
- OpenMC root reachability — deferred to runtime preflight.
- OpenMC runtime repair — out of scope.
- Benchmark-specific coordinate hard-coding — forbidden.

## Gate order

```
FACTS -> MATERIAL_UNIVERSE -> PLACEMENT -> AXIAL_GEOMETRY -> ASSEMBLED_PLAN
```

Controlled barrier: AXIAL_GEOMETRY requires Facts + Material-Universe +
Placement accepted, plus valid axial patches.

## Static reachability boundary

The gate validates:

> accepted upstream contracts -> axial patches -> finite z intervals ->
> fill / loading / overlay / profile references -> deterministic segment
> occupancy

The gate does **not** validate assembled root-universe reachability, OpenMC
region correctness, or runtime executability.

## Through-path preservation

Proven deterministically from structured cell / open-region information:

- A base lattice fill preserves the fuel / guide-tube / instrument-tube
  through-path (each pin cell remains reachable).
- An overlay that replaces the entire lattice fill (rather than overlaying a
  band on the lattice) breaks the through-path — flagged as
  `axial.grid_replaced_entire_lattice`.
- An overlay that consumes a protected cell (fuel / clad / absorber) without
  preserving the path — flagged as `axial.overlay_consumes_protected_cell`.

Name-based heuristics ("fuel", "grid", "tube" in IDs) are only low-confidence
warnings, never production-level conclusions.

## Localised-insert axial occupancy

Computed by translating the insert profile segments by the anchor z, then
intersecting with host axial-layer intervals.  The record captures:

- translated absolute extent;
- segment roles and universe IDs;
- overlapping host layer / loading IDs;
- clipping status (reachable / clipped_out / outside_domain).

## Layer gap / overlap policy

- Source requires continuous coverage -> gap = error.
- Source declares void / outer gap -> allowed.
- Unknown gap policy -> defer to Critic / human (never auto-fill).
- Overlay-band overlap on the same lattice is structural (allowed via overlay
  semantics).
- Base-layer overlap without overlay semantics = error.

## Owner / action decisions

All owner / action decisions are Python-side.  The Critic never decides
owner, action, or numerical tolerance.

## Commit plan

1. Add Axial Geometry binding view, segmentation reuse, and contract matrix.
2. Add Axial Geometry evidence critic and owner policy.
3. Integrate controlled barrier, Phase-3B retry, and Gate replay.
4. Add VERA3/VERA4 Phase 5 offline qualification.
