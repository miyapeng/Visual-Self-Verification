#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
frontalk_job=mmc-frontalk-q35-full-t-0818
interact_job=mmc-iweb-q35-mini-pxy-0818

clean_clusterx() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
    no_proxy=compute.pjlab.org.cn,10.140.100.1 \
    clusterx "$@"
}

clean_clusterx run \
  --job-name "$frontalk_job" \
  --num-nodes 1 \
  --gpus-per-task 1 \
  --cpus-per-task 16 \
  --memory-per-task 160 \
  --shm-size-gib 32 \
  --no-env \
  bash "$project_root/scripts/native_benchmarks/run_frontalk_full_qwen35_job.sh"

clean_clusterx run \
  --job-name "$interact_job" \
  --num-nodes 1 \
  --gpus-per-task 1 \
  --cpus-per-task 16 \
  --memory-per-task 160 \
  --shm-size-gib 32 \
  --no-env \
  bash "$project_root/scripts/native_benchmarks/run_with_proxy.sh" \
  "$project_root/scripts/native_benchmarks/run_interactweb_mini_qwen35_job.sh"
