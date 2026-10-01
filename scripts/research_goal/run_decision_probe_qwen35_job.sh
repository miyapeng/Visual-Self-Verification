#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
mmcode_python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
input_dir="${1:-$project_root/runs/research/active_visual_verification/decision-probe-001}"
output_dir="${2:-$input_dir/qwen35-9b-run-002-no-thinking}"
port=18031

mkdir -p "$output_dir"
exec > >(tee -a "$output_dir/job.log") 2>&1
echo "[decision-probe] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.90 \
  --max-model-len 32768 \
  --max-num-seqs 8 \
  >"$output_dir/vllm.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$output_dir/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$output_dir/vllm.log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[decision-probe] vLLM ready"

cd "$project_root"
PYTHONPATH=src "$mmcode_python" scripts/research_goal/run_decision_probes.py run \
  --input-dir "$input_dir" \
  --output-dir "$output_dir" \
  --model Qwen3.5-9B \
  --base-url "http://127.0.0.1:$port/v1" \
  --workers 8 \
  --max-tokens 256 \
  --temperature 0 \
  --seed 0 \
  --thinking disabled

PYTHONPATH=src "$mmcode_python" scripts/research_goal/run_decision_probes.py summarize \
  --input-dir "$input_dir" \
  --run-dir "$output_dir" \
  --output "$output_dir/summary.json"

echo "[decision-probe] completed_at=$(date -u +%FT%TZ)"
