#!/usr/bin/env bash
set -uo pipefail

if [[ $# -lt 3 || $# -gt 5 ]]; then
    echo "usage: $0 INSTANCE_ID MODEL_NAME SERVER_RUN_NAME [MAX_OUTPUT_TOKENS] [STEP_LIMIT]" >&2
    exit 2
fi

instance_id=$1
model_name=$2
server_run=$3
max_tokens=${4:-4096}
step_limit=${5:-35}
project_root=/data/miyapeng/mmcode/MultimodalCode
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
output_root="$project_root/runs/agent_smoke/agents"
case_root="$output_root/$model_name/swe-mm/$instance_id"
job_log="$case_root/clusterx-job.log"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
eval_python=/data/miyapeng/miniconda3/envs/swemm/bin/python

mkdir -p "$case_root"
exec > >(tee -a "$job_log") 2>&1

echo "[swe-agent] started_at=$(date -u +%FT%TZ) instance=$instance_id model=$model_name"
if [[ ! -f $server_state ]]; then
    echo "[swe-agent] server state is missing: $server_state" >&2
    exit 1
fi

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

echo "[swe-agent] endpoint=$base_url"
curl -fsS "$base_url/models" >/dev/null || {
    echo "[swe-agent] model endpoint is unreachable" >&2
    exit 1
}

cd "$project_root"
"$python" agent_run.py run swe-mm "$instance_id" \
    --workspace /testbed \
    --model "$served_model" \
    --base-url "$base_url" \
    --api-key EMPTY \
    --output-root "$output_root" \
    --steps "$step_limit" \
    --wall-time 2400 \
    --command-timeout 180 \
    --temperature 0 \
    --max-tokens "$max_tokens"
agent_status=$?

patch="$output_root/$served_model/swe-mm/$instance_id/patch.diff"
eval_status=2
if [[ -s $patch ]]; then
    "$eval_python" scripts/swe_mm/direct_case_eval.py \
        --instance-id "$instance_id" \
        --patch "$patch" \
        --output-root "$project_root/runs/agent_smoke/evaluation/$served_model"
    eval_status=$?
else
    echo "[swe-agent] no non-empty patch was produced"
fi

"$python" scripts/agent_smoke/audit_trajectory.py \
    "$output_root/$served_model/swe-mm/$instance_id" || true

echo "[swe-agent] finished_at=$(date -u +%FT%TZ) agent_status=$agent_status eval_status=$eval_status"
exit "$agent_status"
