#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_SH="${CONDA_SH:-/home/wbd/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-openmc-env}"
MODEL="${OPENMC_AGENT_MODEL:-zhipu:glm-5.2}"
OUTPUT_DIR="${OUTPUT_DIR:-data/runs/manual/script-run}"
TIMEOUT_SECONDS=""
MAX_RETRIES=""
STREAM="${OPENMC_AGENT_STREAM:-1}"
MD_FILE=""
REQUIREMENT=""
OPERATING_STATE=""
ENABLE_PLOT=0
ENABLE_SMOKE=0
COMPACT_OUTPUT=1
JSON_OUTPUT=0
INTERACTIVE_FEEDBACK="auto"
MAX_EXPERT_ROUNDS=2
BENCHMARK=0
BENCHMARK_CASES="tests/fixtures/evaluation_cases.json"
BENCHMARK_MAX_CASES=""
BENCHMARK_MODE="plan-only"
SUPERVISOR_MODE="advisory"
DISABLE_AUDIT=0
DISABLE_REPAIR=0
DISABLE_SUPERVISOR=0
REFERENCE_PATCH_POLICY="off"
GOLD_FEW_SHOTS=0
MONOLITHIC_FALLBACK=0
PLAN_LOOP_MODE="off"
MAX_PLAN_REVIEW_ROUNDS=""
MAX_PLAN_REPAIR_ROUNDS=""
MAX_PLAN_HUMAN_ROUNDS=""
MAX_PLAN_NO_PROGRESS_ROUNDS=""
MAX_PLAN_ADDITIONAL_LLM_CALLS=""
PLAN_REVIEWER_MODEL=""
PLAN_REPAIR_MODEL=""
PLAN_HUMAN_MODE="off"
PLAN_GATES=""
PLACEMENT_REVIEW_MODE="off"
FACTS_REVIEW_CHUNK_CHARS=""
MAX_FACTS_REVIEW_CHUNKS=""

