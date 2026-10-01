#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
job_name=mmc-native-q35-smoke-0817
job_script="$project_root/scripts/native_benchmarks/run_qwen35_smoke_job.sh"

env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
  no_proxy=compute.pjlab.org.cn,10.140.100.1 \
  clusterx run \
    --job-name "$job_name" \
    --num-nodes 1 \
    --gpus-per-task 1 \
    --cpus-per-task 16 \
    --memory-per-task 160 \
    --shm-size-gib 32 \
    --no-env \
    bash "$job_script"
