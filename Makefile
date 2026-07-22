.PHONY: check-env check-env-openmc test-quick test-no-openmc test-openmc test-all \
        benchmark-fake benchmark-workflow-fake benchmark-real benchmark-save-baseline benchmark-check \
        model diff-workflow-reports gate-workflow-regression

# ---------------------------------------------------------------------------
# Configurable defaults (override via command line, e.g. make model INPUT=foo.md)
# ---------------------------------------------------------------------------
PYTHON   ?= conda run --no-capture-output -n openmc-env python
MODEL    ?= deepseek:deepseek-chat
CASES    ?= tests/fixtures/evaluation_cases.json
WORKFLOW_BASE_REPORT ?= tests/fixtures/workflow_baseline/evaluation_report.json
WORKFLOW_FAKE_OUT ?= data/evals/workflow/fake_current
WORKFLOW_HEAD_REPORT ?= $(WORKFLOW_FAKE_OUT)/evaluation_report.json
WORKFLOW_DIFF_OUT ?= $(WORKFLOW_FAKE_OUT)/report_diff.md
WORKFLOW_REAL_OUT ?= data/evals/workflow/current
BASE_REPORT ?= $(WORKFLOW_BASE_REPORT)
HEAD_REPORT ?= $(WORKFLOW_HEAD_REPORT)
OUT_DIFF ?= $(WORKFLOW_DIFF_OUT)

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
		--out $(WORKFLOW_FAKE_OUT)

benchmark-workflow-fake: benchmark-fake

benchmark-real:
	$(PYTHON) scripts/run_workflow_benchmark.py \
		--cases $(CASES) \
		--model $(MODEL) \
		--mode plan-only \
		--allow-real-llm \
		--out $(WORKFLOW_REAL_OUT)

# Save the current fake benchmark report as the curated regression baseline.
benchmark-save-baseline:
	@mkdir -p $(dir $(WORKFLOW_BASE_REPORT))
	cp $(WORKFLOW_HEAD_REPORT) $(WORKFLOW_BASE_REPORT)
	@echo "Baseline saved to $(WORKFLOW_BASE_REPORT)"

# Run fake benchmark + diff against baseline + regression gate (one command).
benchmark-check:
	$(PYTHON) scripts/run_workflow_benchmark.py \
		--cases $(CASES) \
		--model fake \
		--mode plan-only \
		--out $(WORKFLOW_FAKE_OUT)
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base $(WORKFLOW_BASE_REPORT) \
		--head $(WORKFLOW_HEAD_REPORT) \
		--out $(WORKFLOW_DIFF_OUT) \
		--fail-on-regression
	@echo "Diff report: $(WORKFLOW_DIFF_OUT)"

# ---------------------------------------------------------------------------
# Report diff (compare any two evaluation_report.json)
# ---------------------------------------------------------------------------
diff-workflow-reports:
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base $(BASE_REPORT) \
		--head $(HEAD_REPORT) \
		--out $(OUT_DIFF)

gate-workflow-regression:
	$(PYTHON) scripts/diff_evaluation_reports.py \
		--base $(BASE_REPORT) \
		--head $(HEAD_REPORT) \
		--fail-on-regression
