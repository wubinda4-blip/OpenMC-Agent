# Benchmark and Ablation Strategy

## Role

The benchmark runner is a thin, offline, reproducible harness for measuring how
much each OpenMC Agent capability (grep, Graph, RAG, auto-repair, ask-expert)
contributes to diagnosis, retrieval, repair, and final results. It does not
call real LLMs, does not run OpenMC simulations, and does not hardcode the
production workflow. Instead it accepts an injectable `case_runner` callable
that turns one `EvaluationCase` + one `AblationConfig` into a `WorkflowTrace`.

The runner lives in `openmc_agent/benchmark_runner.py`. It is purely additive:
importing it changes nothing about the main workflow.

## Why Trace Is the Source of Truth

Every case is scored from a `WorkflowTrace`, not from unstructured logs. Trace
records structured events (`validation_completed`, `retrieval_started`,
`ask_expert_started`, `workflow_completed`, …) with issue codes, route hints,
renderability, and compact metadata. This makes evaluation:

- deterministic and replayable,
- independent of where the trace came from (real run, fake runner, or fixture),
- easy to persist as JSON / JSONL for later analysis.

`evaluate_trace_against_case(trace, case)` compares the observed trace against
the case's expected contract (issue codes, renderability, retrieval trigger,
human confirmation). `aggregate_evaluation_results` rolls many results into
`EvaluationMetrics`.

## Data Model Relationships

- `EvaluationCase` — one input case: what the workflow should produce
  (expected issue codes, renderability, retrieval / human-confirmation flags).
- `BenchmarkConfig` — run-level settings: `run_id`, `name`, `output_dir`,
  artifact toggles (`save_traces`, `save_jsonl`, `save_markdown`), and optional
  `categories` / `max_cases` filters.
- `AblationConfig` — which capabilities are enabled for one arm
  (`enable_grep`, `enable_graph`, `enable_rag`, `enable_auto_repair`,
  `enable_reflect_plan`, `enable_ask_expert`).
- `BenchmarkCaseResult` — one case's trace + evaluation + warnings.
- `BenchmarkRunResult` — all case results for one ablation arm + metrics.
- `AblationStudyResult` — many arms + a comparison table.

Flow: `cases` + `case_runner` + `AblationConfig` + `BenchmarkConfig`
→ `run_benchmark` → `BenchmarkRunResult`; many ablations
→ `run_ablation_study` → `AblationStudyResult` with a comparison dict.

## Default Ablations

`DEFAULT_ABLATIONS` ships six arms:

| ablation | grep | graph | rag | auto-repair | reflect | ask-expert |
| --- | --- | --- | --- | --- | --- | --- |
| `full_stack` | on | on | on | on | on | on |
| `no_grep` | off | on | on | on | on | on |
| `no_graph` | on | off | on | on | on | on |
| `no_rag` | on | on | off | on | on | on |
| `no_retrieval` | off | off | off | on | on | on |
| `no_auto_repair` | on | on | on | off | on | on |

`enable_grep` / `enable_graph` / `enable_rag` map onto `RetrievalPolicy` via
`retrieval_policy_from_ablation(ablation)`. `enable_auto_repair`,
`enable_reflect_plan`, and `enable_ask_expert` are passed to the case runner as
metadata; they are not yet wired into the production workflow and do not change
main workflow behavior in this step.

## Output Formats

When `BenchmarkConfig.output_dir` is set, the runner writes, under
`<output_dir>/<run_id>/<ablation_name>/`:

- `run_result.json` — full `BenchmarkRunResult` (JSON).
- `summary.md` — short markdown summary.
- `cases.jsonl` — one `BenchmarkCaseResult` per line (streaming / batch).
- `traces/<case_id>.json` and `traces/<case_id>.jsonl` — per-case trace dumps.

For an ablation study, each arm writes its own subdirectory, plus a top-level
`<output_dir>/<run_id>/ablation_result.json` and `ablation_summary.md`.

If `output_dir is None`, nothing is written; the runner returns in-memory
results only. Markdown summaries stay short (case counts, rates, failed-case
table, comparison table) and never dump full traces.

## Current Limits

- No real LLM calls and no real OpenMC runtime; experiments need a real
  `case_runner` to be end-to-end meaningful.
- Ablation is primarily a policy layer: retrieval toggles flow into
  `RetrievalPolicy`, while auto-repair / reflect / ask-expert toggles are
  metadata for the case runner rather than switches in the main workflow.
- No persistent trace store; artifacts are written to the filesystem only when
  `output_dir` is set.
- No dashboard.
- No GraphRAG, vector store, or OpenAI file search.
- No hallucination / fact-gap classifier beyond issue / retrieval /
  human-confirmation signals.

## Future Extensions

- Real workflow `case_runner` that drives `build_graph` / `build_plan_graph`
  under each ablation.
- Persistent trace store (queryable across runs).
- Evaluation dashboard.
- GraphRAG comparison arm.
- Vector store and OpenAI file search comparison arms.
- Hallucination and fact-gap prevention metrics.
- HexAssemblyRenderer, depletion, and pebble-bed renderer workflows.
