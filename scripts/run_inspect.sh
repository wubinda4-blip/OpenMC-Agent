#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_SH="${CONDA_SH:-/home/wbd/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-openmc-env}"
MODEL="${OPENMC_AGENT_MODEL:-zhipu:glm-5.2}"
OUTPUT_DIR="${OUTPUT_DIR:-data/runs/manual/script-run}"
TIMEOUT_SECONDS="${ZHIPUAI_TIMEOUT_SECONDS:-240}"
MAX_RETRIES="${ZHIPUAI_MAX_RETRIES:-2}"
MD_FILE=""
REQUIREMENT=""
ENABLE_PLOT=0
ENABLE_SMOKE=0
JSON_OUTPUT=1

usage() {
  cat <<'EOF'
Usage:
  scripts/run_inspect.sh [options]

Options:
  --md-file PATH          Read requirement from a Markdown file.
  --requirement TEXT      Natural-language requirement.
  --full                  Enable both OpenMC geometry plot and smoke test.
  --plot                  Enable geometry plot only.
  --smoke-test            Enable low-particle smoke test only.
  --output-dir PATH       Output directory. Default: data/runs/manual/script-run
  --timeout-seconds N     Zhipu read timeout. Default: 240
  --max-retries N         Retries after timeout. Default: 2
  --text                  Print human-readable transcript instead of JSON.
  -h, --help              Show this help.

Examples:
  scripts/run_inspect.sh --md-file Input/case1.md
  scripts/run_inspect.sh --requirement "建立一个 UO2 pin-cell 临界计算" --full

The script asks for ZHIPUAI_API_KEY securely if it is not already exported.
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

if [[ ! -f "$CONDA_SH" ]]; then
  echo "Conda activation script not found: $CONDA_SH" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
cd "$PROJECT_DIR"

if [[ -z "${ZHIPUAI_API_KEY:-}" ]]; then
  echo "[1/5] Waiting for ZHIPUAI_API_KEY input..." >&2
  read -rsp "ZHIPUAI_API_KEY: " ZHIPUAI_API_KEY
  export ZHIPUAI_API_KEY
  echo
else
  echo "[1/5] ZHIPUAI_API_KEY is already set." >&2
fi

export OPENMC_AGENT_MODEL="$MODEL"
export ZHIPUAI_TIMEOUT_SECONDS="$TIMEOUT_SECONDS"
export ZHIPUAI_MAX_RETRIES="$MAX_RETRIES"

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

if [[ -n "$MD_FILE" ]]; then
  cmd+=(--md-file "$MD_FILE")
else
  cmd+=("$REQUIREMENT")
fi

echo "[2/5] Conda environment activated: $CONDA_ENV" >&2
echo "[3/5] Running OpenMC Agent with model: $MODEL" >&2
echo "      Zhipu timeout: ${ZHIPUAI_TIMEOUT_SECONDS}s, max retries: ${ZHIPUAI_MAX_RETRIES}" >&2
if [[ -n "$MD_FILE" ]]; then
  echo "[4/5] Input markdown: $MD_FILE" >&2
else
  echo "[4/5] Input requirement: $REQUIREMENT" >&2
fi
echo "[5/5] Output directory: $OUTPUT_DIR" >&2
echo "Agent is starting. LLM calls may take a while..." >&2
"${cmd[@]}"
