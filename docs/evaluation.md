# Workflow Evaluation

The P0 workflow benchmark runs the evaluation-case manifest through a lightweight workflow runner and scores the resulting `WorkflowTrace` objects. It is intended for regression tracking across retrieval, planning, incremental patching, capability assessment, renderer readiness, and artifact completeness.

## Default fake benchmark

The default command is safe for base Python environments without OpenMC:

```bash
make benchmark-workflow-fake
```

Equivalent direct invocation:

```bash
python scripts/run_workflow_benchmark.py \
  --cases tests/fixtures/evaluation_cases.json \
  --model fake \
  --mode plan-only \
  --out data/evals/workflow/fake
```

Defaults:

- `model=fake`;
- `mode=plan-only`;
- OpenMC tools disabled;
- rendering disabled;
- real LLM calls refused unless `--allow-real-llm` is set.

## Real LLM opt-in

Use a non-fake model only with an explicit opt-in flag:

```bash
python scripts/run_workflow_benchmark.py \
  --cases tests/fixtures/evaluation_cases.json \
  --model deepseek:deepseek-chat \
  --mode plan-only \
  --allow-real-llm \
  --out data/evals/workflow/deepseek_manual
```

## Output files

Each run writes the following files under the selected output directory:

- `evaluation_report.json`: run metadata, aggregate metrics, and per-case results;
- `benchmark_summary.md`: human-readable summary with pass rate, planning/artifact metrics, issue precision/recall, and a failed-case table;
- `traces/<case_id>.json`: serialized workflow trace for every case, including failures;
- `case_artifacts/<case_id>/case_result.json`: per-case benchmark result summary.

Inspect failed cases using `failed_stage`, `failed_patch_type`, `issue_codes`, and `failure_reasons`. The benchmark is plan-first; OpenMC export, plotting, smoke tests, and transport execution remain opt-in future validation layers.
