#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/data/miyapeng/mmcode/MultimodalCode}"
EVAL_PYTHON="${EVAL_PYTHON:-/data/miyapeng/miniconda3/envs/mmcode/bin/python}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8001/v1}"
MODEL="${MODEL:-Qwen3.5-4B}"
CASES="${CASES:-configs/research/webcompass_local_cases.json}"
OUTPUT_ROOT="${OUTPUT_ROOT:-runs/research/webcompass-final-matrix-qwen35-4b-v1}"
HISTORICAL_ROOT="${HISTORICAL_ROOT:-runs/research/webcompass-final-certification-ablation-qwen35-4b-v1}"
POLICIES="${POLICIES:-full recent summary llm_summary residual guarded_frontier oracle guarded_frontier_text_only unbound_frontier}"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src"

common=(
  --cases "$CASES"
  --backend vllm
  --model "$MODEL"
  --base-url "$BASE_URL"
  --limit 4
  --max-revisions 2
  --max-contract-retries 1
  --max-tokens 8192
  --context-tokens 4096
  --context-images 4
  --seed 0
  --allocated-gpus 1
  --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}'
)

matrix_status=0
for policy in $POLICIES; do
  "$EVAL_PYTHON" research_run.py run \
    "${common[@]}" \
    --output-root "$OUTPUT_ROOT" \
    --policy "$policy" || matrix_status=1
done

"$EVAL_PYTHON" research_run.py run \
  "${common[@]}" \
  --output-root "$HISTORICAL_ROOT" \
  --policy guarded_frontier \
  --reuse-historical-certification || matrix_status=1

exit "$matrix_status"
