#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
input_dir="$project_root/runs/research/active_visual_verification/decision-probe-002-rule-control"
output_dir="$input_dir/qwen35-9b-run-001-no-thinking"
job_name=mmc-ctxrule-q35-0821

clean_clusterx() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
    no_proxy=compute.pjlab.org.cn,10.140.100.1 \
    clusterx "$@"
}

clean_clusterx run \
  --job-name "$job_name" \
  --num-nodes 1 \
  --gpus-per-task 1 \
  --cpus-per-task 16 \
  --memory-per-task 160 \
  --shm-size-gib 32 \
  --no-env \
  bash "$project_root/scripts/research_goal/run_decision_probe_qwen35_job.sh" \
  "$input_dir" "$output_dir"
