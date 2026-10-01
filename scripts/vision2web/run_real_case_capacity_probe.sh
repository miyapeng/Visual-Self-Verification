#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES=""
export PLAYWRIGHT_MCP_SANDBOX=false
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

project_root=/data/miyapeng/mmcode/MultimodalCode
label=${1:-newsday_real_case_capacity_v1}
config_name=${2:-capacity_probe_newsday.json}
if [[ ! $label =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid output label: $label" >&2
    exit 2
fi
if [[ ! $config_name =~ ^[A-Za-z0-9._-]+\.json$ ]]; then
    echo "invalid config name: $config_name" >&2
    exit 2
fi

output="$project_root/runs/vision2web_self_verify/real_case_capacity/$label"
mkdir -p "$output"
exec > >(tee -a "$output/job.log") 2>&1

export PYTHONPATH="$project_root/src:$project_root/scaffolds/openhands/src"
python3.12 "$project_root/scripts/vision2web/run_real_case_capacity_probe.py" \
    --project-root "$project_root" \
    --config "$project_root/configs/vision2web/$config_name" \
    --output-root "$output" \
    --workspace /workspace
