#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

if [[ $# -ne 3 ]]; then
  echo "usage: $0 RUN_LABEL FULL_RUN_LABEL CLUSTERX_JOB_NAME" >&2
  exit 2
fi
run_label=$1
full_run_label=$2
job_name=$3
project_root=/data/miyapeng/mmcode/MultimodalCode

for value in "$run_label" "$full_run_label"; do
  if [[ ! "$value" =~ ^[a-zA-Z0-9._-]+$ ]]; then
    echo "run labels contain unsafe characters" >&2
    exit 2
  fi
done
if [[ ! "$job_name" =~ ^[a-z0-9-]+$ || ${#job_name} -gt 32 ]]; then
  echo "CLUSTERX_JOB_NAME must be <=32 lowercase letters, digits, and dashes" >&2
  exit 2
fi
if [[ -e "$project_root/runs/research/$run_label" ]]; then
  echo "refusing to overwrite existing run label: $run_label" >&2
  exit 2
fi

env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
  no_proxy=compute.pjlab.org.cn,10.140.100.1 \
  clusterx run \
    --job-name "$job_name" --num-nodes 1 --gpus-per-task 1 \
    --cpus-per-task 16 --memory-per-task 180 --shm-size-gib 32 --no-env \
    bash "$project_root/scripts/research_goal/run_controlled_comparison_qwen35_job.sh" \
    "$run_label" "$full_run_label"
