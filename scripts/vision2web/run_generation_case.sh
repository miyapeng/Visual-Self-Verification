#!/usr/bin/env bash
set -uo pipefail

# Case workers are CPU-only. OpenHands/Claude Code and Chrome call the shared
# recorded vLLM endpoint and must never consume a local accelerator.
export CUDA_VISIBLE_DEVICES=""
# The frozen Vision2Web image runs workers as root.  Its native
# playwright-cli supports this official MCP setting; without it Chromium
# exits before opening any page.  Apply the same execution compatibility
# setting to every experimental condition.
export PLAYWRIGHT_MCP_SANDBOX=false

if [[ $# -lt 4 || $# -gt 8 ]]; then
    echo "usage: $0 CASE_ID MODEL_NAME SERVER_RUN RUN_LABEL [official|browser_enabled|guided_vsv] [openhands|claude_code] [standard|pilot20] [WALL_TIME_SECONDS: default 7200; 0 disables Claude Code task timeout]" >&2
    exit 2
fi

case_id=$1
model_name=$2
server_run=$3
run_label=$4
vision2web_mode=${5:-official}
vision2web_framework=${6:-openhands}
vision2web_protocol=${7:-standard}
wall_time=${8:-7200}
if [[ ! $wall_time =~ ^[0-9]+$ ]]; then
    echo "WALL_TIME_SECONDS must be a non-negative integer" >&2
    exit 2
fi
if [[ $wall_time == 0 && $vision2web_framework != claude_code ]]; then
    echo "unlimited task time is currently supported only for claude_code" >&2
    exit 2
fi
case "$vision2web_framework" in
    openhands|claude_code) ;;
    *) echo "invalid Vision2Web framework: $vision2web_framework" >&2; exit 2 ;;
esac
case "$vision2web_mode" in
    official|browser_enabled|guided_vsv) ;;
    tools)
        if [[ $vision2web_framework == claude_code ]]; then
            vision2web_mode=official
        else
            vision2web_mode=browser_enabled
        fi
        ;;
    self_verify) vision2web_mode=guided_vsv ;;
    *) echo "invalid Vision2Web mode: $vision2web_mode" >&2; exit 2 ;;
esac
if [[ $vision2web_framework == claude_code && $vision2web_mode == browser_enabled ]]; then
    echo "claude_code browser_enabled duplicates official; use official or guided_vsv" >&2
    exit 2
fi
case "$vision2web_protocol" in
    standard|pilot20) ;;
    *) echo "invalid Vision2Web protocol: $vision2web_protocol" >&2; exit 2 ;;
esac
if [[ $vision2web_protocol == pilot20 && ( $vision2web_framework != claude_code || $vision2web_mode != official ) ]]; then
    echo "pilot20 currently requires claude_code official (the Natural condition)" >&2
    exit 2
