#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
num_shards=8

clean_clusterx() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
    no_proxy=compute.pjlab.org.cn,10.140.100.1 \
    clusterx "$@"
}

for shard in $(seq 0 $((num_shards - 1))); do
  shard_tag=$(printf '%02d' "$shard")
  job="mmc-iweb-q35-f-s${shard_tag}-0820"
  echo "[submit] shard=$shard_tag job=$job"
  clean_clusterx run \
    --job-name "$job" \
    --num-nodes 1 \
    --gpus-per-task 1 \
    --cpus-per-task 16 \
    --memory-per-task 160 \
    --shm-size-gib 32 \
    --no-env \
    bash "$project_root/scripts/native_benchmarks/run_with_proxy.sh" \
    "$project_root/scripts/native_benchmarks/run_interactweb_full_shard_qwen35_job.sh" \
    "$shard_tag"
done
