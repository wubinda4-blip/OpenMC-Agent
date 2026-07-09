.PHONY: check-env check-env-openmc test-quick test-no-openmc test-openmc test-all benchmark-workflow-fake benchmark-workflow-real diff-workflow-reports gate-workflow-regression

check-env:
	python scripts/check_environment.py

check-env-openmc:
	python scripts/check_environment.py --require-openmc

test-quick:
	python -m pytest -q tests/test_schemas.py tests/test_evaluation.py tests/test_benchmark_runner.py

test-no-openmc:
	python -m pytest -q -m "not openmc and not requires_llm"

test-openmc:
	python -m pytest -q -m openmc

test-all:
	python -m pytest -q

benchmark-workflow-fake:
	python scripts/run_workflow_benchmark.py \
		--cases tests/fixtures/evaluation_cases.json \
		--model fake \
		--mode plan-only \
		--out data/evals/workflow/fake

benchmark-workflow-real:
	python scripts/run_workflow_benchmark.py \
		--cases tests/fixtures/evaluation_cases.json \
		--model $${OPENMC_AGENT_MODEL:-deepseek:deepseek-chat} \
		--mode plan-only \
		--allow-real-llm \
		--out data/evals/workflow/real

diff-workflow-reports:
	python scripts/diff_evaluation_reports.py \
		--base $${BASE_REPORT} \
		--head $${HEAD_REPORT} \
		--out $${OUT_DIFF:-report_diff.md}

gate-workflow-regression:
	python scripts/diff_evaluation_reports.py \
		--base $${BASE_REPORT} \
		--head $${HEAD_REPORT} \
		--fail-on-regression
