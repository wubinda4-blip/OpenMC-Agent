# Plan Investigation — Phase 8A Step 3 Design

## Status

**Controlled LLM investigation agent ready.** Step 3 wires Step 2's
read-only tools into an LLM-driven investigation loop. The feature is
opt-in via `PlanInvestigationConfig.enabled` (default OFF); the legacy
patch-generation path is byte-identical when the flag is off.

## What Step 3 adds

| Module | Contents |
| --- | --- |
| `openmc_agent/plan_investigation/agent.py` | `InvestigationAgent`, `InvestigationContext`, `InvestigationBudget`, `InvestigationResult`, strict JSON `InvestigationPlan` parser. |
| `openmc_agent/plan_investigation/policy.py` | `InvestigationPolicyRegistry` + default suggestions for `facts` / `materials` / `universes` / `axial_layers` / `axial_overlays`. |
| `openmc_agent/plan_investigation/prompt.py` | `build_investigation_prompt`, `render_investigation_evidence_for_prompt`. |
| `openmc_agent/plan_investigation/session_artifacts.py` | `write_investigation_session_artifact`. |
| `openmc_agent/plan_investigation/runner.py` | `PlanInvestigationConfig`, `get/set_investigation_config`, `run_investigation_stage`. |
| `openmc_agent/plan_builder/patch_generator.py` | `PatchGenerationContext.investigation_evidence` field (default empty). |
| `openmc_agent/plan_builder/patch_prompts.py` | `_investigation_evidence_block` renderer. |

## Data flow

```
requirement text
        │
        ▼  (only if config.enabled)
run_investigation_stage()
        │
        ├─ build_investigation_source_index()
        ├─ build_investigation_ledger()
        ├─ InvestigationAgent.plan()  ──→ LLM (strict JSON actions)
        ├─ InvestigationAgent.run()  ──→  Step 2 tools ──→ EvidenceClaim
        └─ return InvestigationResult
                    │
                    ▼
       collect_evidence_for_patch_prompt(ledger, result.evidence_claim_ids)
                    │
                    ▼
       PatchGenerationContext(investigation_evidence=payloads)
                    │
                    ▼
       build_patch_prompt()  ──→  prepends "Evidence Claims" section
                    │
                    ▼
       generate_patch() (unchanged)
```

## Output contract

The LLM must return strict JSON:

```json
{"actions": [{"tool": "<name>", "arguments": {<schema>}}], "summary": "..."}
```

* Empty `actions` list is valid (LLM decides no investigation needed).
* Any extra top-level key, non-string `summary`, non-list `actions`, or
  non-object argument entry → blocked with
  `planning.investigation_invalid_llm_output`.
* Unknown tool name → blocked with `planning.investigation_unknown_tool`.
* Invalid arguments → blocked with `planning.investigation_argument_invalid`.

## Budget

`InvestigationBudget` defaults: `max_tool_calls=5`,
`max_results_per_tool=50`, `max_evidence_claims=100`.

* Budget check runs BEFORE each tool dispatch (so a blocked-on-validation
  action does not consume budget).
* Tool failures (exceptions raised by executors) are non-blocking
  warnings; budget violations are blocking.

## Patch-type policy

`default_policy_registry()` seeds suggestions for five patch types.
Suggestions are advisory prose rendered into the LLM prompt
(`recommended tools`, `useful search queries`, short note). The LLM is
free to deviate.

Reactor-neutral terms only: `full core`, `assembly`, `lattice`,
`loading`, `enrichment`, `material`, `density`, `composition`, `boron`,
`stainless`, `fuel pin`, `guide tube`, `RCCA`, `Pyrex`, `universe`,
`spacer grid`, `axial`, `control rod`, `insertion`. No reactor-specific
names.

## Evidence injection

`render_investigation_evidence_for_prompt(payloads)` returns a section:

```
Evidence Claims (use as constraints, NOT as free text)

- [claim_xxx] model.scope_indicator_present = "full_core"  (explicit/supporting)
    sources: src_yyy:span_zzz

Treat the claims above as constraints.  Do NOT copy their prose into
the patch.  Do NOT invent values that contradict them.
```

The section is prepended to the patch prompt ONLY when
`PatchGenerationContext.investigation_evidence` is non-empty. Empty list
(the default) means legacy prompt is byte-identical.

## Feature flag

`PlanInvestigationConfig.enabled` lives in
`PlanBuildState.metadata["plan_investigation_config"]`. Default is
`enabled=False`. Helpers:

* `get_investigation_config(state)` — read; falls back to default on
  malformed metadata so the legacy path never breaks.
* `set_investigation_config(state, config)` — write.

`run_investigation_stage(...)` returns `None` immediately when disabled
— no LLM call, no tool call, no artifact, no patch-prompt change.

## Session artifact

`write_investigation_session_artifact(output_dir, result)` writes
`workflow/investigation/investigation_session.json`:

```json
{
  "artifact_version": "0.1",
  "session": {
    "session_id": "inv_...",
    "patch_type": "facts",
    "caller_stage": "investigation",
    "tool_calls": [{tool_name, arguments_hash, result_hash, evidence_claim_ids, ok, error_codes}],
    "evidence_claim_ids": ["claim_a", "claim_b"],
    "budget": {...},
    "budget_used": {...},
    "completed": true,
    "blocked": false,
    "block_code": null,
    "warnings": [],
    "result_hash": "<sha256>"
  }
}
```

Excludes prompts, `reasoning_content`, API keys, host paths, full
source bodies. Atomic write (tmp + replace).

## Security boundaries (Step 3 guarantees)

* LLM never calls tools directly: Python parses strict JSON and
  dispatches.
* Unknown tool names (e.g. `shell_exec`, `repo_grep`, `subprocess`)
  are blocked.
* `REPOSITORY_INSPECTION` capability remains disabled at the spec
  level (Step 2 gate).
* The agent never receives a `PlanBuildState`; it sees only
  `InvestigationContext` (ledger + source_indexes).
* The agent never builds a patch; `InvestigationResult` carries no
  patch / envelope field.
* Investigation evidence reaches the patch prompt only via the
  structured `investigation_evidence` channel — the LLM cannot
  smuggle free text into the prompt.

## Fake-LLM canary

`tests/test_plan_investigation_integration.py` ships three fake-LLM
canaries:

* **Facts**: discovers `full core`, `3x3`, `assembly` via
  `inspect_requirement_structure` + `search_source_index`.
* **Materials**: discovers `density` via `search_source_index`.
* **Universes**: discovers `RCCA` via `search_source_index`.

Patch generation itself is unchanged; the canaries verify evidence
flows from requirement → tools → ledger → patch prompt without
mutating the patch body.

## Step 4 hooks (Real LLM controlled investigation)

The next step can:

1. Wire `run_investigation_stage()` into the existing executor right
   before each `generate_patch()` call.
2. Pass real LLM clients (deepseek / sensenova) instead of fakes.
3. Reuse the session artifact for resume / debugging.
4. Lift budget via `PlanInvestigationConfig` per patch type if the
   default 5 calls turn out to be too tight.

Step 4 will NOT need to change Step 3's contract: the LLM output
schema, the budget rules, and the evidence injection format are stable.
