#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

if [[ $# -ne 3 ]]; then
  echo "usage: $0 RUN_LABEL CREDENTIALS_FILE JOB_TAG" >&2
  echo "JOB_TAG must be 2-8 lowercase letters/digits and makes job names unique." >&2
  exit 2
fi

project_root=/data/miyapeng/mmcode/MultimodalCode
interact_python=/data/miyapeng/miniconda3/envs/interactweb/bin/python
run_label=$1
credentials_file=$2
job_tag=$3
run_root="$project_root/runs/native_benchmarks/$run_label"

if [[ ! $run_label =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "RUN_LABEL contains unsafe characters: $run_label" >&2
  exit 2
fi
if [[ ! $job_tag =~ ^[a-z0-9]{2,8}$ ]]; then
  echo "invalid JOB_TAG: $job_tag" >&2
  exit 2
fi
if [[ ! -f $credentials_file ]]; then
  echo "credentials file not found: $credentials_file" >&2
  exit 2
fi
credentials_file=$(realpath "$credentials_file")
credential_mode=$(stat -c '%a' "$credentials_file")
if (( (8#$credential_mode & 077) != 0 )); then
  echo "credentials file must be chmod 600 (current mode=$credential_mode)" >&2
  exit 2
fi
if [[ -e $run_root ]]; then
  echo "refusing duplicate submission: run root already exists: $run_root" >&2
  exit 1
fi

# Fail before reserving eight GPUs unless both the released text simulator and
# the released multimodal judge model names work on the supplied endpoints.
"$interact_python" "$project_root/scripts/interactweb_bench/check_official_apis.py" \
  --credentials-file "$credentials_file"

mkdir -p "$run_root"

clean_clusterx() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
    no_proxy=compute.pjlab.org.cn,10.140.100.1 \
    clusterx "$@"
}

for shard in $(seq 0 7); do
  shard_tag=$(printf '%02d' "$shard")
  job="mmc-iwoff-q35-s${shard_tag}-${job_tag}"
  echo "[submit] shard=$shard_tag job=$job"
  clean_clusterx run \
    --job-name "$job" \
    --num-nodes 1 \
    --gpus-per-task 1 \
    --cpus-per-task 16 \
    --memory-per-task 160 \
    --shm-size-gib 32 \
    --no-env \
    -e "INTERACTWEB_CREDENTIALS_FILE=$credentials_file" \
    bash "$project_root/scripts/native_benchmarks/run_with_proxy.sh" \
    "$project_root/scripts/native_benchmarks/run_interactweb_official_full_shard_qwen35_job.sh" \
    "$shard_tag" "$run_label"
done
