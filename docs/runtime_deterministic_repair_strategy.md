# Runtime Deterministic Repair Strategy (P1-RUNTIME-R2/R3)

> **Stage**: One-shot deterministic runtime recovery. No LLM plan editing.

## 1. RuntimeFailure "owner" vs real patch type

A `RuntimeFailure.owner_patch_types` from R0/R1 uses coarse-grained concepts
(`cells`, `regions`, `surfaces`, `settings.source`) for diagnosis. But the
incremental executor only knows these real patch types:

| Real patch type | Editable scientific content? |
|-----------------|------------------------------|
| `facts`         | benchmark ID, axial domain, active fuel region — **protected** |
| `materials`     | composition, density, enrichment — **protected** |
| `universes`     | cell layers, radii, material refs — **partially protected** |
| `pin_map`       | coordinate positions — **protected** |
| `axial_layers`  | z bounds, fill refs, loading refs — **partially protected** |
| `axial_overlays`| grid mass, frame dimensions — **protected** |
| `settings`      | source strategy, fissionable constraint — **safe to modify** |

The repair policy maps runtime issue codes to **real** candidate patch types
and defines which paths within those patches are safe to modify.

## 2. What can be deterministically repaired?

| Issue family | Auto-repair? | Target patch | Allowed paths |
|-------------|-------------|-------------|---------------|
| Source rejection / source extent | **Yes** | `settings` | `/source_strategy`, `/source_requires_fissionable_constraint` |
| Geometry overlap / lost particle | **Diagnose only** | `universes` / `axial_layers` / `axial_overlays` | Requires unique proof (R3-B) |
| Missing nuclide data | **No** (name norm only) | `materials` | `/composition/*/name` (GND format only) |
| Cross-section environment | **No** | — | blocked_environment |
| Timeout / crash / unknown | **No** | — | transient / unknown |

## 3. Root-cause precedence (unified)

1. **cross-section missing/invalid** → `environment`, `environment_only=true`.
   Blocks ALL plan repair, even if other errors co-occur.
2. **source rejection** → primary root cause over downstream segfault/MPI-abort.
3. **geometry overlap/lost particle** → `plan_fixable` candidate, but requires
   deterministic object localization before any modification.
4. **missing nuclide data** → `human_fact` unless provably a name normalization.
5. **timeout/crash/unknown** → no auto-repair unless a higher-confidence
   primary issue exists.

## 4. Clone-only acceptance

Every repair proposal is evaluated on a deep clone:
1. Clone `PlanBuildState`
2. Clone target patch envelope
3. Apply operations within `allowed_paths`, reject `forbidden_paths`
4. `parse_patch_content` + `validate_patch`
5. Deterministic assemble
6. `validate_simulation_plan` + capability assessment
7. Render to isolated candidate directory
8. Export XML
9. Run stage-specific check (geometry debug / source preflight)
10. Reclassify `RuntimeFailure`
11. Accept only if primary issue resolved, no new blockers, plan hash changed,
    and protected facts unchanged.

## 5. One-shot recovery budget

- Each fingerprint: max 1 deterministic repair attempt.
- Entire workflow: max 1 runtime repair (separate from planning `retry_count`).
- Re-execution after repair: if the same fingerprint recurs, stop immediately.
- No monolithic `reflect_plan`, no LLM, no infinite re-runs.

## 6. Scientific fact protection

Regardless of policy, these are forbidden:
- `facts` all fields
- `materials` composition/density/temperature/mixture fractions
- enrichment, pin-map coordinates, expected counts
- confirmed radial dimensions, confirmed axial z bounds
- grid total mass/density/frame dimensions, nozzle/core-plate fractions
- cross-section paths, benchmark IDs

If a repair would change any of these → `blocked_human_fact`.

## 7. What R2/R3 does NOT do

- No LLM runtime diagnostician (R4)
- No multi-round runtime supervisor (R5/R6)
- No automatic nuclide replacement
- No epsilon surface shifts
- No keff-driven geometry changes
- No VERA4, no few-shot, no monolithic fallback
