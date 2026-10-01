#!/usr/bin/env bash
set -uo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
outer_log="$project_root/runs/agent_smoke/jobs/vision2web/clusterx-outer.log"
mkdir -p "$(dirname "$outer_log")"
exec > >(tee -a "$outer_log") 2>&1
set -x

id
pwd
ls -ld /data /workspace "$project_root"
command -v bash
command -v python3.12
command -v node

exec bash -x "$project_root/scripts/agent_smoke/run_vision2web_case.sh" "$@"