fi
project_root=/data/miyapeng/mmcode/MultimodalCode
runtime_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
server_state="$project_root/runs/agent_smoke/servers/$server_run/ready.json"
run_root="$project_root/runs/vision2web_generation/$run_label"
output_root="$run_root/agents"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
proxy_port=4000
model_context=229376
model_max_output=32768
proxy_venv="$project_root/.venvs/vision2web-litellm-proxy-py312"
case_safe=${case_id//\//__}
log_root="$run_root/jobs/$vision2web_mode/$case_safe"
if [[ $vision2web_framework == claude_code ]]; then
    run_dir="$output_root/$model_name/vision2web/claude_code/$vision2web_mode/$case_safe"
else
    run_dir="$output_root/litellm_proxy__$model_name/vision2web/$vision2web_mode/$case_safe"
fi
case_summary="$run_dir/generation-summary.json"
extensions_source="$runtime_root/scaffolds/openhands/extensions"
extensions_commit=75924ab2dd9b1efe7eecff6aaa8034cca099354e
skills_repo="${HOME:-/root}/.openhands/cache/skills/public-skills"

mkdir -p "$log_root"
exec > >(tee -a "$log_root/clusterx-job.log") 2>&1

echo "[vision2web-generation] started_at=$(date -u +%FT%TZ) case=$case_id model=$model_name"
echo "[vision2web-generation] runtime=$runtime_root framework=$vision2web_framework profile=official mode=$vision2web_mode evaluation=false"
echo "[vision2web-generation] protocol=$vision2web_protocol"
echo "[vision2web-generation] wall_time_seconds=$wall_time (0 means no task deadline)"

python3.12 "$runtime_root/evaluate/vision2web/run_clusterx.py" --preflight || exit $?

# The installed official OpenHands CLI loads the public OpenHands/extensions
# catalog on startup. Seed the exact frozen upstream commit into its canonical
# cache and point the cache's origin at the local snapshot. OpenHands still
# executes its unmodified discovery/update path, but case execution no longer
# depends on mutable GitHub main or ClusterX outbound-network availability.
if [[ $vision2web_framework == openhands ]]; then
    if [[ ! -d $extensions_source/.git ]]; then
        echo "[vision2web-generation] frozen OpenHands extensions are missing: $extensions_source" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$skills_repo")"
    if [[ -e $skills_repo && ! -d $skills_repo/.git ]]; then
        echo "[vision2web-generation] invalid OpenHands skills cache: $skills_repo" >&2
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
        echo "[vision2web-generation] OpenHands extensions mismatch: $actual_extensions_commit" >&2
        exit 1
    fi
    export EXTENSIONS_REF=main
    echo "[vision2web-generation] openhands_extensions=$actual_extensions_commit source=frozen-local"
else
    python3.12 "$runtime_root/scripts/vision2web/probe_claude_code_runtime.py" \
        --output "$log_root/claude-runtime-probe.json" || exit $?
fi

if [[ ! -f $server_state ]]; then
    echo "[vision2web-generation] server state is missing: $server_state" >&2
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
    echo "[vision2web-generation] endpoint model mismatch: expected=$model_name actual=$served_model" >&2
    exit 1
fi
pod_host=${base_url#http://}
pod_host=${pod_host%%:*}
export NO_PROXY="localhost,127.0.0.1,$pod_host"
export no_proxy="$NO_PROXY"
curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 "$base_url/models" >/dev/null || {
    echo "[vision2web-generation] model endpoint is unreachable: $base_url" >&2
    exit 1
}

proxy_pid=""
cleanup_proxy() {
    if [[ -n $proxy_pid ]]; then
        kill "$proxy_pid" 2>/dev/null || true
        wait "$proxy_pid" 2>/dev/null || true
    fi
}
trap cleanup_proxy EXIT

if [[ $vision2web_framework == claude_code ]]; then
    # vLLM exposes a native Anthropic Messages endpoint. Claude Code's SDK
    # appends /v1/messages, so pass the server origin and avoid protocol
    # conversion through LiteLLM.
    agent_base=${base_url%/v1}
    # Qwen3.8 supports xhigh/medium/low rather than Claude Code's generic
    # "high" value. xhigh is Qwen3.8's documented default and preserves the
    # model's official thinking behavior without changing the task prompt.
    if [[ $model_name == Qwen3.8-* ]]; then
        export MMCODE_CLAUDE_CODE_EFFORT_LEVEL=xhigh
    fi
else
    proxy_config="$log_root/litellm-proxy.json"
    cp "$runtime_root/scripts/vision2web/litellm_output_token_cap.py" \
        "$log_root/litellm_output_token_cap.py"
    python3.12 "$runtime_root/scripts/vision2web/write_litellm_proxy_config.py" \
        --output "$proxy_config" \
        --model-name "$served_model" \
        --api-base "$base_url" \
        --max-input-tokens "$model_context" \
        --max-output-tokens "$model_max_output"
    export MMCODE_PROXY_MAX_OUTPUT_TOKENS="$model_max_output"
    export MMCODE_PROXY_AUDIT_LOG="$log_root/litellm-request-audit.jsonl"

    if ! "$proxy_venv/bin/python" -c \
        'import apscheduler, litellm; from fastapi.dependencies.utils import get_flat_dependant' \
        >/dev/null 2>&1; then
        echo "[vision2web-generation] preparing isolated LiteLLM proxy environment"
        python3.12 -m venv --system-site-packages "$proxy_venv"
        "$proxy_venv/bin/python" -m pip install --disable-pip-version-check \
            'litellm[proxy]==1.79.0' 'fastapi==0.115.14'
    fi
    litellm_cli=$(command -v litellm)
    "$proxy_venv/bin/python" "$litellm_cli" \
        --config "$proxy_config" --host 127.0.0.1 --port "$proxy_port" \
        >"$log_root/litellm-proxy.log" 2>&1 &
    proxy_pid=$!
    agent_base="http://127.0.0.1:$proxy_port"
    for _ in $(seq 1 60); do
        if curl -fsS "$agent_base/v1/models" >/dev/null; then
            break
        fi
        if ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "[vision2web-generation] LiteLLM proxy exited during startup" >&2
            exit 1
        fi
        sleep 2
    done
    curl -fsS "$agent_base/v1/models" >/dev/null || exit 1
fi

cd "$runtime_root"
if [[ $vision2web_framework == claude_code ]]; then
    "$python" agent_run.py run vision2web "$case_id" \
        --workspace /workspace \
        --model "$served_model" \
        --base-url "$agent_base" \
        --api-key EMPTY \
        --output-root "$output_root" \
        --vision2web-framework claude_code \
        --vision2web-mode "$vision2web_mode" \
        --claude-max-retries 0 \
        --wall-time "$wall_time"
else
    "$python" agent_run.py run vision2web "$case_id" \
        --workspace /workspace \
        --model "litellm_proxy/$served_model" \
        --base-url "$agent_base" \
        --api-key EMPTY \
        --output-root "$output_root" \
        --vision2web-framework openhands \
        --openhands-profile official \
        --vision2web-mode "$vision2web_mode" \
        --openhands-max-retries 2 \
        --wall-time "$wall_time"
fi
agent_status=$?

same_context_status="not_requested"
same_context_output=""
if [[ $vision2web_protocol == pilot20 && $agent_status -eq 0 ]]; then
    same_context_output="$run_dir/same_context_self_check"
    echo "[vision2web-generation] resuming the Natural Claude session for the frozen structured self-check"
    PYTHONPATH="$runtime_root/src" \
    "$python" "$runtime_root/scripts/vision2web/continue_claude_same_context.py" \
        "$case_id" \
        --workspace /workspace \
        --natural-trajectory "$run_dir/claude.events.jsonl" \
        --output "$same_context_output" \
        --model "$served_model" \
        --base-url "$proxy_base" \
        --api-key EMPTY \
        --timeout 1800
    followup_exit=$?
    if [[ $followup_exit -eq 0 ]]; then
        same_context_status="success"
    else
        same_context_status="failed"
    fi
    echo "[vision2web-generation] same_context_status=$same_context_status exit=$followup_exit"
elif [[ $vision2web_protocol == pilot20 ]]; then
    same_context_status="skipped_natural_failed"
fi

"$python" "$runtime_root/scripts/agent_smoke/audit_trajectory.py" "$run_dir" || true

# Executability is a diagnostic only. Official functional/visual evaluation is
# deliberately deferred and is not approximated here.
if [[ -f /workspace/start.sh ]]; then
    timeout 180 bash /workspace/start.sh >"$run_dir/start-validation.log" 2>&1 &
    server_pid=$!
    http_status=0
    for _ in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:3000/ >/dev/null; then
            http_status=200
            break
        fi
        sleep 2
    done
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    python3.12 - "$run_dir/start-validation.json" "$http_status" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
path, http_status = Path(sys.argv[1]), int(sys.argv[2])
record = {
    "http_status": http_status,
    "ready": http_status == 200,
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "diagnostic_only": True,
}
path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record))
PY
else
    echo "[vision2web-generation] no generated start.sh"
fi

python3.12 - "$case_summary" "$run_dir/result.json" "$case_id" "$model_name" "$agent_status" "$vision2web_mode" "$vision2web_framework" "$vision2web_protocol" "$same_context_status" "$same_context_output" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
summary_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
case_id, model, agent_status = sys.argv[3], sys.argv[4], int(sys.argv[5])
result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
summary = {
    "case_id": case_id,
    "model": model,
    "profile": "official",
    "mode": sys.argv[6] if len(sys.argv) > 6 else "official",
    "framework": sys.argv[7] if len(sys.argv) > 7 else "openhands",
    "protocol": sys.argv[8] if len(sys.argv) > 8 else "standard",
    "same_context_status": sys.argv[9] if len(sys.argv) > 9 else "not_requested",
    "same_context_output": sys.argv[10] if len(sys.argv) > 10 else "",
    "agent_exit_status": agent_status,
    "result_status": result.get("status", "missing"),
    "evaluation_performed": False,
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
PY

echo "[vision2web-generation] finished_at=$(date -u +%FT%TZ) agent_status=$agent_status"
exit "$agent_status"
