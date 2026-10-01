#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 CASE_ID MODEL_NAME SERVER_RUN_NAME" >&2
    exit 2
fi

case_id=$1
model_name=$2
server_run=$3
project_root=/data/miyapeng/mmcode/MultimodalCode
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
output_root="$project_root/runs/agent_smoke/agents"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
wall_time=${VISION_WALL_TIME:-7200}
force=${VISION_FORCE:-0}
proxy_port=${VISION_LITELLM_PORT:-4000}
model_context=${VISION_MODEL_CONTEXT:-262144}
model_max_output=${VISION_MODEL_MAX_OUTPUT:-8192}
proxy_venv=${VISION_LITELLM_VENV:-$project_root/.venvs/vision2web-litellm-proxy-py312}
case_safe=${case_id//\//__}
log_root="$project_root/runs/agent_smoke/jobs/vision2web/$model_name/$case_safe"
mkdir -p "$log_root"
exec > >(tee -a "$log_root/clusterx-job.log") 2>&1

echo "[vision-agent] preflight"
python3.12 "$project_root/evaluate/vision2web/run_clusterx.py" --preflight
preflight_status=$?
if (( preflight_status != 0 )); then exit "$preflight_status"; fi

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

proxy_config="$log_root/litellm-proxy.json"
python3.12 "$project_root/scripts/vision2web/write_litellm_proxy_config.py" \
    --output "$proxy_config" \
    --model-name "$served_model" \
    --api-base "$base_url" \
    --max-input-tokens "$model_context" \
    --max-output-tokens "$model_max_output"

# The upstream Vision2Web image installs OpenHands (and therefore LiteLLM's
# SDK), but not LiteLLM's optional proxy dependencies.  Keep those additions
# in a separate Python 3.12 venv so the frozen OpenHands environment remains
# untouched.  The same venv is reused by later ClusterX case containers.
if ! "$proxy_venv/bin/python" -c \
    'import apscheduler, litellm; from fastapi.dependencies.utils import get_flat_dependant' \
    >/dev/null 2>&1; then
    echo "[vision-agent] preparing isolated LiteLLM proxy environment"
    python3.12 -m venv --system-site-packages "$proxy_venv"
    "$proxy_venv/bin/python" -m pip install --disable-pip-version-check \
        'litellm[proxy]==1.79.0' 'fastapi==0.115.14'
fi
litellm_cli=$(command -v litellm)
"$proxy_venv/bin/python" "$litellm_cli" \
    --config "$proxy_config" --host 127.0.0.1 --port "$proxy_port" \
    >"$log_root/litellm-proxy.log" 2>&1 &
proxy_pid=$!
cleanup_proxy() {
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
}
trap cleanup_proxy EXIT
proxy_base="http://127.0.0.1:$proxy_port"
for _ in $(seq 1 60); do
    if curl -fsS "$proxy_base/v1/models" >/dev/null; then
        break
    fi
    if ! kill -0 "$proxy_pid" 2>/dev/null; then
        echo "[vision-agent] LiteLLM proxy exited during startup" >&2
        exit 1
    fi
    sleep 2
done
curl -fsS "$proxy_base/v1/models" >/dev/null

cd "$project_root"
agent_args=(
    agent_run.py run vision2web "$case_id"
    --workspace /workspace
    --model "litellm_proxy/$served_model"
    --base-url "$proxy_base"
    --api-key EMPTY
    --output-root "$output_root"
    --openhands-profile official
    --openhands-max-retries 2
    --wall-time "$wall_time"
)
if [[ $force == 1 ]]; then
    agent_args+=(--force)
fi
"$python" "${agent_args[@]}"
agent_status=$?

"$python" scripts/agent_smoke/audit_trajectory.py \
    "$output_root/litellm_proxy__$served_model/vision2web/official/$case_safe" || true

if [[ -f /workspace/start.sh ]]; then
    timeout 180 bash /workspace/start.sh >"$log_root/start.log" 2>&1 &
    server_pid=$!
    ready=false
    for _ in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:3000 >"$log_root/homepage.html"; then
            ready=true
            break
        fi
        sleep 2
    done
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    echo "[vision-agent] start_sh_ready=$ready"
else
    echo "[vision-agent] no executable start.sh"
fi

exit "$agent_status"
