.PHONY: check-env check-env-openmc test-quick test-no-openmc test-openmc test-all \
        benchmark-fake benchmark-real benchmark-save-baseline benchmark-check \
        model diff-workflow-reports gate-workflow-regression

# ---------------------------------------------------------------------------
# Configurable defaults (override via command line, e.g. make model INPUT=foo.md)
# ---------------------------------------------------------------------------
PYTHON   ?= conda run --no-capture-output -n openmc-env python
MODEL    ?= deepseek:deepseek-chat
CASES    ?= tests/fixtures/evaluation_cases.json

# Single-model run defaults
INPUT    ?= Input/VERA3_problem.md
VARIANT  ?= 3A
BENCHMARK ?= VERA3
REF_POLICY ?= off
MAT_POLICY ?= apply_alloy_library
OUT      ?= data/runs/$(BENCHMARK)_$(VARIANT)
# Set ALLOW_REAL_LLM=1 on the command line to enable non-fake models
ALLOW_REAL_LLM ?=
# Log level: INFO (default, shows [llm]/[node:] progress), WARNING (quiet), DEBUG
LOG_LEVEL ?= INFO

# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------
check-env:
	$(PYTHON) scripts/check_environment.py

check-env-openmc:
	$(PYTHON) scripts/check_environment.py --require-openmc

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
test-quick:
	$(PYTHON) -m pytest -q tests/test_schemas.py tests/test_evaluation.py tests/test_benchmark_runner.py

test-no-openmc:
	$(PYTHON) -m pytest -q -m "not openmc and not requires_llm"

test-openmc:
	$(PYTHON) -m pytest -q -m openmc

test-all:
	$(PYTHON) -m pytest -q

# ---------------------------------------------------------------------------
# Single-model run (real LLM modeling on one input file)
#
# Usage:
#   make model                                    # defaults: VERA3 3A, deepseek
#   make model INPUT=Input/VERA3_problem.md VARIANT=3B ALLOW_REAL_LLM=1
#   make model INPUT=Input/VERA2_problem.md VARIANT=2A BENCHMARK=VERA2 ALLOW_REAL_LLM=1
#   make model INPUT=Input/VERA3_problem.md MODEL=glm:glm-4-plus ALLOW_REAL_LLM=1
#   make model INPUT=Input/VERA3_problem.md MODEL=fake              # no LLM, quick check
#   make model INPUT=Input/VERA3_problem.md SMOKE=1 ALLOW_REAL_LLM=1  # OpenMC smoke + keff
#   make model INPUT=Input/VERA3_problem.md FULL=1 ALLOW_REAL_LLM=1    # plot + smoke (equivalent to --full)
# ---------------------------------------------------------------------------
model:
	@mkdir -p $(OUT)
	$(PYTHON) scripts/run_model.py \
		--input $(INPUT) \
		--model $(MODEL) \
		--benchmark $(BENCHMARK) \
		--variant $(VARIANT) \
		--reference-patch-policy $(REF_POLICY) \
		--material-policy $(MAT_POLICY) \
		--out $(OUT) \
		--log-level $(LOG_LEVEL) \
		$(if $(ALLOW_REAL_LLM),--allow-real-llm) \
		$(if $(SMOKE),--smoke-test) \
		$(if $(FULL),--full) 2>&1 | tee $(OUT)/cli.log

# Dry-run: resolve requirement + feature detection only (no LLM, no OpenMC)
model-dry:
	$(PYTHON) scripts/run_model.py \
		--input $(INPUT) \
		--benchmark $(BENCHMARK) \
		--variant $(VARIANT) \
		--dry-run

# ---------------------------------------------------------------------------
# Workflow benchmark (evaluation cases manifest — for regression tracking)
# ---------------------------------------------------------------------------
benchmark-fake:
	$(PYTHON) scripts/run_workflow_benchmark.py \
		--cases $(CASES) \
		--model fake \
		--mode plan-only \
		--out data/evals/workflow/fake

benchmark-real:
	$(PYTHON) scripts/run_workflow_benchmark.py \
		--cases $(CASES) \
		--model $(MODEL) \
		--mode plan-only \
		--allow-real-llm \
		--out data/evals/workflow/current

# Save current benchmark result as the regression baseline
benchmark-save-baseline:
	rm -rf data/evals/workflow/baseline
	cp -r data/evals/workflow/current data/evals/workflow/baseline
	@echo "Baseline saved to data/evals/workflow/baseline"

# Run real benchmark + diff against baseline + regression gate (one command)
benchmark-check:
	$(PYTHON) scripts/run_workflow_benchmark.py \
		--cases $(CASES) \
		--model $(MODEL) \
		--mode plan-only \
		--allow-real-llm \
		--out data/evals/workflow/current
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base data/evals/workflow/baseline/evaluation_report.json \
		--head data/evals/workflow/current/evaluation_report.json \
		--out data/evals/workflow/current/report_diff.md \
		--fail-on-regression
	@echo "Diff report: data/evals/workflow/current/report_diff.md"

# ---------------------------------------------------------------------------
# Report diff (compare any two evaluation_report.json)
# ---------------------------------------------------------------------------
diff-workflow-reports:
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base $(BASE_REPORT) \
		--head $(HEAD_REPORT) \
		--out $(OUT_DIFF:-report_diff.md)

gate-workflow-regression:
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base $(BASE_REPORT) \
		--head $(HEAD_REPORT) \
		--fail-on-regression
