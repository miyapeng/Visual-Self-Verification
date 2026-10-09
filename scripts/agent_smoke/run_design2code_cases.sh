#!/usr/bin/env bash
set -uo pipefail

if [[ $# -lt 3 ]]; then
    echo "usage: $0 MODEL_NAME SERVER_RUN_NAME CASE [CASE ...]" >&2
    exit 2
fi

model_name=$1
server_run=$2
shift 2
project_root=/data/miyapeng/mmcode/MultimodalCode
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
output_root="$project_root/runs/agent_smoke/agents"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
log_root="$project_root/runs/agent_smoke/jobs/design2code/$model_name"
mkdir -p "$log_root"
exec > >(tee -a "$log_root/clusterx-job.log") 2>&1

readarray -t server_info < <("$python" - "$server_state" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
print(d['endpoint'])
print(d['model'])
PY
)
base_url=${server_info[0]}
served_model=${server_info[1]}
pod_host=${base_url#http://}
pod_host=${pod_host%%:*}
export NO_PROXY="localhost,127.0.0.1,$pod_host"
export no_proxy="$NO_PROXY"
curl -fsS "$base_url/models" >/dev/null

cd "$project_root"
overall=0
for case_id in "$@"; do
    echo "[design-agent] case=$case_id model=$served_model started_at=$(date -u +%FT%TZ)"
    "$python" scripts/agents/run.py run design2code "$case_id" \
        --model "$served_model" \
        --base-url "$base_url" \
        --api-key EMPTY \
        --output-root "$output_root" \
        --steps 25 \
        --wall-time 1800 \
        --command-timeout 180 \
        --temperature 0 \
        --max-tokens 4096
    status=$?
    if (( status != 0 )); then overall=$status; fi
    "$python" scripts/agent_smoke/audit_trajectory.py \
        "$output_root/$served_model/design2code/$case_id" || true
done
exit "$overall"
