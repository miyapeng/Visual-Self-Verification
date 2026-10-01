#!/usr/bin/env bash
set -euo pipefail

# The two Qwen checkpoints both declare a native 262,144-token context.  Two
# A800 GPUs per replica keep the KV cache and weights within memory while using
# exactly four GPUs for the two-model experiment.
project_root=/data/miyapeng/mmcode/MultimodalCode
launcher="$project_root/scripts/agent_smoke/start_vllm_server.sh"
context=262144
tp=2
max_num_seqs=2

clusterx run \
    --job-name mmcode-swm-q35-262k \
    --num-nodes 1 \
    --gpus-per-task 2 \
    --cpus-per-task 32 \
    --memory-per-task 320 \
    --shm-size-gib 32 \
    --no-env \
    bash "$launcher" \
    /data/miyapeng/model/Qwen3.5-9B Qwen3.5-9B 18002 \
    qwen35-9b-swe-262k qwen3_xml "$context" "$tp" "$max_num_seqs"

clusterx run \
    --job-name mmcode-swm-q3vl30-262k \
    --num-nodes 1 \
    --gpus-per-task 2 \
    --cpus-per-task 32 \
    --memory-per-task 320 \
    --shm-size-gib 32 \
    --no-env \
    bash "$launcher" \
    /data/miyapeng/model/Qwen3-VL-30B-A3B Qwen3-VL-30B-A3B 18002 \
    qwen3vl-30b-swe-262k hermes "$context" "$tp" "$max_num_seqs"

echo "Submitted both native-context vLLM services."
echo "Ready files:"
echo "  runs/agent_smoke/servers/qwen35-9b-swe-262k/ready.json"
echo "  runs/agent_smoke/servers/qwen3vl-30b-swe-262k/ready.json"
