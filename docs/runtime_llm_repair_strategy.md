# Runtime LLM Repair Strategy (P1-RUNTIME-R4)

> **Stage**: LLM Runtime Diagnostician + Constrained Runtime Patch Proposer.
> Builds on R2/R3 deterministic repair. Still one-shot, not multi-round Supervisor.

## 1. R2/R3 evaluator gap and R4 upgrade

R2/R3 `evaluate_deterministic_runtime_repair` checks:
- patch parse + validate
- plan assembly + schema validation
- `validate_simulation_plan`
- source preflight (for source issues)

**Missing**: real render to candidate directory, XML export, geometry debug,
and reclassification of the actual runtime failure.

R4 introduces `evaluate_runtime_repair_candidate(...)` — a unified evaluator
used by both deterministic and LLM proposals — that adds steps 11–19:
render to isolated `candidate/` directory → export XML → check dangling refs →
run stage-specific OpenMC check (geometry debug / source preflight / smoke) →
reclassify `RuntimeFailure` → compare before/after.

## 2. RFC6902 engine reuse

R4 reuses the existing infrastructure:
- `apply_json_patch_to_clone` (atomic, all-or-nothing) from `repair_proposal.py`
- `is_protected_path` + `match_json_pointer_pattern` from `repair_policy.py`
- `PatchRepairOperation` model from `validation_repair.py`
- `stable_json_hash` for candidate dedup

The simplified `_apply_operations_to_clone` from R2/R3 is replaced.

## 3. Evidence grading

**Hard evidence** (can authorize physical modifications):
- User input facts, RuntimeFailure, ValidationIssue, rendered_object_map,
  current patch content, current plan, OpenMC logs, deterministic geometry.

**Soft evidence** (can explain semantics, cannot authorize values):
- grep, Graph/GraphRAG, RAG, OpenMC docs, code examples.

Rules: every replacement value must trace to hard evidence. Soft evidence
cannot supply material composition, radii, z-bounds, coordinates, or constants.

## 4. Allowed repair kinds (R4 apply_if_safe)

| Kind | Auto-commit? | Example |
|------|-------------|---------|
| `reference_correction` | Yes | Fix fill_universe_id to existing unique candidate |
| `duplicate_reference_removal` | Yes | Remove exact duplicate loading_id entry |
| `restore_existing_topology_constraint` | Yes | Background cell missing exclusion of existing solid |
| `align_redundant_boundary_to_existing_value` | validate_only unless Python proves donor | Copy authoritative radius from sibling |
| `source_binding_implementation_bug` | No (stop + report) | Source correct but renderer bug |
| `renderer_implementation_bug` | No (stop + report) | |
| `environment_fix_required` | No | |
| `human_fact_required` | No | |
| `no_safe_repair` | No | |

## 5. Absolutely forbidden LLM modifications

- facts patch any field
- material composition/density/temperature/enrichment
- nuclide name replacement
- pin-map coordinates, expected counts
- fuel/cladding/pyrex/control-rod radii
- authoritative axial z boundaries
- grid mass/density/frame thickness
- nozzle/core-plate fractions
- cross-section paths
- source numeric bounds
- execution particles/batches
- any keff fitting
- deleting error structures, replacing solids with coolant
- direct renderer/model.py/XML edits

## 6. Budget

| Resource | Limit |
|----------|-------|
| Diagnosis per fingerprint | 1 |
| Proposal per fingerprint | 1 |
| Committed repairs per workflow | 1 |
| Re-executions after repair | 1 |
| Mutating operations per proposal | 4 |
| Total operations per proposal | 8 |

LLM failure does not auto-retry full diagnosis. One schema-only correction allowed.

## 7. Graph routing

```
execute_tools failure
  → classify_runtime_feedback
    → deterministic repair (if available)
      → accepted → reexecute
      → rejected + LLM enabled → llm_runtime_diagnose
    → plan_fixable + LLM enabled → llm_runtime_diagnose
    → environment/human/transient → save

llm_runtime_diagnose
  → validated + proposal_allowed → llm_runtime_propose
  → no_safe/diagnose_only → save

llm_runtime_propose
  → valid proposal → evaluate_runtime_repair_candidate
  → invalid/empty → save

evaluate_runtime_repair_candidate
  → accepted + apply_if_safe → commit → render → reexecute
  → rejected → save
```

No monolithic reflect_plan. No full-plan regeneration. No infinite loops.
