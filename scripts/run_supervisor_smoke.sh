#!/bin/bash
# Run Supervisor Real LLM Advisory Smoke Test
#
# Runs the full workflow benchmark with real LLM (deepseek) in advisory mode.
# All LLM intelligence features are enabled by default:
#   - Semantic Audit (warning_only)
#   - LLM Repair Proposer (proposal_only)
#   - Run Supervisor (advisory)
#
# The supervisor runs but does NOT change routing — it only records decisions.
# Compare supervisor's proposed action vs deterministic fallback in the output.

set -euo pipefail

MODEL="${1:-deepseek:deepseek-chat}"
MAX_CASES="${2:-6}"
OUT="data/evals/workflow/run_supervisor_deepseek_advisory"

echo "============================================================"
echo "  Real LLM Advisory Smoke Test"
echo "  Model:      $MODEL"
echo "  Max cases:  $MAX_CASES"
echo "  Output:     $OUT"
echo "  Features:   semantic-audit + llm-repair + run-supervisor (all advisory)"
echo "============================================================"
echo ""

conda run --no-capture-output -n openmc-env python scripts/run_workflow_benchmark.py \
    --cases tests/fixtures/evaluation_cases.json \
    --model "$MODEL" \
    --mode plan-only \
    --allow-real-llm \
    --max-cases "$MAX_CASES" \
    --out "$OUT"

echo ""
echo "============================================================"
echo "  Done! Key artifacts:"
echo "    Report:  $OUT/evaluation_report.json"
echo "    Summary: $OUT/benchmark_summary.md"
echo ""
echo "  Check the '## Run Supervisor' section in the summary for:"
echo "    - action accuracy (proposed vs expected)"
echo "    - veto rate (should be low in advisory)"
echo "    - fallback rate (deterministic vs LLM)"
echo "    - human escalation accuracy"
echo "============================================================"
