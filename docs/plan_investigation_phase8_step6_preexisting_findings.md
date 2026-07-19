# Phase 8A Step 6 — Pre-existing Findings (verified at HEAD `77634e5`)

All seven P0 findings are CONFIRMED against the live source tree.

## P0-1 — Investigation only runs before Facts patch (CONFIRMED)

`openmc_agent/plan_builder/executor.py:2959` gates the investigation
on `patch_type == "facts"`.  No equivalent call exists for
`materials` or `universes`.

For Materials/Universes the executor only injects the
`_inventory_evidence_payloads_for_patch_type(...)` payloads (line 753
via `build_generation_context_from_state`) — this is *prompt
injection*, not a real investigation.  There is no:

- mandatory baseline run,
- LLM supplemental action plan,
- typed evidence synthesis,
- shared Ledger update,
- session artifact.

## P0-2 — Inventory compile exception is non-blocking (CONFIRMED)

`_maybe_compile_geometry_inventory` at `executor.py:1026-1031`:

```python
except Exception as exc:
    state.add_event(
        "planning.geometry_inventory_blocked",
        f"geometry inventory compilation failed: ...",
        {"error": str(exc)[:200]},
    )
```

It only logs an event and returns.  In `controlled` mode the run
then continues to generate Materials/Universes via the legacy prompt
path (no inventory attached), violating fail-closed semantics.

## P0-3 — Inventory preflight exception returns `[]` (CONFIRMED)

`_maybe_run_inventory_preflight` at `executor.py:1171-1177`:

```python
except Exception as exc:
    state.add_event(...)
    return []
```

The Material-Universe Gate consumes this list as deterministic
findings; an empty list is treated as "no deterministic finding"
→ the gate may accept even when the preflight crashed.

The owner-mapping at lines 1163-1168 is also string-contains based:

```python
"universes" if "radial_profile" in finding.code or "universe" in finding.code
else "materials" if "material" in finding.code
else "materials"
```

Default owner is `materials`, violating the explicit owner map
requirement.

## P0-4 — Inventory payloads misrepresent EvidenceClaims (CONFIRMED)

`_inventory_evidence_payloads_for_patch_type` at
`executor.py:1051-1084`:

```python
payloads.append({
    "claim_id": req.get("requirement_id", ""),   # ← requirement_id, not claim_id
    ...
    "status": "explicit",                         # ← claims to be explicit
    "source_spans": [],                           # ← empty spans
})
```

The `claim_id` is actually a `requirement_id`, the `status` claims
`explicit` while `source_spans` is empty.  These payloads are
indistinguishable from real source-backed EvidenceClaims in the
prompt renderer.

## P0-5 — Baseline resolver always gets `accepted_facts=None, inventory=None` (CONFIRMED)

`openmc_agent/plan_investigation/agent.py:546-550`:

```python
return baseline_policy_for_patch_type(
    context.patch_type,
    accepted_facts=None,
    inventory=None,
)
```

`InvestigationContext` (agent.py:120-139) has NO `accepted_facts`,
`geometry_inventory`, `material_requirement_set`,
`universe_requirement_set`, `gate_findings`, or `research_request`
fields.  The Materials/Universes dynamic baseline is dead code —
even though `materials_baseline_policy(accepted_facts=...,
inventory=...)` (baseline.py:190) reads both, they are never
populated.

## P0-6 — `--stop-after-gate` is not cumulative (CONFIRMED)

`real_campaign_harness.py:1574-1577`:

```python
policy = make_five_gate_controlled_policy(
    enabled_gate_ids=tuple((campaign.stop_after_gate,))
    if getattr(campaign, "stop_after_gate", None)
    else None,
)
```

Only the named gate is enabled.  `make_five_gate_controlled_policy`
(real_campaign_harness.py:335-339) treats `enabled_gate_ids` as an
exact set, not a cumulative prefix.  So `--stop-after-gate
material_universe` runs WITHOUT the Facts Gate, breaking the
"Material-Universe requires accepted Facts" invariant.

## P0-7 — Recursive resume drops configuration (CONFIRMED)

The placement retry path at `executor.py:2647-2656` calls
`run_incremental_planning(...)` but forwards only:

- `requirement, state, llm_client, max_patch_attempts, strict`
- `task_order=None, reference_patch_policy, reference_path,
  few_shot_case_ids`
- `material_policy, plan_loop_policy, plan_loop_output_dir`
- `plan_reviewer_client, plan_repair_client`

Missing kwargs (lost on resume):

- `universes_generation_mode`
- `universe_fragment_max_tokens`
- `large_patch_safe_output_ratio`
- `strict_structured_patch_output`
- `plan_investigation_config`
- `plan_investigation_client`
- `plan_investigation_registry`
- `plan_investigation_policy_registry`
- `plan_investigation_output_dir`

Any downstream resume therefore drops back to `off` investigation
mode and monolithic universes — silently losing the controlled
contract.

## Summary

| ID | Confirmed | File:line | Severity |
|---|---|---|---|
| P0-1 | yes | executor.py:2959 | blocks Step 6A MU closure |
| P0-2 | yes | executor.py:1026-1031 | blocks fail-closed |
| P0-3 | yes | executor.py:1171-1177 | blocks fail-closed |
| P0-4 | yes | executor.py:1051-1084 | misrepresents provenance |
| P0-5 | yes | agent.py:546-550 | dead Materials/Universe baseline |
| P0-6 | yes | real_campaign_harness.py:1575 | breaks Facts→MU ordering |
| P0-7 | yes | executor.py:2647-2656 | breaks controlled resume |

All seven are addressed in Step 6A.
