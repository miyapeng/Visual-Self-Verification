#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
launcher="$project_root/scripts/agent_smoke/start_vllm_server.sh"
job_name=${1:-mmc-q35-9b-v2w-official}
server_run=${2:-qwen35-9b-v2w-official}

# Qwen3.5's official vLLM tool-use configuration uses the qwen3_coder tool
# parser together with the qwen3 reasoning parser. TP=2 is only a
# capacity/throughput choice; it does not change the Claude Code scaffold.
clusterx run \
    --job-name "$job_name" \
    --num-nodes 1 \
    --gpus-per-task 2 \
    --cpus-per-task 32 \
    --memory-per-task 320 \
    --shm-size-gib 32 \
    --no-env \
    bash "$launcher" \
    /data/miyapeng/model/Qwen3.5-9B Qwen3.5-9B 18002 \
    "$server_run" qwen3_coder 262144 2 3 qwen3 false

echo "Server state: $project_root/runs/agent_smoke/servers/$server_run/ready.json"
