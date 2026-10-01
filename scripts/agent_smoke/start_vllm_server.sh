#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 || $# -gt 10 ]]; then
    echo "usage: $0 MODEL_PATH SERVED_NAME PORT RUN_NAME [TOOL_CALL_PARSER] [MAX_MODEL_LEN] [TENSOR_PARALLEL_SIZE] [MAX_NUM_SEQS] [REASONING_PARSER] [ENABLE_PREFIX_CACHING]" >&2
    exit 2
fi

model_path=$1
served_name=$2
port=$3
run_name=$4
tool_call_parser=${5:-}
max_model_len=${6:-32768}
tensor_parallel_size=${7:-1}
max_num_seqs=${8:-8}
reasoning_parser=${9:-}
enable_prefix_caching=${10:-false}
run_root=/data/miyapeng/mmcode/MultimodalCode/runs/agent_smoke/servers/$run_name
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm

mkdir -p "$run_root"
exec > >(tee -a "$run_root/job.log") 2>&1

echo "[server] started_at=$(date -u +%FT%TZ)"
echo "[server] host=$(hostname)"
echo "[server] model_path=$model_path served_name=$served_name port=$port max_model_len=$max_model_len tensor_parallel_size=$tensor_parallel_size max_num_seqs=$max_num_seqs tool_call_parser=${tool_call_parser:-none} reasoning_parser=${reasoning_parser:-none} enable_prefix_caching=$enable_prefix_caching"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

if [[ ! -x $vllm_bin ]]; then
    echo "[server] vLLM executable is missing: $vllm_bin" >&2
    exit 1
fi

rm -f "$run_root/ready.json" "$run_root/failed"

server_args=(
    serve "$model_path"
    --host 0.0.0.0
    --port "$port"
    --served-model-name "$served_name"
    --gpu-memory-utilization 0.92
    --max-model-len "$max_model_len"
    --tensor-parallel-size "$tensor_parallel_size"
    --max-num-seqs "$max_num_seqs"
)
if [[ -n $tool_call_parser ]]; then
    server_args+=(--enable-auto-tool-choice --tool-call-parser "$tool_call_parser")
fi
if [[ -n $reasoning_parser ]]; then
    server_args+=(--reasoning-parser "$reasoning_parser")
fi
if [[ $enable_prefix_caching == true ]]; then
    server_args+=(--enable-prefix-caching)
elif [[ $enable_prefix_caching != false ]]; then
    echo "ENABLE_PREFIX_CACHING must be true or false, got: $enable_prefix_caching" >&2
    exit 2
fi

"$vllm_bin" "${server_args[@]}" \
    >"$run_root/vllm.log" 2>&1 &
server_pid=$!

cleanup() {
    # A pod IP is valid only for the lifetime of this server process. Removing
    # the readiness record prevents resume controllers from trusting a stale
    # endpoint after ClusterX terminates the service job.
    rm -f "$run_root/ready.json"
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

ready=false
for _ in $(seq 1 180); do
    if curl -fsS "http://127.0.0.1:$port/v1/models" >"$run_root/models.json"; then
        ready=true
        break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
        echo "[server] vLLM exited during startup" >&2
        touch "$run_root/failed"
        tail -200 "$run_root/vllm.log" || true
        exit 1
    fi
    sleep 5
done

if [[ $ready != true ]]; then
    echo "[server] startup timed out" >&2
    touch "$run_root/failed"
    tail -200 "$run_root/vllm.log" || true
    exit 1
fi

pod_ip=$(hostname -I | awk '{print $1}')
endpoint="http://$pod_ip:$port/v1"
/data/miyapeng/miniconda3/envs/mmcode/bin/python - \
    "$run_root/ready.json" "$endpoint" "$served_name" "$model_path" \
    "$max_model_len" "$tensor_parallel_size" "$max_num_seqs" \
    "$tool_call_parser" "$reasoning_parser" "$enable_prefix_caching" <<'PY'
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    endpoint,
    model,
    model_path,
    max_model_len,
    tensor_parallel_size,
    max_num_seqs,
    tool_call_parser,
    reasoning_parser,
    enable_prefix_caching,
) = sys.argv[1:]
Path(path).write_text(json.dumps({
    "endpoint": endpoint,
    "model": model,
    "model_path": model_path,
    "max_model_len": int(max_model_len),
    "tensor_parallel_size": int(tensor_parallel_size),
    "max_num_seqs": int(max_num_seqs),
    "tool_call_parser": tool_call_parser or None,
    "reasoning_parser": reasoning_parser or None,
    "enable_prefix_caching": enable_prefix_caching == "true",
    "hostname": socket.gethostname(),
    "ready_at_utc": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY

echo "[server] ready endpoint=$endpoint"
wait "$server_pid"
