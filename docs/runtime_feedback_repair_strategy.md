# OpenMC Runtime Feedback Contract — Repair Strategy

> **Stage**: P1-RUNTIME-R0/R1 — reliable runtime feedback layer.
> No LLM plan repair, no repair loop. This document describes the audit of
> existing capabilities and the new structured runtime feedback contract.

## 1. Audit of Existing Capabilities (HEAD `a73e7e9`)

### 1.1 Tool layer (`openmc_agent/tools.py`)

| Function            | Purpose                                         | Output                         |
|---------------------|-------------------------------------------------|--------------------------------|
| `export_xml`        | Run `model.py` to export XML artifacts          | `ToolResult` + dangling-ref issues |
| `run_geometry_plots`| Run `openmc -p` for plot generation             | `ToolResult`                   |
| `run_smoke_test`    | Render smoke model, run `openmc` transport      | `ToolResult`                   |
| `parse_openmc_output` | Pattern-match stdout/stderr into `ValidationReport` | `ValidationReport` with stable issue codes |

### 1.2 Execution router (`openmc_agent/graph.py::_make_execute_tools_node`)

Current order (pre-R1):

```
export_xml
  └─ if ok and enable_plots → run_geometry_plots
  └─ if ok and runnable and enable_smoke_test → pre-flight source check → run_smoke_test
```

No geometry-debug stage exists. A geometry overlap can only surface during
the smoke transport, where it is mixed with source-rejection crash noise.

### 1.3 Error catalog (`openmc_agent/error_catalog.py`)

Existing runtime codes:
- `runtime.cross_sections_missing` — environment, ask_expert
- `runtime.cross_sections_invalid` — environment, ask_expert
- `runtime.geometry_overlap` — reflect_plan
- `runtime.lost_particle` — reflect_plan
- `runtime.material_missing_nuclide_data` — ask_expert
- `runtime.openmc_source_rejection_failure` — auto_repair (source binding)
- `runtime.openmc_unknown_error` — manual_review
- `runtime.dagmc_or_geometry_load_failed` — manual_review

**Missing**: `runtime.openmc_timeout`, `runtime.openmc_process_crash`.

### 1.4 Supervisor / trace / validation_repair

- `run_supervisor.py`: LLM routing decisions (continue / retry / stop). Does not
  touch tools or plan.
- `run_supervisor_policy.py`: deterministic fallback, veto rules, loop detection.
- `workflow_trace.py`: side-channel trace recorder. Has `export_xml_completed`
  and `smoke_test_completed` events but **no** `geometry_debug_completed`.
- `plan_builder/validation_repair.py`: schema-level repair (materials, axial refs,
  profile universes). No runtime-failure classification.

### 1.5 Root-cause precedence (current)

`parse_openmc_output` already gives source-rejection priority over downstream
segfault/MPI-abort noise, but:
- No structured `RuntimeFailure` dataclass with classification.
- No error fingerprint (identical root cause → identical ID across run dirs).
- No timeout / process-crash classification.
- No geometry-debug stage to catch overlaps before transport.

## 2. R1 Changes

### 2.1 New module: `openmc_agent/runtime_feedback.py`

**`RuntimeFailureClass`**: `plan_fixable | environment | human_fact | transient | unknown`

**`RuntimeFailure`** fields: `failure_id`, `stage`, `tool_name`, `returncode`,
`primary_issue_code`, `secondary_issue_codes`, `normalized_message`,
`raw_error_excerpt`, `error_fingerprint`, `plan_hash`, `artifact_paths`,
`classification`, `owner_patch_types`, `requires_human_confirmation`,
`environment_only`, `metadata`.

**Functions**:
- `classify_runtime_tool_results(tool_results)` → `list[RuntimeFailure]`
- `normalize_runtime_error(text)` → stripped, de-timestamped string
- `compute_runtime_error_fingerprint(normalized_text)` → stable SHA-256 prefix

**Root-cause precedence** (highest first):
1. `runtime.cross_sections_missing/invalid` → `environment`
2. `runtime.openmc_source_rejection_failure` → `plan_fixable` (source)
3. `runtime.geometry_overlap` → `plan_fixable` (geometry, owner = candidate set)
4. `runtime.lost_particle` → `plan_fixable` (geometry, owner = candidate set)
5. `runtime.material_missing_nuclide_data` → `human_fact` or `materials`
6. `runtime.openmc_timeout` → `transient`
7. `runtime.openmc_process_crash` → `transient` (unless source rejection present)
8. `runtime.openmc_unknown_error` → `unknown`

**Fingerprint normalization**: removes timestamps, PIDs, absolute temp paths,
hex addresses, so the same root cause yields the same fingerprint regardless of
run directory.

### 2.2 New tool: `run_geometry_debug`

Runs OpenMC geometry-debug mode (`openmc -g` / `--geometry-debug`) in a
separate subdirectory (`geometry_debug/`) so it never overwrites the smoke
statepoint. Low-cost settings. Timeout is **not** treated as geometry overlap.

### 2.3 Refactored execution order

```
export_xml
  └─ if ok → optional run_geometry_plots
  └─ if ok → run_geometry_debug
       └─ if ok and runnable → run_smoke_test
```

- export failure → stop (no geometry debug, no smoke).
- geometry-debug failure → no smoke.
- each stage writes its own trace event.
- final `ValidationReport` merges all stages but preserves stage source.

### 2.4 New error catalog entries

| Code                          | Severity | Classification   | Route          |
|-------------------------------|----------|------------------|----------------|
| `runtime.openmc_timeout`      | error    | transient        | manual_review  |
| `runtime.openmc_process_crash`| error    | transient*       | manual_review  |

(*unless source rejection or cross-section error is also present.)

### 2.5 New trace event

`geometry_debug_completed` — records geometry-debug stage outcome.

## 3. Next Step (R2/R3)

Targeted deterministic runtime repair: use `RuntimeFailure.classification` +
`owner_patch_types` to drive safe, deterministic plan fixes (e.g. source-box
binding, overlap surface correction) without LLM plan editing.