usage() {
  cat <<'EOF'
Usage:
  scripts/run_inspect.sh [options]

Options:
  --md-file PATH          Read requirement from a Markdown file.
  --requirement TEXT      Natural-language requirement.
  --state ID              Select one operating state (e.g., 1A) when the
                          markdown describes multiple states; only that
                          state is modeled.
  --model PROVIDER:MODEL  LLM model as 'provider:model'. Default: zhipu:glm-5.2
                          Examples: zhipu:glm-5.2, deepseek:deepseek-chat,
                          ds:deepseek-v4-flash, deepseek:deepseek-reasoner.
  --full                  Enable both OpenMC geometry plot and smoke test.
  --plot                  Enable geometry plot only.
  --smoke-test            Enable low-particle smoke test only.
  --interactive-feedback  Ask human expert questions when the graph detects
                          missing or ambiguous modeling facts.
                          This is also enabled automatically for terminal runs.
  --no-interactive-feedback
                          Disable expert questions for batch/non-interactive runs.
  --max-expert-rounds N   Maximum interactive expert feedback rounds. Default: 2
  --output-dir PATH       Output directory. Default: data/runs/manual/script-run
  --timeout-seconds N     Read timeout per request. Default: 240
  --max-retries N         Retries after a transport error. Default: 2
  --no-stream             Disable SSE streaming. Streaming is on by default so
                          slow (reasoning) generations do not hit the read
                          timeout and you can see token-level progress.
  --text                  Print the full human-readable transcript to the terminal.
  --raw-output            Print the JSON result to the terminal.
                           By default, terminal output is compact; full JSON is
                           saved as transcript.json in the output directory.
  --benchmark             Run workflow benchmark instead of single-model inspect.
                          All LLM intelligence (audit+repair+supervisor) is on
                          by default in advisory mode.
  --cases PATH            Evaluation cases JSON for --benchmark.
                          Default: tests/fixtures/evaluation_cases.json
  --max-cases N           Limit benchmark to first N cases.
  --benchmark-mode MODE   Benchmark mode: plan-only (default), render-only, smoke-test.
  --controlled-route      Run supervisor in controlled_route mode (overrides advisory).
                          WARNING: supervisor decisions will affect real routing.
  --disable-audit         Disable semantic audit.
  --disable-repair        Disable LLM repair proposer.
  --disable-supervisor    Disable run supervisor.
  --reference-patch-policy POLICY
                          Benchmark reference-patch policy for incremental
                          planning. Default: off (no benchmark memory loaded).
                          Choices: off, fallback_after_llm_failure,
                          prefer_reference_for_structural,
                          reference_only_for_structural.
  --gold-few-shots        Enable gold few-shot examples (default: off).
  --allow-monolithic-fallback
                          Allow monolithic plan fallback when incremental
                          patch generation fails (default: off).
  --plan-loop-mode MODE   Phase-0 closed-loop mode: off, advisory, controlled.
  --max-plan-review-rounds N
  --max-plan-repair-rounds N
  --max-plan-human-rounds N
  --max-plan-no-progress-rounds N
  --max-plan-additional-llm-calls N
  --plan-reviewer-model MODEL
  --plan-repair-model MODEL
  --facts-review-chunk-chars N
  --max-facts-review-chunks N
  --plan-human-mode MODE     off or ambiguity_only
  --plan-gates LIST          Comma-separated gates, e.g. facts,placement
  --placement-review-mode MODE  off, advisory, or controlled
  -h, --help              Show this help.

Providers:
  zhipu       -> ZHIPUAI_API_KEY,     endpoint open.bigmodel.cn
  deepseek    -> DEEPSEEK_API_KEY,    endpoint api.deepseek.com
  ds          -> SENSENOVA_API_KEY,   endpoint token.sensenova.cn

The corresponding API key env var is requested securely if not already set.

Examples:
  scripts/run_inspect.sh --md-file Input/case1.md
  scripts/run_inspect.sh --requirement "建立一个 UO2 pin-cell 临界计算" --full
  scripts/run_inspect.sh --model deepseek:deepseek-chat --md-file Input/case2.md --full --text
  scripts/run_inspect.sh --model ds:deepseek-v4-flash --md-file Input/VERA3_problem.md --state 3A
  scripts/run_inspect.sh --md-file Input/VERA1_problem.md --state 1A --text
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --md-file)
      MD_FILE="${2:-}"
      shift 2
      ;;
    --requirement)
      REQUIREMENT="${2:-}"
      shift 2
      ;;
    --state)
      OPERATING_STATE="${2:-}"
      shift 2
      ;;
    --model)
      MODEL="${2:-}"
      shift 2
      ;;
    --full)
      ENABLE_PLOT=1
      ENABLE_SMOKE=1
      shift
      ;;
    --plot)
      ENABLE_PLOT=1
      shift
      ;;
    --smoke-test)
      ENABLE_SMOKE=1
      shift
      ;;
    --interactive-feedback)
      INTERACTIVE_FEEDBACK=1
      shift
      ;;
    --no-interactive-feedback)
      INTERACTIVE_FEEDBACK=0
      shift
      ;;
    --max-expert-rounds)
      MAX_EXPERT_ROUNDS="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --max-retries)
      MAX_RETRIES="${2:-}"
      shift 2
      ;;
    --no-stream)
      STREAM=0
      shift
      ;;
    --text)
      COMPACT_OUTPUT=0
      shift
      ;;
    --raw-output)
      COMPACT_OUTPUT=0
      JSON_OUTPUT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --benchmark)
      BENCHMARK=1
      shift
      ;;
    --cases)
      BENCHMARK_CASES="${2:-}"
      shift 2
      ;;
    --max-cases)
      BENCHMARK_MAX_CASES="${2:-}"
      shift 2
      ;;
    --benchmark-mode)
      BENCHMARK_MODE="${2:-}"
      shift 2
      ;;
    --controlled-route)
      SUPERVISOR_MODE="controlled_route"
      shift
      ;;
    --disable-audit)
      DISABLE_AUDIT=1
      shift
      ;;
    --disable-repair)
      DISABLE_REPAIR=1
      shift
      ;;
    --disable-supervisor)
      DISABLE_SUPERVISOR=1
      shift
      ;;
    --reference-patch-policy)
      REFERENCE_PATCH_POLICY="${2:-}"
      shift 2
      ;;
    --gold-few-shots)
      GOLD_FEW_SHOTS=1
      shift
      ;;
    --allow-monolithic-fallback)
      MONOLITHIC_FALLBACK=1
      shift
      ;;
    --plan-loop-mode)
      PLAN_LOOP_MODE="${2:-}"
      shift 2
      ;;
    --max-plan-review-rounds)
      MAX_PLAN_REVIEW_ROUNDS="${2:-}"
      shift 2
      ;;
    --max-plan-repair-rounds)
      MAX_PLAN_REPAIR_ROUNDS="${2:-}"
      shift 2
      ;;
    --max-plan-human-rounds)
      MAX_PLAN_HUMAN_ROUNDS="${2:-}"
      shift 2
      ;;
    --max-plan-no-progress-rounds)
      MAX_PLAN_NO_PROGRESS_ROUNDS="${2:-}"
      shift 2
      ;;
    --max-plan-additional-llm-calls)
      MAX_PLAN_ADDITIONAL_LLM_CALLS="${2:-}"
      shift 2
      ;;
    --plan-reviewer-model)
      PLAN_REVIEWER_MODEL="${2:-}"
      shift 2
      ;;
    --plan-repair-model)
      PLAN_REPAIR_MODEL="${2:-}"
      shift 2
      ;;
    --plan-human-mode)
      PLAN_HUMAN_MODE="${2:-}"
      shift 2
      ;;
    --plan-gates)
      PLAN_GATES="${2:-}"
      shift 2
      ;;
    --placement-review-mode)
      PLACEMENT_REVIEW_MODE="${2:-}"
      shift 2
      ;;
    --facts-review-chunk-chars)
      FACTS_REVIEW_CHUNK_CHARS="${2:-}"
      shift 2
      ;;
    --max-facts-review-chunks)
      MAX_FACTS_REVIEW_CHUNKS="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$MD_FILE" && -n "$REQUIREMENT" ]]; then
  echo "Use either --md-file or --requirement, not both." >&2
  exit 2
