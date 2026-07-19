# Phase 8B Step 1 — Retry Owner Registry and Binding Gap Analysis

## 1. Baseline

**Actual start HEAD**: `c407757f710fd8b99c679930c5e81fee349740e1`

**Tests**: 3188 passed, 2 skipped, 392 deselected in 84.26s  
**Compile**: clean (no errors)  
**Workspace**: clean (no dirty files)

## 2. Gap A: Inventory codes returning None from retry_owner_policy()

**All 14 PREFLIGHT_ISSUE_CODES return None** — the root cause of the retry loop rejection.

| Code | INVENTORY_FINDING_OWNER_MAP | retry_owner_policy() |
|------|---------------------------|---------------------|
| `inventory.hash_mismatch` | `inventory_rebuild` | `None` ❌ |
| `inventory.source_claim_missing` | `plan_investigation` | `None` ❌ |
| `inventory.source_span_invalid` | `plan_investigation` | `None` ❌ |
| `inventory.conflict_unresolved` | `plan_investigation` | `None` ❌ |
| `inventory.component_unresolved` | `plan_investigation_or_human` | `None` ❌ |
| `inventory.material_role_uncovered` | `materials` | `None` ❌ |
| `inventory.fuel_variant_material_uncovered` | `materials` | `None` ❌ |
| `inventory.universe_material_unresolved` | `universes` | `None` ❌ |
| `inventory.radial_profile_uncovered` | `universes` | `None` ❌ |
| `inventory.profile_layer_uncovered` | `universes` | `None` ❌ |
| `inventory.localized_insert_profile_uncovered` | `universes` | `None` ❌ |
| `inventory.unsupported_implicit_component` | `universes` | `None` ❌ |
| `inventory.fabricated_geometry_value` | `universes` | `None` ❌ |
| `manifest.inventory_requirement_missing` | `universes` | `None` ❌ |

**Root cause**: `retry_owner_policy()` in `retry_owner_policy.py:149` has no entries for inventory.* or manifest.* codes. The function returns None at line 205.

## 3. Gap B: MU codes returning None from retry_owner_policy()

**11/37 registered_material_universe_issue_codes() return None** — these exist in `material_universe_issue_policy.py` but are missing from the duplicate code sets in `retry_owner_policy.py`.

### Missing Materials-owned MU codes:
- `material_universe.compound_isotope_policy_missing`
- `material_universe.material_source_variant_unknown`
- `material_universe.material_provenance_missing`
- `material_universe.density_provenance_missing`
- `material_universe.materials_schema_invalid`

### Missing Universes-owned MU codes:
- `material_universe.fuel_cell_missing`
- `material_universe.guide_tube_wall_missing`
- `material_universe.guide_tube_moderator_missing`
- `material_universe.insert_material_missing`
- `material_universe.profile_material_structure_incomplete`
- `material_universe.universes_schema_invalid`

## 4. Root cause: three independent code registries

```
retry_owner_policy.py          — _MATERIAL_CODES (18 codes), _UNIVERSE_CODES (15 codes)
material_universe_issue_policy.py — _MATERIALS_CODES (17 codes), _UNIVERSES_CODES (20 codes)
                                    + variant sub-sets
inventory_preflight.py          — INVENTORY_FINDING_OWNER_MAP (14 codes)
```

These three registries overlap partially but are NOT synchronized. New codes added to one are silently missing from the others.

## 5. retry_request_rejected call path

1. `normalize_retry_request()` in `retry_controller.py:53`
2. Line 72: extracts `codes` from source dict
3. Line 83: `code = next((item for item in codes if _policy_for(item) is not None), None)`
4. `_policy_for(candidate_code)` calls `retry_owner_policy(candidate_code, ...)`
5. `retry_owner_policy()` returns `None` for all inventory.* codes
6. Line 84: `if code is None:` → `state.add_event("planning.retry_request_rejected", ...)`
7. Returns `None` → request never enters retry loop

## 6. Special routes needed

| Code | Current owner | Should route to |
|------|--------------|-----------------|
| `inventory.source_claim_missing` | plan_investigation | RETRIEVE_EVIDENCE |
| `inventory.source_span_invalid` | plan_investigation | RETRIEVE_EVIDENCE |
| `inventory.conflict_unresolved` | plan_investigation | ASK_HUMAN |
| `inventory.component_unresolved` | plan_investigation_or_human | ASK_HUMAN (if engineering choice) |
| `inventory.hash_mismatch` | inventory_rebuild | deterministic rebuild |

## 7. Current VERA4 findings

From qualification report (`data/runs/phase8_step7_vera4_mu_v5`):
- Run blocked at material_universe gate
- No deterministic findings were commit-able because retry_owner_policy returned None
- The gate blocked but couldn't route to a repair

The four VERA4 findings (`material_role_uncovered`, `fuel_variant_material_uncovered`, `radial_profile_uncovered`, `manifest.inventory_requirement_missing`) are **missing explicit binding** — the material/universe objects likely exist in the patches, but the binding metadata (requirement_id, geometry_profile_id, source_requirement_ids) is missing or incomplete.

## 8. Recommended approach

1. **Single adapter** in `retry_owner_policy.py`:
   - Check Facts/Placement/Axial/Assembled first (existing)
   - Delegate to `owner_for_inventory_finding_code()` for inventory.* codes
   - Delegate to `material_universe_issue_owner()` for material_universe.* codes
   - Add `SpecialRetryRoute` typed enum for non-patch routes
   - Keep legacy local sets only for non-overlapping codes

2. **Material Binding Skeleton** — deterministic slot per MaterialRequirement

3. **Universe Binding Skeleton** — deterministic slot per UniverseRequirement

4. **Phase-3B targeted repair** — only touch affected slots, protected fields

## 9. Truthfulness

- `retry_inventory_code_unregistered` — would fire for unregistered codes (now covered)
- `retry_material_universe_code_unregistered` — would fire for missing MU codes (now covered)
- `retry_special_route_misrepresented_as_patch` — prevents plan_investigation from becoming a fake patch
