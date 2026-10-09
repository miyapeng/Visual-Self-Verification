#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-35B-A3B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
mmcode_python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
cases="$project_root/configs/research/interactweb_active_repair_cases.json"
persistent_root="$project_root/runs/research/active_visual_verification/interactweb-repair-capacity-qwen35b-002"
scratch_root=/tmp/mmcode-iwcap-q3535-0821r1
output_root="$scratch_root/stronger_model"
port=18034

mkdir -p "$persistent_root" "$scratch_root"
exec > >(tee -a "$scratch_root/job.log") 2>&1
echo "[repair-capacity-r1] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
df -h /data /tmp

# The first infrastructure-only attempt failed before inference because the
# shared /data filesystem had only 80 MiB free. Keep all mutable caches, logs,
# browser artifacts, and research outputs on the task node's local /tmp. Only
# a compressed audit archive and small index files are copied back to /data.
export XDG_CACHE_HOME="$scratch_root/cache"
export HF_HOME="$scratch_root/cache/huggingface"
export TORCHINDUCTOR_CACHE_DIR="$scratch_root/cache/torchinductor"
export TRITON_CACHE_DIR="$scratch_root/cache/triton"
export TMPDIR="$scratch_root/tmp"
mkdir -p "$XDG_CACHE_HOME" "$HF_HOME" "$TORCHINDUCTOR_CACHE_DIR" "$TRITON_CACHE_DIR" "$TMPDIR"

export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.local/runtime/research/playwright"

server_pid=
persist_small_logs() {
  for name in job.log vllm.log models.json; do
    if [[ -f "$scratch_root/$name" ]]; then
      cp -f "$scratch_root/$name" "$persistent_root/$name" 2>/dev/null || true
    fi
  done
}
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  persist_small_logs
}
trap cleanup EXIT

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-35B-A3B \
  --gpu-memory-utilization 0.98 \
  --max-model-len 32768 \
  --max-num-seqs 1 \
  >"$scratch_root/vllm.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$scratch_root/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$scratch_root/vllm.log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[repair-capacity-r1] vLLM ready"

cd "$project_root"
set +e
PYTHONPATH=src "$mmcode_python" scripts/research/run.py run \
  --cases "$cases" \
  --output-root "$output_root" \
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

summary="$output_root/guarded_frontier/summary.json"
run_config="$output_root/guarded_frontier/run_config.json"
if [[ ! -s "$summary" || ! -s "$run_config" ]]; then
  echo "[repair-capacity-r1] missing summary or run config" >&2
  exit 1
fi

# Release the GPU before packaging results.
kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=

archive="$scratch_root/result-audit.tar.gz"
tar -C "$scratch_root" -czf "$archive" stronger_model job.log vllm.log models.json
cp -f "$archive" "$persistent_root/result-audit.tar.gz"
cp -f "$summary" "$persistent_root/summary.json"
cp -f "$run_config" "$persistent_root/run_config.json"
sha256sum "$persistent_root/result-audit.tar.gz" >"$persistent_root/checksums.sha256"
echo "[repair-capacity-r1] completed_at=$(date -u +%FT%TZ) experimental_status=$experimental_status"
persist_small_logs