fi

if [[ -z "$MD_FILE" && -z "$REQUIREMENT" ]]; then
  MD_FILE="Input/case1.md"
fi

# Provider is the part before the first ':' in the model id (e.g. zhipu, deepseek).
PROVIDER="${MODEL%%:*}"
  case "$PROVIDER" in
    zhipu)
      API_KEY_ENV="ZHIPUAI_API_KEY"
      TIMEOUT_ENV="ZHIPUAI_TIMEOUT_SECONDS"
      RETRIES_ENV="ZHIPUAI_MAX_RETRIES"
      ;;
    deepseek)
      API_KEY_ENV="DEEPSEEK_API_KEY"
      TIMEOUT_ENV="DEEPSEEK_TIMEOUT_SECONDS"
      RETRIES_ENV="DEEPSEEK_MAX_RETRIES"
      ;;
    ds)
      API_KEY_ENV="SENSENOVA_API_KEY"
      TIMEOUT_ENV="SENSENOVA_TIMEOUT_SECONDS"
      RETRIES_ENV="SENSENOVA_MAX_RETRIES"
      ;;
    *)
      echo "Unsupported provider '$PROVIDER' in model '$MODEL'." >&2
      echo "Use a 'provider:model' id such as 'zhipu:glm-5.2', 'deepseek:deepseek-chat', or 'ds:deepseek-v4-flash'." >&2
      exit 2
      ;;
  esac

# Resolve timeout/retries: --flag > provider env > default.
[[ -z "$TIMEOUT_SECONDS" ]] && TIMEOUT_SECONDS="${!TIMEOUT_ENV:-}"
[[ -z "$TIMEOUT_SECONDS" ]] && TIMEOUT_SECONDS=240
[[ -z "$MAX_RETRIES" ]] && MAX_RETRIES="${!RETRIES_ENV:-}"
[[ -z "$MAX_RETRIES" ]] && MAX_RETRIES=2

