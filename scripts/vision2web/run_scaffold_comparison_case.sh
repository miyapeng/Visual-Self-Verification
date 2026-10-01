#!/usr/bin/env bash
set -uo pipefail

# Run one real Vision2Web coding-agent session in the pinned task container.
# The harness exposes tools and records events; it does not force deployment,
# browsing, repair, replay, or submission.

if [[ $# -lt 6 || $# -gt 7 ]]; then
    echo "usage: $0 CASE_ID FRAMEWORK MODEL_NAME BACKEND RUN_LABEL MODE [SERVER_RUN]" >&2
    exit 2
fi

case_id=$1
framework=$2
model_name=$3
backend=$4
run_label=$5
mode=$6
server_run=${7:-}

case "$framework" in
    openhands|claude_code) ;;
    *) echo "invalid framework: $framework" >&2; exit 2 ;;
esac
case "$backend" in
    local|relay|pjlab) ;;
    *) echo "invalid backend: $backend" >&2; exit 2 ;;
esac
case "$mode" in
    official|browser_enabled|guided_vsv) ;;
    tools)
        if [[ $framework == claude_code ]]; then
            mode=official
        else
            mode=browser_enabled
        fi
        ;;
    self_verify) mode=guided_vsv ;;
    *) echo "invalid Vision2Web mode: $mode" >&2; exit 2 ;;
esac
if [[ $framework == claude_code && $mode == browser_enabled ]]; then
    echo "claude_code browser_enabled duplicates official; use official or guided_vsv" >&2
    exit 2
fi
if [[ $backend == local && -z $server_run ]]; then
    echo "local backend requires SERVER_RUN" >&2
    exit 2
fi
if [[ $backend == relay && -z ${LLM_RELAY_API_KEY:-} ]]; then
    echo "LLM_RELAY_API_KEY is absent from the worker environment" >&2
    exit 2
fi
if [[ $backend == pjlab && -z ${PJLAB_TOKEN_API_KEY:-} ]]; then
    echo "PJLAB_TOKEN_API_KEY is absent from the worker environment" >&2
    exit 2
fi

export CUDA_VISIBLE_DEVICES=""
export PLAYWRIGHT_MCP_SANDBOX=false

project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
# Qwen3.8 has a 262,144-token total sequence budget.  Reserve the 32K output
# requested by Claude Code rather than advertising the full sequence length as
# input capacity as the previous compatibility proxy did.
image_model_context=229376
relay_model_context=200000
pjlab_model_context=200000
local_model_max_output=32768
relay_model_max_output=8192
model_max_output=$relay_model_max_output
wall_time=${MMCODE_AGENT_WALL_TIME:-7200}
if [[ ! $wall_time =~ ^[1-9][0-9]*$ ]]; then
    echo "invalid MMCODE_AGENT_WALL_TIME: $wall_time" >&2
    exit 2
