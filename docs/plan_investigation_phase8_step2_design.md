# Plan Investigation — Phase 8A Step 2 Design

## Status

**Read-only tool foundation ready.** Step 2 ships four deterministic,
audit-friendly investigation tools and their registry. No LLM, no
graph wiring, no tool dispatch surface exposed to any LLM.

Built on Step 1's `PlanningEvidenceLedger` + `SourceIndex`. The next
step (Step 3) wires these tools into an LLM investigation loop.

## What Step 2 adds

| Module | Contents |
| --- | --- |
| `openmc_agent/plan_investigation/tool_models.py` | `InvestigationToolRequest`, `InvestigationToolResult`, `InvestigationToolSpec`, `ToolCapability` / `ToolSideEffect` enums, `STEP2_ENABLED_CAPABILITIES` set. |
| `openmc_agent/plan_investigation/tool_registry.py` | `InvestigationToolRegistry` (register/get/list_tools/validate_arguments/execute), `ToolExecutionContext`, `build_default_step2_registry()`. |
| `openmc_agent/plan_investigation/tools.py` | Four tool spec factories + executors. |
| `openmc_agent/plan_investigation/tool_artifacts.py` | `ToolCallRecord`, `ToolCallLedger`, `record_tool_call()`, `write_tool_call_artifact()`. |

## Tool inventory

| Tool | Capability | Input | Output | Produces evidence? |
| --- | --- | --- | --- | --- |
| `search_source_index` | `source_search` | `{query, source_id?, keywords?, max_results?}` | `{spans: [...], total_hits, truncated, query, keywords}` | Yes — one explicit `source.search_hit` claim per hit. |
| `inspect_requirement_structure` | `structure_inspection` | `{source_id?, keyword_groups?}` | `{scope_indicators: [...], grid_sizes: [...], source_id}` | Yes — one `model.scope_indicator_present` claim per detected indicator; one `model.grid_size_text` per parsed grid pattern. |
| `inspect_patch_schema` | `schema_inspection` | `{patch_type}` | `{patch_type, model_class, required_fields, optional_fields, enum_values, nested_models, allowed_top_level_keys, forbidden_top_level_keys, json_schema_digest}` | No — returns reference data only. |
| `query_evidence_ledger` | `schema_inspection` | `{subject?, predicate?, status?, criticality?}` | `{claims: [...], total, truncated}` | No — read-only query. |

The registry rejects `ToolCapability.REPOSITORY_INSPECTION` at spec
construction time so no Step 2 caller can register a forbidden
capability.

## Execution model

```
caller builds InvestigationToolRequest
        │
        ▼
registry.execute(tool_name, request, context=ToolExecutionContext(...))
        │
        ├─ get spec
        ├─ validate_arguments against spec.input_schema
        ├─ dispatch to executor(ctx, request)
        └─ return InvestigationToolResult
```

`ToolExecutionContext` is the only side-channel tools have. It carries:

* `source_indexes: dict[source_id, SourceIndex]`
* `ledger: PlanningEvidenceLedger`
* `patch_schema_provider` (optional override; defaults to the public
  `plan_builder.patches` introspection used by `inspect_patch_schema`).

Tools never receive a `PlanBuildState`, a `PlanPatchEnvelope`, or any
other graph-level state object.

## Determinism

* `search_source_index` returns spans sorted by
  `(source_id, start_line, end_line, span_id)`.
* `inspect_requirement_structure` walks keyword groups in dict order,
  then phrases in list order, then lines in source order.
* `query_evidence_ledger` returns claims sorted by `claim_id` (the
  ledger's existing canonical order).
* `inspect_patch_schema` returns alphabetically-sorted field lists.
* Every `InvestigationToolResult.execution_hash` is SHA-256 over
  canonical JSON of `(tool_name, ok, result, evidence_claim_ids,
  source_refs)` — independent of timestamps, run ids, output paths.

## Reactor-neutrality

`REQUIREMENT_KEYWORD_GROUPS` and `GRID_PATTERN_RE` are intentionally
generic:

* `full core`, `assembly`, `lattice`, `loading map`, `control rod`,
  `burnable poison`, `fuel enrichment`, `axial`, `spacer grid`,
  `universe`, `material` — phrases any PWR / BWR / VVER / HTGR / SFR /
  CANDU / MOX problem statement might use.
* `N x N` / `N by N` / `N×N` / `N×N` pattern, case-insensitive,
  captures rows × cols.

No reactor-specific names, no fuel names, no fixed lattice orientation.
The tool records **indicator presence**, not **model scope** — the
Facts Gate remains the sole decider of `model_scope`.

## Artifact

`write_tool_call_artifact(output_dir, ledger)` writes
`<output_dir>/workflow/investigation/tool_calls.json`:

```json
{
  "artifact_version": "0.1",
  "record_count": N,
  "records": [
    {
      "tool_name": "search_source_index",
      "arguments_hash": "<sha256>",
      "result_hash": "<execution_hash>",
      "evidence_claim_ids": ["claim_a", "claim_b"],
      "caller_stage": "investigation",
      "ok": true,
      "error_codes": []
    }
  ]
}
```

Sorted by `(tool_name, arguments_hash, result_hash)` for deterministic
diff. Excludes prompts, reasoning, API keys, host paths, full source
text, and full result bodies — only the audit fields above.

## Security boundaries (Step 2 guarantees)

* No LLM client is imported or invoked by any tool.
* No `subprocess`, `os.system`, `eval`, `exec`, `socket`, or network
  client module is imported anywhere in
  `openmc_agent.plan_investigation/`.
* No tool imports `openmc_agent.retrieval`, `openmc_agent.llm`, or
  any patch generator.
* Source documents containing prompt-injection text are treated as
  inert data: the keyword scan operates on lowercased line text; no
  substring is ever executed.
* Tools cannot mutate `PlanBuildState`, `PlanPatchEnvelope`, or any
  existing `EvidenceClaim`. They only ADD new claims via the ledger's
  public `add_claim`.
* Tools cannot fabricate source refs: every ref must come from
  `SourceIndex.make_span` + `register_span`, and is validated by
  `add_claim` against the supplied source indexes.
* `REPOSITORY_INSPECTION` capability is rejected at spec construction
  time; no future PR can register a repository-grep tool without first
  lifting the Step 2 capability gate.

## Why Step 2 does not wire the Graph

Same rationale as Step 1: graph nodes, gate lifecycle, patch generator
and renderer have hard contracts that compound risk when changed in
the same PR as a new tool surface. Step 2 ships the deterministic
substrate; Step 3 can wrap it in an LLM orchestration loop with
confidence that the data layer and tool surface are stable.

## How Step 3 will consume Step 2

Step 3 (LLM tool orchestration) will:

1. Receive a requirement text and build a `SourceIndex` + empty
   `PlanningEvidenceLedger`.
2. Hand the LLM the registry's `list_tools()` output as tool schemas.
3. For each LLM-requested tool call, build an
   `InvestigationToolRequest` and dispatch via
   `registry.execute(...)`.
4. Record every call via `record_tool_call` and write
   `tool_calls.json` at the end.
5. Pass the resulting ledger to the existing Facts generator (which
   remains the sole decider of `model_scope` and other facts).