if [[ ! -f "$CONDA_SH" ]]; then
  echo "Conda activation script not found: $CONDA_SH" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
cd "$PROJECT_DIR"
mkdir -p "$OUTPUT_DIR"

if [[ -z "${!API_KEY_ENV:-}" ]]; then
  echo "[1/5] Waiting for $API_KEY_ENV input..." >&2
  read -rsp "$API_KEY_ENV: " API_KEY_VALUE
  export "$API_KEY_ENV=$API_KEY_VALUE"
  echo
else
  echo "[1/5] $API_KEY_ENV is already set." >&2
fi

export OPENMC_AGENT_MODEL="$MODEL"
export OPENMC_AGENT_STREAM="$STREAM"
export "$TIMEOUT_ENV=$TIMEOUT_SECONDS"
export "$RETRIES_ENV=$MAX_RETRIES"

# ---------------------------------------------------------------------------
# Benchmark mode: run workflow benchmark with all LLM intelligence
# ---------------------------------------------------------------------------
if [[ "$BENCHMARK" -eq 1 ]]; then
  cmd=(python scripts/run_workflow_benchmark.py
       --cases "$BENCHMARK_CASES"
       --model "$MODEL"
       --mode "$BENCHMARK_MODE"
       --allow-real-llm
       --run-supervisor-mode "$SUPERVISOR_MODE"
       --output-dir "$OUTPUT_DIR")

  [[ -n "$BENCHMARK_MAX_CASES" ]] && cmd+=(--max-cases "$BENCHMARK_MAX_CASES")
  [[ "$DISABLE_AUDIT" -eq 1 ]] && cmd+=(--disable-semantic-audit)
  [[ "$DISABLE_REPAIR" -eq 1 ]] && cmd+=(--disable-llm-repair)
  [[ "$DISABLE_SUPERVISOR" -eq 1 ]] && cmd+=(--disable-run-supervisor)

  echo "[2/5] Conda environment activated: $CONDA_ENV" >&2
  echo "[3/5] Benchmark model: $MODEL (provider: $PROVIDER)" >&2
  echo "      Supervisor: $SUPERVISOR_MODE | Audit: $([[ $DISABLE_AUDIT -eq 0 ]] && echo on || echo off) | Repair: $([[ $DISABLE_REPAIR -eq 0 ]] && echo on || echo off)" >&2
  [[ -n "$BENCHMARK_MAX_CASES" ]] && echo "      Max cases: $BENCHMARK_MAX_CASES" >&2
  echo "[4/5] Cases: $BENCHMARK_CASES" >&2
  echo "[5/5] Output: $OUTPUT_DIR" >&2
  echo "Benchmark is starting. LLM calls may take a while..." >&2
  exec "${cmd[@]}"
fi

# ---------------------------------------------------------------------------
# Single-model inspect mode (default)
# ---------------------------------------------------------------------------
cmd=(python -m openmc_agent.inspect --plan --verbose --model "$MODEL"
      --output-dir "$OUTPUT_DIR")

if [[ "$COMPACT_OUTPUT" -eq 1 ]]; then
  cmd+=(--compact)
elif [[ "$JSON_OUTPUT" -eq 1 ]]; then
  cmd+=(--json)
fi

[[ "$DISABLE_AUDIT" -eq 0 ]] && cmd+=(--enable-semantic-audit)
[[ "$DISABLE_REPAIR" -eq 0 ]] && cmd+=(--enable-llm-repair)
if [[ "$DISABLE_SUPERVISOR" -eq 0 ]]; then
  cmd+=(--enable-run-supervisor)
  [[ "$SUPERVISOR_MODE" == "controlled_route" ]] && cmd+=(--controlled-route)
fi

