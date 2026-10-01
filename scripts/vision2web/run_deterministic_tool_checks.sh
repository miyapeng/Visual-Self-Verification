#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES=""
export PLAYWRIGHT_MCP_SANDBOX=false
# Browser-use launches Chrome and discovers its DevTools endpoint on loopback.
# ClusterX injects HTTP(S)_PROXY into the worker, so loopback must be excluded
# before the OpenHands deterministic browser probe starts.
export NO_PROXY="localhost,127.0.0.1,::1${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
server_run=${1:-qwen35-9b-v2w-official}
model=${2:-Qwen3.5-9B}
output_label=${3:-tool_checks_v1}
if [[ ! $output_label =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid output label: $output_label" >&2
    exit 2
fi
output="$project_root/runs/vision2web_self_verify/end_to_end/$output_label"
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
proxy_venv="$project_root/.venvs/vision2web-litellm-proxy-py312"
proxy_port=4000

mkdir -p "$output"
exec > >(tee -a "$output/clusterx-job.log") 2>&1

python3.12 "$project_root/evaluate/vision2web/run_clusterx.py" --preflight
python3.12 "$project_root/scripts/vision2web/probe_claude_code_runtime.py" \
    --output "$output/claude-runtime-probe.json"

PYTHONPATH="$project_root/src:$project_root/scaffolds/openhands/src" \
python3.12 "$project_root/scripts/vision2web/smoke_browser_interfaces.py" \
    --output "$output/openhands-browser-tool-check.json"

readarray -t server_info < <("$python" - "$server_state" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
print(record["endpoint"])
print(record["model"])
PY
)
base_url=${server_info[0]}
served_model=${server_info[1]}
if [[ $served_model != "$model" ]]; then
    echo "model mismatch: expected=$model actual=$served_model" >&2
    exit 1
fi
pod_host=${base_url#http://}
pod_host=${pod_host%%:*}
export NO_PROXY="localhost,127.0.0.1,::1,$pod_host"
export no_proxy="$NO_PROXY"
curl --noproxy '*' -fsS "$base_url/models" >/dev/null

proxy_config="$output/litellm-proxy.json"
cp "$project_root/scripts/vision2web/litellm_output_token_cap.py" \
    "$output/litellm_output_token_cap.py"
python3.12 "$project_root/scripts/vision2web/write_litellm_proxy_config.py" \
    --output "$proxy_config" --model-name "$served_model" --api-base "$base_url" \
    --max-input-tokens 262144 --max-output-tokens 8192
export MMCODE_PROXY_MAX_OUTPUT_TOKENS=8192
export MMCODE_PROXY_AUDIT_LOG="$output/litellm-request-audit.jsonl"
litellm_cli=$(command -v litellm)
"$proxy_venv/bin/python" "$litellm_cli" --config "$proxy_config" \
    --host 127.0.0.1 --port "$proxy_port" >"$output/litellm-proxy.log" 2>&1 &
proxy_pid=$!
cleanup() {
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
}
trap cleanup EXIT
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$proxy_port/v1/models" >/dev/null; then
        break
    fi
    sleep 2
done
curl -fsS "http://127.0.0.1:$proxy_port/v1/models" >/dev/null

PYTHONPATH="$project_root/src" "$python" \
    "$project_root/scripts/vision2web/smoke_claude_png_context.py" \
    --output-root "$output/claude-png-context" \
    --model "$served_model" \
    --base-url "http://127.0.0.1:$proxy_port"
