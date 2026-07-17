# Plan Closed-Loop Phase 4 Design — Material–Universe Review Gate

## Purpose

Phase 4 adds the first cross-patch Review Gate between Facts and Placement:
the **Material–Universe Gate** (`PlanGateId.MATERIAL_UNIVERSE`).  It validates
the static reachability edge:

```
accepted Facts source contract
  → MaterialsPatch
  → UniversesPatch
  → static material bindings (cell-level)
  → fuel-variant identity
```

This is **not** OpenMC root reachability and **not** Placement (which owns
universe→assembly→core binding).  It only proves that the materials required
by the source are expressed, executable, and correctly referenced by universe
cells, and that each required fuel variant keeps an independent identity.

## 1. Existing Materials validator (single-patch, local)

`validators.py` emits:
- `patch.materials.duplicate_material_id`
- `patch.materials.density_invalid`
- `patch.materials.fissile_enrichment_out_of_range`
- compound/transport composition guards
- fuel-enrichment / source-variant presence checks

These remain the **first** line of defence; Phase 4 preflight reuses
`validate_patch(MaterialsPatch)` and never duplicates these algorithms.

## 2. Existing Universes validator (single-patch, local)

`validators.py` emits:
- `patch.universes.empty_universe`
- `patch.universes.duplicate_universe_id`
- `patch.pin_map.default_universe_missing`
- cell radius ordering (via `radial_profile_validation.py`)

Phase 4 reuses `validate_patch(UniversesPatch)` and `radial_profile_validation`
for local checks.

## 3. Existing material species resolver

`material_species.py` resolves compound formulae → transport species,
enforcing fraction conservation and fissile isotope policy.  Phase 4 pulls
the resolver report into the EvidencePack as deterministic evidence; the
Critic never recomputes stoichiometry.

## 4. Existing fuel-variant checks

`retry_acceptance.py` has `_check_fuel_variant_identity` and
`_check_fuel_variant_reachability`.  Phase 4 reuses the same logic at the
binding-view level so a single source of truth covers both retry acceptance
and gate preflight.

## 5. Existing material readiness

`material_execution_readiness.py` checks density availability for
mass-derived grid geometry.  That check depends on `axial_overlays` and is
therefore Placement/Axial-owned.  Phase 4 does **not** duplicate readiness;
it only verifies density is present (non-None, positive) for materials
declared with `density_status != "needs_confirmation"`.

## 6. Existing retry acceptance (Phase 3B)

`retry_acceptance.py` already implements:
- `materials_schema`, `density_policy`, `fuel_variant_identity`,
  `material_readiness`
- `universes_schema`, `material_references`, `required_universe_ids`,
  `fuel_variant_reachability`, `profile_references`

Phase 4 preflight calls the **same** functions where applicable.  No second
implementation is created.

## 7. Local vs cross-patch vs Placement vs Final

| Check category | Owner | Phase 4 covers? |
| --- | --- | --- |
| MaterialsPatch schema/density/duplicate | local validator | reused, not duplicated |
| UniversesPatch schema/duplicate/empty | local validator | reused, not duplicated |
| Material species resolution | resolver | reused as evidence |
| Radial profile local (gap/overlap/order) | local validator | reused |
| **Material → Universe cell reference** | **cross-patch** | **YES (Phase 4)** |
| **Cell role ↔ material role compatibility** | **cross-patch** | **YES (Phase 4)** |
| **Fuel variant → material → active-fuel universe** | **cross-patch** | **YES (Phase 4)** |
| **Fuel variant collapse / identity loss** | **cross-patch** | **YES (Phase 4)** |
| **Required material from source not expressed** | **cross-patch** | **YES (Phase 4)** |
| Universe → pin_map / assembly_catalog binding | Placement Gate | NO |
| Universe → axial segment binding | Axial Gate | NO |
| Assembled plan / root reachability | Final Gate | NO |

## 8. Gate ordering and barrier

Canonical order is unchanged: `FACTS → MATERIAL_UNIVERSE → PLACEMENT →
AXIAL_GEOMETRY → ASSEMBLED_PLAN`.

Controlled barrier: downstream patches that consume Materials or Universes
(`localized_insert_profiles`, `base_path_axial_profiles`, `pin_map`,
`assembly_catalog`, `axial_overlays`, `core_layout`) must not be generated
until the Material–Universe Gate is `accepted`.

**Choice for `axial_layers`**: Option B — generate **after** the
Material–Universe Gate.  Although `axial_layers` only depends on Facts in the
dependency graph, its fill references consume universes; generating it before
the gate is accepted would risk consuming unreviewed universes.  The
canonical dependency graph already lists `axial_overlays` as depending on
`axial_layers` and `materials`, so `axial_layers` is generated after the gate.

## 9. Mode semantics

- **off**: no EvidencePack, no Critic, no extra preflight; existing hashes
  and call counts unchanged.
- **advisory**: run preflight and Critic; record `reviewed`/
  `review_failed`; do not modify patches; `workflow_behavior_changed=false`.
- **controlled**: requires accepted Facts; blocking issues are routed through
  Phase-3B retry; the gate is replayed after owner commit; only `accepted`
  unblocks downstream.

## 10. Files added

| File | Responsibility |
| --- | --- |
| `material_universe_binding.py` | `MaterialUniverseBindingView`, material/universe/cell/variant records |
| `material_universe_evidence.py` | `MaterialUniverseEvidencePack`, contract matrix, applicable/ready/input_hash |
| `material_universe_preflight.py` | `run_material_universe_preflight()` deterministic issues |
| `material_universe_review_prompts.py` | Critic prompt builder |
| `material_universe_reviewer.py` | Critic invocation + normalization |
| `material_universe_issue_policy.py` | Python owner/action registry |
| `material_universe_human.py` | Human question/answer (reuses generic plan/retry human route) |

Models (`MaterialUniverseReviewFindingDraft`, `MaterialUniverseReviewModelOutput`,
`MaterialUniverseContractRow`, `MaterialUniverseContractMatrix`,
`MaterialUniverseEvidencePack`) are added to `models.py`.

## 11. Phase-3B integration

The gate constructs typed retry requests via a new builder
`build_retry_request_from_material_universe_finding(...)` and dispatches them
through `execute_plan_retry_loop`.  No parallel repair agent is created.

## 12. Non-goals

- Axial Geometry Review Gate
- Final/Assembled Plan Review Gate
- OpenMC root reachability
- Runtime repair
- LLM Supervisor
- Monolithic fallback
- Auto-mutating Facts without dependency retry
- Benchmark-specific production rules