fi
proxy_port=4000
proxy_venv="$project_root/.venvs/vision2web-litellm-proxy-py312"
run_root="$project_root/runs/vision2web_scaffold_comparison/$run_label"
output_root="$run_root/agents"
case_safe=${case_id//\//__}
log_root="$run_root/jobs/$mode/$model_name/$framework/$case_safe"
extensions_source="$project_root/scaffolds/openhands/extensions"
extensions_commit=75924ab2dd9b1efe7eecff6aaa8034cca099354e
skills_repo="${HOME:-/root}/.openhands/cache/skills/public-skills"
mkdir -p "$log_root"
exec > >(tee -a "$log_root/clusterx-job.log") 2>&1

echo "[comparison] started_at=$(date -u +%FT%TZ) case=$case_id framework=$framework model=$model_name backend=$backend"
echo "[comparison] mode=$mode image=frozen-vision2web evaluation=false forced_loop=false"

python3.12 "$project_root/evaluate/vision2web/run_clusterx.py" --preflight || exit $?

if [[ $framework == openhands ]]; then
    if [[ ! -d $extensions_source/.git ]]; then
        echo "frozen OpenHands extensions are missing: $extensions_source" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$skills_repo")"
    if [[ -e $skills_repo && ! -d $skills_repo/.git ]]; then
        echo "invalid OpenHands skills cache: $skills_repo" >&2
        exit 1
    fi
    if [[ ! -d $skills_repo/.git ]]; then
        git clone --branch main --single-branch --no-tags "$extensions_source" "$skills_repo"
    fi
    git -C "$skills_repo" remote set-url origin "$extensions_source"
    git -C "$skills_repo" fetch --no-tags origin main
    git -C "$skills_repo" checkout -q main
    git -C "$skills_repo" reset --hard "$extensions_commit" >/dev/null
    actual_extensions_commit=$(git -C "$skills_repo" rev-parse HEAD)
    if [[ $actual_extensions_commit != "$extensions_commit" ]]; then
        echo "OpenHands extensions mismatch: $actual_extensions_commit" >&2
        exit 1
    fi
    export EXTENSIONS_REF=main
else
    python3.12 "$project_root/scripts/vision2web/probe_claude_code_runtime.py" \
        --output "$log_root/claude-runtime-probe.json" || exit $?
fi

proxy_pid=""
cleanup_proxy() {
    if [[ -n $proxy_pid ]]; then
        kill "$proxy_pid" 2>/dev/null || true
        wait "$proxy_pid" 2>/dev/null || true
    fi
}
trap cleanup_proxy EXIT

supports_vision=true
model_context=$relay_model_context
upstream_base=""
agent_base=""
agent_model="$model_name"
agent_key="${LLM_RELAY_API_KEY:-EMPTY}"
need_proxy=false

if [[ $backend == local ]]; then
    server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
    if [[ ! -f $server_state ]]; then
        echo "server state is missing: $server_state" >&2
        exit 1
    fi
    readarray -t server_info < <("$python" - "$server_state" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["endpoint"])
print(d["model"])
PY
    )
    upstream_base=${server_info[0]}
    served_model=${server_info[1]}
    if [[ $served_model != "$model_name" ]]; then
        echo "endpoint model mismatch: expected=$model_name actual=$served_model" >&2
        exit 1
    fi
    pod_host=${upstream_base#http://}
    pod_host=${pod_host%%:*}
    export NO_PROXY="localhost,127.0.0.1,$pod_host"
    export no_proxy="$NO_PROXY"
    curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 \
        "$upstream_base/models" >/dev/null || exit 1
    model_context=$image_model_context
    model_max_output=$local_model_max_output
    agent_key=EMPTY
    if [[ $framework == openhands ]]; then
        need_proxy=true
    else
        # vLLM 0.25 exposes the native Anthropic Messages endpoint.  Claude
        # Code can therefore keep Anthropic image, tool, effort, and thinking
        # semantics without an Anthropic -> OpenAI -> Anthropic conversion.
        agent_base=${upstream_base%/v1}
        need_proxy=false
        export MMCODE_CLAUDE_CODE_EFFORT_LEVEL=xhigh
    fi
elif [[ $framework == openhands ]]; then
    if [[ $backend == pjlab ]]; then
        unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
        export NO_PROXY="localhost,127.0.0.1,token.pjlab.org.cn"
        export no_proxy="$NO_PROXY"
        model_context=$pjlab_model_context
        upstream_base=https://token.pjlab.org.cn/v1
        agent_key=$PJLAB_TOKEN_API_KEY
    else
        upstream_base=http://35.220.164.252:3888/v1
    fi
    need_proxy=true
else
    # Claude Code's Anthropic SDK appends /v1/messages itself.
    if [[ $backend == pjlab ]]; then
        unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
        export NO_PROXY="localhost,127.0.0.1,token.pjlab.org.cn"
        export no_proxy="$NO_PROXY"
        model_context=$pjlab_model_context
        agent_base=https://token.pjlab.org.cn
        agent_key=$PJLAB_TOKEN_API_KEY
    else
        agent_base=http://35.220.164.252:3888
    fi
fi

# The relay's glm-5.2 route passed tool-calling probes but not image-perception
# probes. Keep it as an explicitly text-only diagnostic rather than claiming
# that screenshots reached a visual model.
if [[ $model_name == glm-5.2 ]]; then
    supports_vision=false
fi

if [[ $need_proxy == true ]]; then
    if [[ ! -x $proxy_venv/bin/litellm ]]; then
        echo "pinned LiteLLM proxy environment is missing: $proxy_venv" >&2
        exit 1
    fi
    proxy_config="$log_root/litellm-proxy.json"
    cp "$project_root/scripts/vision2web/litellm_output_token_cap.py" \
        "$log_root/litellm_output_token_cap.py"
    proxy_args=(
        --output "$proxy_config"
        --model-name "$model_name"
        --api-base "$upstream_base"
        --supports-vision "$supports_vision"
        --max-input-tokens "$model_context"
        --max-output-tokens "$model_max_output"
    )
    if [[ $backend == relay ]]; then
        proxy_args+=(--api-key-env LLM_RELAY_API_KEY)
    elif [[ $backend == pjlab ]]; then
        proxy_args+=(--api-key-env PJLAB_TOKEN_API_KEY)
    else
        proxy_args+=(--api-key EMPTY)
    fi
    python3.12 "$project_root/scripts/vision2web/write_litellm_proxy_config.py" \
        "${proxy_args[@]}"
    export MMCODE_PROXY_MAX_OUTPUT_TOKENS="$model_max_output"
    export MMCODE_PROXY_AUDIT_LOG="$log_root/litellm-request-audit.jsonl"
    "$proxy_venv/bin/litellm" \
        --config "$proxy_config" --host 127.0.0.1 --port "$proxy_port" \
        >"$log_root/litellm-proxy.log" 2>&1 &
    proxy_pid=$!
    proxy_base="http://127.0.0.1:$proxy_port"
    for _ in $(seq 1 90); do
        if curl -fsS "$proxy_base/v1/models" >/dev/null; then
            break
        fi
        if ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "LiteLLM proxy exited during startup" >&2
            tail -100 "$log_root/litellm-proxy.log" || true
            exit 1
        fi
        sleep 2
    done
    curl -fsS "$proxy_base/v1/models" >/dev/null || exit 1
    agent_key=EMPTY
    if [[ $framework == openhands ]]; then
        # The litellm_proxy provider expects the proxy origin.  It appends the
        # OpenAI route itself, while OpenHands separately queries
        # <origin>/v1/model/info to confirm visual capability.
        agent_base="$proxy_base"
        agent_model="litellm_proxy/$model_name"
    else
        agent_base="$proxy_base"
    fi
fi

if [[ $framework == openhands ]]; then
    run_dir="$output_root/litellm_proxy__$model_name/vision2web/$mode/$case_safe"
else
    run_dir="$output_root/$model_name/vision2web/claude_code/$mode/$case_safe"
fi

cd "$project_root"
common_args=(
    run vision2web "$case_id"
    --workspace /workspace
    --model "$agent_model"
    --base-url "$agent_base"
    --api-key "$agent_key"
    --output-root "$output_root"
    --vision2web-mode "$mode"
    --wall-time "$wall_time"
)
if [[ $framework == openhands ]]; then
    vision_requirement=(--openhands-require-vision)
    if [[ $supports_vision != true ]]; then
        vision_requirement=(--no-openhands-require-vision)
    fi
    "$python" agent_run.py "${common_args[@]}" \
        --vision2web-framework openhands \
        --openhands-profile official \
        --openhands-max-retries 0 \
        "${vision_requirement[@]}"
else
    "$python" agent_run.py "${common_args[@]}" \
        --vision2web-framework claude_code \
        --claude-max-retries 0
fi
agent_status=$?

"$python" "$project_root/scripts/agent_smoke/audit_trajectory.py" "$run_dir" || true

python3.12 - "$run_dir/comparison-summary.json" "$run_dir/result.json" \
    "$case_id" "$framework" "$model_name" "$backend" "$supports_vision" \
    "$agent_status" "$mode" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

summary_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
result = json.loads(result_path.read_text()) if result_path.is_file() else {}
summary = {
    "schema": "vision2web-scaffold-model-comparison-1",
    "case_id": sys.argv[3],
    "framework": sys.argv[4],
    "model": sys.argv[5],
    "backend": sys.argv[6],
    "endpoint_image_probe_passed": sys.argv[7].casefold() == "true",
    "mode": sys.argv[9],
    "forced_loop": False,
    "agent_exit_status": int(sys.argv[8]),
    "result_status": result.get("status", "missing"),
    "evaluation_performed": False,
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False))
PY

echo "[comparison] finished_at=$(date -u +%FT%TZ) status=$agent_status run_dir=$run_dir"
exit "$agent_status"
