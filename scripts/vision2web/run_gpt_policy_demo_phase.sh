#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES=""
export PLAYWRIGHT_MCP_SANDBOX=false
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

project_root=/data/miyapeng/mmcode/MultimodalCode
demo_root="$project_root/runs/vision2web_self_verify/gpt_policy_demo/smartrecruiters_clean_v1"
phase=${1:?phase label is required}
plan_name=${2:-}
if [[ ! $phase =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "invalid phase label: $phase" >&2
  exit 2
fi

args=(
  --source-workspace "$demo_root/workspace"
  --output-root "$demo_root/phases/$phase"
  --workspace /workspace
)
if [[ -n $plan_name ]]; then
  args+=(--plan "$demo_root/plans/$plan_name")
fi

mkdir -p "$demo_root/phases/$phase"
exec > >(tee -a "$demo_root/phases/$phase/job.log") 2>&1
export PYTHONPATH="$project_root/src:$project_root/scaffolds/openhands/src"
python3.12 "$project_root/scripts/vision2web/run_gpt_policy_demo_phase.py" "${args[@]}"
