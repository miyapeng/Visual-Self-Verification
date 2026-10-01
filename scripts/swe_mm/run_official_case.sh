#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 INSTANCE_ID MODEL_NAME SERVER_RUN RUN_LABEL" >&2
    exit 2
fi

instance_id=$1
model_name=$2
server_run=$3
run_label=$4
project_root=/data/miyapeng/mmcode/MultimodalCode
runtime_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
eval_python=/data/miyapeng/miniconda3/envs/swemm/bin/python
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
run_root="$project_root/runs/swe_mm_official/$run_label"
agent_root="$run_root/agents"
evaluation_root="$run_root/evaluation/$model_name"
case_root="$agent_root/$model_name/swe-mm/$instance_id"
job_log="$case_root/clusterx-job.log"
case_summary="$case_root/clusterx-summary.json"

mkdir -p "$case_root" "$evaluation_root"
exec > >(tee -a "$job_log") 2>&1

echo "[official-swe-mm] started_at=$(date -u +%FT%TZ) instance=$instance_id model=$model_name"
echo "[official-swe-mm] mini_profile=swebench-official native_tools=true steps=250 command_timeout=60"
echo "[official-swe-mm] evaluator_timeout=1800"

if [[ ! -f $server_state ]]; then
    echo "[official-swe-mm] server state is missing: $server_state" >&2
    exit 1
fi

readarray -t server_info < <("$python" - "$server_state" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["endpoint"])
print(d["model"])
PY
)
base_url=${server_info[0]}
served_model=${server_info[1]}
if [[ $served_model != "$model_name" ]]; then
    echo "[official-swe-mm] endpoint model mismatch: expected=$model_name actual=$served_model" >&2
    exit 1
fi
pod_host=${base_url#http://}
pod_host=${pod_host%%:*}
export NO_PROXY="localhost,127.0.0.1,$pod_host"
export no_proxy="$NO_PROXY"
# The model is local and has no public price entry. Avoid a mutable network
# fetch and use LiteLLM's packaged map; cost tracking remains the upstream $3
# limit but evaluates to zero for these self-hosted model names.
export LITELLM_LOCAL_MODEL_COST_MAP=True

curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 "$base_url/models" >/dev/null || {
    echo "[official-swe-mm] model endpoint is unreachable: $base_url" >&2
    exit 1
}

cd "$runtime_root"
"$python" "$runtime_root/agent_run.py" run swe-mm "$instance_id" \
    --workspace /testbed \
    --model "$served_model" \
    --base-url "$base_url" \
    --api-key EMPTY \
    --output-root "$agent_root" \
    --mini-profile swebench-official \
    --tool-mode native
agent_status=$?

patch="$case_root/patch.diff"
eval_status=2
if [[ -s $patch ]]; then
    "$eval_python" "$runtime_root/scripts/swe_mm/direct_case_eval.py" \
        --instance-id "$instance_id" \
        --patch "$patch" \
        --output-root "$evaluation_root" \
        --timeout 1800
    eval_status=$?
else
    echo "[official-swe-mm] no non-empty patch was produced"
fi

"$python" "$runtime_root/scripts/agent_smoke/audit_trajectory.py" "$case_root" || true
"$python" - "$case_summary" "$instance_id" "$model_name" "$agent_status" "$eval_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, instance_id, model, agent_status, eval_status = sys.argv[1:]
Path(path).write_text(json.dumps({
    "instance_id": instance_id,
    "model": model,
    "profile": "swebench-official",
    "agent_status": int(agent_status),
    "evaluation_status": int(eval_status),
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY

echo "[official-swe-mm] finished_at=$(date -u +%FT%TZ) agent_status=$agent_status eval_status=$eval_status"
# An unresolved model prediction is a completed benchmark case, not a broken
# ClusterX job. Infrastructure failures above still exit nonzero.
exit 0
