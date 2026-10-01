#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
output=${1:-$project_root/runs/vision2web_self_verify/probes/browser_interface_smoke_final.json}

cd "$project_root"
export PYTHONPATH="$project_root/src:$project_root/scaffolds/openhands/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3.12 scripts/vision2web/smoke_browser_interfaces.py --output "$output"
