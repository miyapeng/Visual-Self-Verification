#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-35B-A3B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
mmcode_python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
cases="$project_root/configs/research/interactweb_active_repair_cases.json"
output_root="$project_root/runs/research/active_visual_verification/interactweb-repair-capacity-qwen35b-001"
port=18034

mkdir -p "$output_root"
exec > >(tee -a "$output_root/job.log") 2>&1
echo "[repair-capacity] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.runtime/research/playwright"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-35B-A3B \
  --gpu-memory-utilization 0.98 \
  --max-model-len 32768 \
  --max-num-seqs 1 \
  >"$output_root/vllm.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$output_root/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$output_root/vllm.log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[repair-capacity] vLLM ready"

cd "$project_root"
set +e
PYTHONPATH=src "$mmcode_python" research_run.py run \
  --cases "$cases" \
  --output-root "$output_root/stronger_model" \
  --policy guarded_frontier \
  --source-context execution_rooted \
  --backend vllm \
  --model Qwen3.5-35B-A3B \
  --base-url "http://127.0.0.1:$port/v1" \
  --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' \
  --max-revisions 1 \
  --max-contract-retries 1 \
  --max-tokens 8192 \
  --temperature 0 \
  --seed 0 \
  --context-tokens 4096 \
  --context-images 4 \
  --context-image-pixels 8000000 \
  --timeout 600 \
  --allocated-gpus 1 \
  --no-video
experimental_status=$?
set -e

summary="$output_root/stronger_model/guarded_frontier/summary.json"
if [[ ! -s "$summary" ]]; then
  echo "[repair-capacity] missing summary" >&2
  exit 1
fi
echo "[repair-capacity] completed_at=$(date -u +%FT%TZ) experimental_status=$experimental_status"
