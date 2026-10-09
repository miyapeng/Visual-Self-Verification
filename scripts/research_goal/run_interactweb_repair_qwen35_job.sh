#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
mmcode_python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
cases="$project_root/configs/research/interactweb_active_repair_cases.json"
output_root="$project_root/runs/research/active_visual_verification/interactweb-repair-qwen35-001"
port=18032

mkdir -p "$output_root"
exec > >(tee -a "$output_root/job.log") 2>&1
echo "[interactweb-repair] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.local/runtime/research/playwright"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.90 \
  --max-model-len 65536 \
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
echo "[interactweb-repair] vLLM ready"

cd "$project_root"
run_status=0
for policy in full guarded_frontier guarded_frontier_text_only; do
  echo "[interactweb-repair] policy=$policy started_at=$(date -u +%FT%TZ)"
  PYTHONPATH=src "$mmcode_python" scripts/research/run.py run \
    --cases "$cases" \
    --output-root "$output_root" \
    --policy "$policy" \
    --backend vllm \
    --model Qwen3.5-9B \
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
    --no-video || run_status=1
  echo "[interactweb-repair] policy=$policy ended_at=$(date -u +%FT%TZ)"
done

echo "[interactweb-repair] completed_at=$(date -u +%FT%TZ) status=$run_status"
exit "$run_status"
