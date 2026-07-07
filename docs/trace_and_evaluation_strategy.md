# Trace and Evaluation Strategy

## Role

The trace layer records structured workflow events for OpenMC Agent runs. It is
for debugging, regression tests, future benchmark runs, and ablation studies.
It does not make routing decisions, modify `SimulationPlan`, or promote
retrieval evidence into confirmed physics facts.

## Non-Interference

Trace is a side channel. `TraceRecorder` stores events in memory by default and
does not write files unless a caller explicitly invokes the export helpers.
Workflow code treats trace failures as non-fatal: if recording fails, the main
workflow continues with the same validation, repair, render, and tool behavior.

## Recorded Events

The current event vocabulary covers:

- `plan_generated`
- `validation_completed`
- `capability_assessed`
- `auto_repair_attempted`
- `auto_repair_completed`
- `retrieval_started`
- `retrieval_completed`
- `reflect_plan_started`
- `reflect_plan_completed`
- `ask_expert_started`
- `ask_expert_completed`
- `render_started`
- `render_completed`
- `export_xml_completed`
- `smoke_test_completed`
- `workflow_completed`
- `workflow_failed`

Events carry compact fields: issue codes, route hints, renderability,
supported renderer, a short summary, and JSON-serializable metadata. Prompt,
plan, and evidence previews are bounded by `TraceConfig.max_preview_chars`.

## RetrievalContext in Trace

The retrieval orchestrator remains the owner of grep, graph, GraphRAG, plain
RAG, evidence merge, and evidence ranking. Trace records only a structured
summary of the resulting
`RetrievalContext`:

- issue count
- grep request/result/evidence counts
- graph node/edge counts
- GraphRAG chunk/evidence counts
- GraphRAG query plan intent, planned path count, preferred query count, and
  fact-gap safe mode
- RAG chunk/evidence counts
- merged evidence count
- ranked evidence count
- dropped duplicate / low-score / budget counts
- evidence score min/max/mean
- warnings
- skipped steps

Trace does not dump full evidence text by default.

## Export

`save_trace_json(trace, path)` writes the full `WorkflowTrace` object as
indented JSON.

`save_trace_jsonl(trace, path)` writes one `TraceEvent` JSON object per line for
streaming or batch analysis.

Both helpers are explicit opt-in and create parent directories as needed.

## Evaluation

`EvaluationCase` describes a lightweight expected behavior contract for a
trace. It includes expected issue codes, expected renderability, expected
supported renderer, and optional booleans for retrieval and human-confirmation
behavior.

`EvaluationResult` stores observed issue codes, renderability, supported
renderer, whether retrieval triggered, whether human confirmation was required,
small metrics, and failure reasons.

`EvaluationMetrics` aggregates a list of results into:

- pass rate
- issue-code precision
- issue-code recall
- retrieval trigger rate
- human confirmation rate

Existing smoke-test evaluation remains compatible with the same module.

## Benchmark and Ablation Runner

The trace-evaluation primitives above are now wrapped by an offline benchmark
runner (`openmc_agent/benchmark_runner.py`, see
`docs/benchmark_and_ablation_strategy.md`). It accepts an injectable
`case_runner`, runs ablation arms as `AblationConfig` policy, and scores every
case via `evaluate_trace_against_case` + `aggregate_evaluation_results` — so
trace remains the single source of truth for evaluation.

## Current Limits

- No persistent trace store.
- No dashboard.
- Benchmark runner is offline only: ablation is a policy layer and needs a real
  `case_runner` for end-to-end experiments.
- No vector store or OpenAI file search.
- No hallucination classifier beyond issue/retrieval/human-confirmation signals.
- Trace does not confirm nuclear data paths, material densities, compositions,
  or benchmark constants.

## Future Extensions

- Persistent trace store.
- Real workflow `case_runner` driving `build_graph` / `build_plan_graph` under
  each ablation.
- Evaluation dashboard.
- GraphRAG evaluation with query-planner and evidence-ranker ablations.
- Hallucination and fact-gap prevention metrics.
- Vector store and OpenAI file search comparisons.
- HexAssemblyRenderer.
- Depletion and pebble-bed renderer workflows.
