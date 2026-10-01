#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/data/miyapeng/mmcode/MultimodalCode}"
EVAL_PYTHON="${EVAL_PYTHON:-/data/miyapeng/miniconda3/envs/mmcode/bin/python}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8001/v1}"
MODEL="${MODEL:-Qwen3.5-4B}"
CASES="${CASES:-configs/research/webcompass_local_cases.json}"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src"

run_one() {
  local output_root="$1"
  local context_tokens="$2"
  local context_images="$3"
  "$EVAL_PYTHON" research_run.py run \
    --cases "$CASES" \
    --output-root "$output_root" \
    --policy guarded_frontier \
    --backend vllm \
    --model "$MODEL" \
    --base-url "$BASE_URL" \
    --limit 4 \
    --max-revisions 2 \
    --max-contract-retries 1 \
    --context-tokens "$context_tokens" \
    --context-images "$context_images" \
    --seed 0 \
    --allocated-gpus 1 \
    --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}'
}

# Each command resumes from its own immutable output root. Completed cases make
# no additional model calls when this script is restarted.
matrix_status=0
run_one runs/research/webcompass-guarded-frontier-qwen35-4b-no-evidence-images-v1 4096 0 || matrix_status=1
run_one runs/research/webcompass-guarded-frontier-qwen35-4b-budget512-v1 512 4 || matrix_status=1
run_one runs/research/webcompass-guarded-frontier-qwen35-4b-budget256-v1 256 4 || matrix_status=1
exit "$matrix_status"
