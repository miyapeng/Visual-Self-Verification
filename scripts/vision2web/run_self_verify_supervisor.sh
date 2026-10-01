#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
phase=${1:-smoke}
max_active=${2:-3}
run_label=${3:-qwen35-9b-openhands-selfverify-v2}
log_root="$project_root/runs/vision2web_generation/$run_label"
mkdir -p "$log_root"

exec /data/miyapeng/miniconda3/envs/mmcode/bin/python \
    "$project_root/scripts/vision2web/submit_self_verify.py" \
    --phase "$phase" \
    --max-active "$max_active" \
    --run-label "$run_label" \
    --poll-seconds 60 \
    2>&1 | tee -a "$log_root/controller-$phase.log"
