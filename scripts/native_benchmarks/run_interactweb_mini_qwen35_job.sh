#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
interact_python=/data/miyapeng/miniconda3/envs/interactweb/bin/python
interact_bin=/data/miyapeng/miniconda3/envs/interactweb/bin
port=18023
run_label=qwen35-9b-local-support-interactweb-mini-proxy-20260818
run_root="$project_root/runs/native_benchmarks/$run_label"
output_root="$run_root/interactweb"

mkdir -p "$run_root"
exec > >(tee -a "$run_root/job.log") 2>&1
echo "[interactweb-mini] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

export PATH="$interact_bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.runtime/interactweb/playwright"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"

echo "[interactweb-mini] checking npm registry through platform proxy"
npm ping --registry=https://registry.npmjs.org --fetch-timeout=30000 --fetch-retries=1
"$interact_python" "$project_root/evaluate/interactweb_bench/run_official.py" preflight
"$interact_python" "$project_root/evaluate/verify_official_sources.py" interactweb_bench

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.92 \
  --max-model-len 262144 \
  --max-num-seqs 1 \
  >"$run_root/vllm.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$run_root/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$run_root/vllm.log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[interactweb-mini] vLLM ready"

export OPENAILIKE_API_KEY=EMPTY
export OPENAILIKE_BASE_URL="http://127.0.0.1:$port/v1"
export OPENAILIKE_VLM_API_KEY=EMPTY
export OPENAILIKE_VLM_BASE_URL="http://127.0.0.1:$port/v1"
export BUILDER_API_KEY=EMPTY
export BUILDER_BASE_URL="http://127.0.0.1:$port/v1"
export COPILOT_API_KEY=EMPTY
export COPILOT_BASE_URL="http://127.0.0.1:$port/v1"
export USER_MODEL_API_KEY=EMPTY
export USER_MODEL_BASE_URL="http://127.0.0.1:$port/v1"

"$interact_python" "$project_root/evaluate/interactweb_bench/run_trajectory_only.py" \
  --data_path "$project_root/data/interactweb_bench/test_mini.jsonl" \
  --output_dir "$output_root" \
  --builder_model Qwen3.5-9B \
  --visual_copilot_model Qwen3.5-9B \
  --user_model Qwen3.5-9B \
  --webvoyager_model Qwen3.5-9B \
  --max_workers 1

"$interact_python" "$project_root/scripts/interactweb_bench/validate_trajectory.py" \
  --output-dir "$output_root" --model Qwen3.5-9B --expected 1
echo "[interactweb-mini] completed_at=$(date -u +%FT%TZ)"