cmd+=(--reference-patch-policy "$REFERENCE_PATCH_POLICY")
cmd+=(--plan-loop-mode "$PLAN_LOOP_MODE")
[[ -n "$PLAN_REVIEWER_MODEL" ]] && cmd+=(--plan-reviewer-model "$PLAN_REVIEWER_MODEL")
[[ -n "$PLAN_REPAIR_MODEL" ]] && cmd+=(--plan-repair-model "$PLAN_REPAIR_MODEL")
cmd+=(--plan-human-mode "$PLAN_HUMAN_MODE")
[[ -n "$PLAN_GATES" ]] && cmd+=(--plan-gates "$PLAN_GATES")
cmd+=(--placement-review-mode "$PLACEMENT_REVIEW_MODE")
[[ -n "$FACTS_REVIEW_CHUNK_CHARS" ]] && cmd+=(--facts-review-chunk-chars "$FACTS_REVIEW_CHUNK_CHARS")
[[ -n "$MAX_FACTS_REVIEW_CHUNKS" ]] && cmd+=(--max-facts-review-chunks "$MAX_FACTS_REVIEW_CHUNKS")
[[ -n "$MAX_PLAN_REVIEW_ROUNDS" ]] && cmd+=(--max-plan-review-rounds "$MAX_PLAN_REVIEW_ROUNDS")
[[ -n "$MAX_PLAN_REPAIR_ROUNDS" ]] && cmd+=(--max-plan-repair-rounds "$MAX_PLAN_REPAIR_ROUNDS")
[[ -n "$MAX_PLAN_HUMAN_ROUNDS" ]] && cmd+=(--max-plan-human-rounds "$MAX_PLAN_HUMAN_ROUNDS")
[[ -n "$MAX_PLAN_NO_PROGRESS_ROUNDS" ]] && cmd+=(--max-plan-no-progress-rounds "$MAX_PLAN_NO_PROGRESS_ROUNDS")
[[ -n "$MAX_PLAN_ADDITIONAL_LLM_CALLS" ]] && cmd+=(--max-plan-additional-llm-calls "$MAX_PLAN_ADDITIONAL_LLM_CALLS")
[[ "$GOLD_FEW_SHOTS" -eq 1 ]] && cmd+=(--gold-few-shots)
[[ "$MONOLITHIC_FALLBACK" -eq 1 ]] && cmd+=(--allow-monolithic-fallback)

if [[ "$ENABLE_PLOT" -eq 1 ]]; then
  cmd+=(--plot)
fi

if [[ "$ENABLE_SMOKE" -eq 1 ]]; then
  cmd+=(--smoke-test)
fi

if [[ "$INTERACTIVE_FEEDBACK" == "1" ]]; then
  cmd+=(--interactive-feedback --max-expert-rounds "$MAX_EXPERT_ROUNDS")
elif [[ "$INTERACTIVE_FEEDBACK" == "0" ]]; then
  cmd+=(--no-interactive-feedback)
else
  cmd+=(--max-expert-rounds "$MAX_EXPERT_ROUNDS")
fi

if [[ -n "$MD_FILE" ]]; then
  cmd+=(--md-file "$MD_FILE")
else
  cmd+=("$REQUIREMENT")
fi

if [[ -n "$OPERATING_STATE" ]]; then
  cmd+=(--state "$OPERATING_STATE")
fi

echo "[2/5] Conda environment activated: $CONDA_ENV" >&2
echo "[3/5] Running OpenMC Agent with model: $MODEL (provider: $PROVIDER)" >&2
echo "      $TIMEOUT_ENV=$TIMEOUT_SECONDS s, $RETRIES_ENV=$MAX_RETRIES" >&2
if [[ "$STREAM" == "1" ]]; then
  echo "      Streaming: on (OPENMC_AGENT_STREAM=1; pass --no-stream to disable)" >&2
else
  echo "      Streaming: off" >&2
fi
if [[ -n "$MD_FILE" ]]; then
  echo "[4/5] Input markdown: $MD_FILE" >&2
else
  echo "[4/5] Input requirement: $REQUIREMENT" >&2
fi
echo "[5/5] Output directory: $OUTPUT_DIR" >&2
echo "      Terminal: compact status | Full report: $OUTPUT_DIR/transcript.json" >&2
echo "      Node/error log: $OUTPUT_DIR/cli.log" >&2
echo "Agent is starting. LLM calls may take a while..." >&2
"${cmd[@]}" 2>&1 | tee "$OUTPUT_DIR/cli.log"
