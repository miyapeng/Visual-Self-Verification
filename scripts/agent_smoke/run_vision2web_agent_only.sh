#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 CASE_ID MODEL_NAME SERVER_RUN OUTPUT_ROOT WALL_TIME" >&2
    exit 2
fi

case_id=$1
model_name=$2
server_run=$3
output_root=$4
wall_time=$5
project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
proxy_port=${VISION_LITELLM_PORT:-4000}
model_context=${VISION_MODEL_CONTEXT:-262144}
model_max_output=${VISION_MODEL_MAX_OUTPUT:-8192}
proxy_venv=${VISION_LITELLM_VENV:-$project_root/.local/venvs/vision2web-litellm-proxy-py312}
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
case_safe=${case_id//\//__}

readarray -t server_info < <("$python" - "$server_state" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["endpoint"])
print(d["model"])
PY
)
base_url=${server_info[0]}
served_model=${server_info[1]}
pod_host=${base_url#http://}
pod_host=${pod_host%%:*}
export NO_PROXY="localhost,127.0.0.1,$pod_host"
export no_proxy="$NO_PROXY"

python3.12 "$project_root/evaluate/vision2web/run_clusterx.py" --preflight
curl -fsS "$base_url/models" >/dev/null
proxy_config="/tmp/vision2web-litellm-$case_safe.json"
python3.12 "$project_root/scripts/vision2web/write_litellm_proxy_config.py" \
    --output "$proxy_config" \
    --model-name "$served_model" \
    --api-base "$base_url" \
    --max-input-tokens "$model_context" \
    --max-output-tokens "$model_max_output"
if ! "$proxy_venv/bin/python" -c \
    'import apscheduler, litellm; from fastapi.dependencies.utils import get_flat_dependant' \
    >/dev/null 2>&1; then
    python3.12 -m venv --system-site-packages "$proxy_venv"
    "$proxy_venv/bin/python" -m pip install --disable-pip-version-check \
        'litellm[proxy]==1.79.0' 'fastapi==0.115.14'
fi
litellm_cli=$(command -v litellm)
"$proxy_venv/bin/python" "$litellm_cli" \
    --config "$proxy_config" --host 127.0.0.1 --port "$proxy_port" \
    >"/tmp/vision2web-litellm-$case_safe.log" 2>&1 &
proxy_pid=$!
cleanup_proxy() {
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
}
trap cleanup_proxy EXIT
proxy_base="http://127.0.0.1:$proxy_port"
for _ in $(seq 1 60); do
    if curl -fsS "$proxy_base/v1/models" >/dev/null; then break; fi
    if ! kill -0 "$proxy_pid" 2>/dev/null; then exit 1; fi
    sleep 2
done
curl -fsS "$proxy_base/v1/models" >/dev/null
cd "$project_root"
"$python" scripts/agents/run.py run vision2web "$case_id" \
    --workspace /workspace \
    --model "litellm_proxy/$served_model" \
    --base-url "$proxy_base" \
    --api-key EMPTY \
    --output-root "$output_root" \
    --openhands-profile official \
    --openhands-max-retries 2 \
    --wall-time "$wall_time" \
    --force
"$python" scripts/agent_smoke/audit_trajectory.py \
    "$output_root/litellm_proxy__$served_model/vision2web/official/$case_safe"
