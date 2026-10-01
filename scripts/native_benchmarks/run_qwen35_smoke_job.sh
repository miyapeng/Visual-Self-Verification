#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
frontalk_python=/data/miyapeng/miniconda3/envs/frontalk/bin/python
port=18021
run_label=qwen35-9b-local-support-smoke-20260817
run_root="$project_root/runs/native_benchmarks/$run_label"
server_log="$run_root/vllm.log"

mkdir -p "$run_root"
exec > >(tee -a "$run_root/job.log") 2>&1
echo "[native-smoke] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B gpt-4o deepseek-v3.2 gpt-5-mini \
  --gpu-memory-utilization 0.92 \
  --max-model-len 65536 \
  --max-num-seqs 2 \
  >"$server_log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 240); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$run_root/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$server_log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[native-smoke] vLLM ready"

# FronTalk: one released dialogue, all ten turns. The frozen upstream source is
# executed directly; only data.jsonl is reduced to a deterministic smoke split.
frontalk_input="$run_root/frontalk_input"
frontalk_output="$run_root/frontalk"
mkdir -p "$frontalk_input" "$frontalk_output"
"$frontalk_python" "$project_root/scripts/frontalk/prepare_subset.py" \
  --output "$frontalk_input/data.jsonl" --count 1
if [[ ! -e "$frontalk_input/placeholder" ]]; then
  ln -s "$project_root/evaluate/frontalk/upstream/placeholder" "$frontalk_input/placeholder"
fi
(
  cd "$frontalk_input"
  export OPENAI_API_KEY=EMPTY
  export OPENAI_BASE_URL="http://127.0.0.1:$port/v1"
  export NO_PROXY=localhost,127.0.0.1
  export no_proxy="$NO_PROXY"
  "$frontalk_python" "$project_root/evaluate/frontalk/upstream/infer_multiturn_textual.py" \
    "$frontalk_output" \
    --local_openai_port "$port" \
    --local_openai_key EMPTY \
    --openai_model Qwen3.5-9B \
    --num_workers 1 \
    --max_tokens 8192
)
echo "[native-smoke] FronTalk trajectory complete"

"$frontalk_python" "$project_root/evaluate/verify_official_sources.py" frontalk
echo "[native-smoke] completed_at=$(date -u +%FT%TZ)"
