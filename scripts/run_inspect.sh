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
ENABLE_PLOT=0
ENABLE_SMOKE=0
JSON_OUTPUT=1
INTERACTIVE_FEEDBACK="auto"
MAX_EXPERT_ROUNDS=2

usage() {
  cat <<'EOF'
Usage:
  scripts/run_inspect.sh [options]

Options:
  --md-file PATH          Read requirement from a Markdown file.
  --requirement TEXT      Natural-language requirement.
  --model PROVIDER:MODEL  LLM model as 'provider:model'. Default: zhipu:glm-5.2
                          Examples: zhipu:glm-5.2, deepseek:deepseek-chat,
                          deepseek:deepseek-reasoner.
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
  --text                  Print human-readable transcript instead of JSON.
  -h, --help              Show this help.

Providers:
  zhipu       -> ZHIPUAI_API_KEY,    endpoint open.bigmodel.cn
  deepseek    -> DEEPSEEK_API_KEY,   endpoint api.deepseek.com

The corresponding API key env var is requested securely if not already set.

Examples:
  scripts/run_inspect.sh --md-file Input/case1.md
  scripts/run_inspect.sh --requirement "建立一个 UO2 pin-cell 临界计算" --full
  scripts/run_inspect.sh --model deepseek:deepseek-chat --md-file Input/case2.md --full --text
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
      JSON_OUTPUT=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
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
  *)
    echo "Unsupported provider '$PROVIDER' in model '$MODEL'." >&2
    echo "Use a 'provider:model' id such as 'zhipu:glm-5.2' or 'deepseek:deepseek-chat'." >&2
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

cmd=(python -m openmc_agent.inspect --plan --verbose --model "$MODEL" --output-dir "$OUTPUT_DIR")

if [[ "$JSON_OUTPUT" -eq 1 ]]; then
  cmd+=(--json)
fi

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
echo "Agent is starting. LLM calls may take a while..." >&2
"${cmd[@]}"
