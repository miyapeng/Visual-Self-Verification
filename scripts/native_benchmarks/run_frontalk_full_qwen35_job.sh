#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
frontalk_python=/data/miyapeng/miniconda3/envs/frontalk/bin/python
port=18022
run_label=qwen35-9b-local-support-frontalk-text-full-20260818
run_root="$project_root/runs/native_benchmarks/$run_label"
output_root="$run_root/frontalk"

mkdir -p "$run_root"
exec > >(tee -a "$run_root/job.log") 2>&1
echo "[frontalk-full] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

"$frontalk_python" "$project_root/evaluate/frontalk/run_official.py" preflight
"$frontalk_python" "$project_root/evaluate/verify_official_sources.py" frontalk

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B gpt-4o \
  --gpu-memory-utilization 0.92 \
  --max-model-len 262144 \
  --max-num-seqs 4 \
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
echo "[frontalk-full] vLLM ready"

export OPENAI_API_KEY=EMPTY
export OPENAI_BASE_URL="http://127.0.0.1:$port/v1"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"

"$frontalk_python" "$project_root/evaluate/frontalk/run_official.py" infer-text \
  "$output_root" \
  --local_openai_port "$port" \
  --local_openai_key EMPTY \
  --openai_model Qwen3.5-9B \
  --num_workers 4 \
  --max_tokens 8192

"$frontalk_python" "$project_root/scripts/frontalk/validate_run.py" \
  "$output_root" --dialogues 100 --turns-per-dialogue 10
echo "[frontalk-full] completed_at=$(date -u +%FT%TZ)"
