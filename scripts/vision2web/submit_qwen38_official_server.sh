#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
launcher="$project_root/scripts/agent_smoke/start_vllm_server.sh"
job_name=${1:-mmc-q38-27b-v2w-v2}
server_run=${2:-qwen38-27b-v2w-official-v2}
port=${3:-18004}

# Qwen3.8-27B official vLLM semantics on two A800-80G GPUs.  TP=2 is a
# capacity/throughput choice; parser, reasoning separation, and native 262K
# context match the model's serving recommendations.  Prefix caching remains
# off because vLLM 0.25.1 marks it experimental for this hybrid Mamba model.
clusterx run \
    --job-name "$job_name" \
    --num-nodes 1 \
    --gpus-per-task 2 \
    --cpus-per-task 32 \
    --memory-per-task 320 \
    --shm-size-gib 32 \
    --no-env \
    bash "$launcher" \
    /data/miyapeng/model/Qwen3.8-27B Qwen3.8-27B "$port" \
    "$server_run" qwen3_coder 262144 2 3 qwen3 false

echo "Server state: $project_root/runs/agent_smoke/servers/$server_run/ready.json"
