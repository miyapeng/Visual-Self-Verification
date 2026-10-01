#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES=""
export PLAYWRIGHT_MCP_SANDBOX=false
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

project_root=/data/miyapeng/mmcode/MultimodalCode
label=${1:-repeated_function_check_v1}
if [[ ! $label =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid output label: $label" >&2
    exit 2
fi

output="$project_root/runs/vision2web_self_verify/$label"
mkdir -p "$output"
exec > >(tee -a "$output/job.log") 2>&1

export PYTHONPATH="$project_root/src:$project_root/scaffolds/openhands/src"
python3.12 "$project_root/scripts/vision2web/smoke_repeated_function_check.py" \
    --output-root "$output"
