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

## P0-NEW-1: Controlled material composition policy

Pure-element approximations of structural alloys (Zircaloy-4 -> pure Zr, SS-304 -> pure Fe, Inconel-718 -> pure Ni) lose real absorption from minor constituents (Sn, Cr, Ni, Nb, Mo, ...) and bias keff high. This makes "it runs" indistinguishable from "it runs accurately" and weakens keff comparisons.

The controlled alloy composition library (`openmc_agent/material_library.py`) restores nominal engineering compositions for those three alloys. A material composition policy (`openmc_agent/material_policy.py`) decides when to apply them:

- `preserve_plan` — keep the plan's composition exactly as-is (even pure Zr / Fe / Ni).
- `apply_alloy_library` (default) — replace *only* when the material id/name canonicalizes to a known alloy AND its current composition is that alloy's pure base element. Fuel, water, helium, pyrex, and unknown alloys are always preserved.
- `strict_confirmed_only` — only substitute when the patch explicitly marks `composition_status = needs_library` or `approximate`.

Every substitution is recorded in a `material_composition_report.json` written under `incremental/` and surfaced as an `materials.alloy_library_applied` info issue. The compositions are nominal handbook midpoints, NOT VERA benchmark specs; they are intentionally replaceable via `register_alloy_composition()`.

### Dry-run comparison (no OpenMC required)

```bash
python scripts/compare_material_policies.py \
  --benchmark VERA3 \
  --variant 3A \
  --input Input/VERA3_problem.md \
  --model fake \
  --reference-patch-policy reference_only_for_structural \
  --dry-run \
  --out data/evals/material_policy/VERA3_3A_dry
```

This writes a `comparison_report.json` describing what *would* run, without calling any LLM or OpenMC. Use it in base Python environments and CI.

### Real OpenMC smoke comparison (inside `openmc-env`)

```bash
python scripts/compare_material_policies.py \
  --benchmark VERA3 \
  --variant 3A \
  --input Input/VERA3_problem.md \
  --model deepseek:deepseek-chat \
  --reference-patch-policy reference_only_for_structural \
  --batches 5 --inactive 1 --particles 1000 \
  --allow-real-llm \
  --out data/evals/material_policy/VERA3_3A_alloy
```

The report records `preserve_plan` keff, `apply_alloy_library` keff, and `delta_pcm` (alloy - preserve). Smoke-level runs (5 batches, 1000 particles) are **not** benchmark agreement; they only establish a controlled baseline for future higher-fidelity comparisons.
