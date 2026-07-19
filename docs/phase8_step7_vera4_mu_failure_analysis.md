# Phase 8A Step 7 — VERA4 MU Failure Analysis

Source: `data/runs/phase8_step6a_vera4_mu_v2/runs/run_001/`

## Inventory snapshot (compiled after Facts Gate accepted)

- Inventory hash: `bfd8c42aac532176`
- Radial profiles: 5
- Material role requirements: 6
  - `fuel` / `region1` (status=None)
  - `fuel` / `region2` (status=None)
  - `poison` / `-`
  - `structural` / `-` (×2 — guide-tube wall + instrument-tube wall)
  - `absorber` / `-`
- Universe requirement set: 5 requirements
  - `active_fuel_pin` / `fuel_pin` × 2 (region1, region2)
  - `poison_rod` / `pyrex_rod`
  - `plug_in_guide_tube` / `thimble_plug`
  - `control_rod` / `control_rod`

## Generated patches

### Materials patch (14 materials, all valid)

```
fuel_region1            UO2 Fuel 2.11%
fuel_region2            UO2 Fuel 2.619%
borated_water           Borated Water 1360 ppm
zircaloy4               Zircaloy-4
ss304                   Stainless Steel 304
inconel718              Inconel-718
pyrex                   Borosilicate Glass (Pyrex)
aic                     Silver-Indium-Cadmium Alloy
b4c                     Boron Carbide
helium                  Helium Gas
lower_nozzle            Lower Nozzle (SS304/coolant)
upper_nozzle            Upper Nozzle (SS304/coolant)
lower_core_plate        Lower Core Plate (SS304/coolant)
upper_core_plate        Upper Core Plate (SS304/coolant)
```

### Universes patch (10 universes, fragmented, all valid)

```
fuel_variant_region1            (1 cell)
fuel_variant_region2            (1 cell)
localized_insert_pyrex_E        (1 cell)
localized_insert_thimble_plug_C (1 cell)
localized_insert_thimble_plug_E (1 cell)
localized_insert_rcca_R         (1 cell)
implicit_end_plug_lower         (1 cell)
implicit_end_plug_upper         (1 cell)
```

## The four blocking deterministic findings

### Finding 1: `inventory.material_role_uncovered` (ERROR, owner=materials)

Message: "6 material requirements have no Materials entry"

**Root cause**: Category 4 — Materials Patch generation error.
The Materials patch HAS the right materials (fuel_region1, fuel_region2,
pyrex, aic, b4c, ss304, zircaloy4, etc.) but the patch does not declare
which `material_id` fills which inventory `role`. The preflight checks
role coverage by matching `role` + `fuel_variant_id` against the
materials list and finds zero explicit bindings.

The Materials patch schema does not carry a `material_role_bindings`
field; the preflight cannot infer "fuel_region1 → fuel/region1" without
a binding declaration.

**Action**: REVISE_CURRENT_PATCH with explicit role bindings injected
via `planning_constraints`. RETRIEVE_EVIDENCE alone will not fix this
because the source document is not the gap — the patch structure is.

### Finding 2: `inventory.fuel_variant_material_uncovered` (ERROR, owner=materials)

Message: "fuel variant region2 has no fuel material binding"

**Root cause**: Category 4 — same as Finding 1.
fuel_region2 exists in the Materials patch but no explicit binding
links it to the inventory's `fuel/region2` variant.

**Action**: Same as Finding 1 — REVISE_CURRENT_PATCH.

### Finding 3: `inventory.radial_profile_uncovered` (ERROR, owner=universes)

Message: "5 radial profiles have no universe binding"

**Root cause**: Category 5 — Universes Patch generation error.
The Universes patch has 10 universes but the inventory's 5 radial
profiles (active_fuel_pin × 2, poison_rod, plug_in_guide_tube,
control_rod) do not have explicit `geometry_profile_id` bindings in
the universe cells. The universes exist but their binding to the
inventory's profile IDs is missing.

**Action**: REVISE_CURRENT_PATCH — regenerate Universes fragments with
explicit `geometry_profile_id` declarations matching the inventory's
profile IDs.

### Finding 4: `manifest.inventory_requirement_missing` (ERROR, owner=universes)

Message: "5 resolved inventory universe requirements are not covered by any universe"

**Root cause**: Category 5 — same as Finding 3.
The 5 universe requirements from the inventory each need a matching
universe with the right `geometry_profile_id`. The current Universes
patch has the right universes but the binding is implicit, not
explicit.

**Action**: Same as Finding 3 — REVISE_CURRENT_PATCH.

## Summary classification

| Finding | Category | Root cause | Primary action |
|---|---|---|---|
| material_role_uncovered | 4 | Materials patch missing role bindings | REVISE_CURRENT_PATCH |
| fuel_variant_material_uncovered | 4 | Materials patch missing variant binding | REVISE_CURRENT_PATCH |
| radial_profile_uncovered | 5 | Universes patch missing profile bindings | REVISE_CURRENT_PATCH |
| manifest.inventory_requirement_missing | 5 | Universes patch missing profile bindings | REVISE_CURRENT_PATCH |

**None of the four findings is caused by**:

1. Source document evidence missing — the VERA4 spec has all the info.
2. Missing semantic claims — the inventory compiled correctly.
3. Inventory compiler gaps — the inventory has the right requirements.
4. Preflight false positives — the preflight correctly identifies
   real binding gaps.

**Key architectural insight**: The Step 6A `planning_constraints`
injection IS reaching the patch prompt, but the LLM is not producing
patches with explicit role/profile bindings. The Step 7 RETRIEVE_EVIDENCE
path must be augmented with a proper REVISE_CURRENT_PATCH path that
regenerates the owner patches with stronger constraint enforcement.

## Impact on Step 7 design

1. **Research evidence synthesis** is still needed for cases where the
   source document genuinely lacks info (Category 1). For VERA4 MU it
   will run but likely produce `no_evidence_found` or
   `candidate_spans_found` because the issue is Patch content, not
   source absence.

2. **Owner patch regeneration** (Section 10) is the critical path for
   VERA4 MU. The regeneration must:
   - Use the latest `planning_constraints` (already wired in Step 6A).
   - Inject the SPECIFIC uncovered requirement IDs into the revision
     prompt so the LLM knows exactly which bindings to add.
   - Run clone validation before commit.

3. **Scoped invalidation** must invalidate ONLY Materials (for findings
   1-2) and ONLY Universes (for findings 3-4). It must NOT invalidate
   Facts or downstream patches.

4. **Gate replay** must re-run the deterministic preflight + independent
   reviewer with the new patch content.
