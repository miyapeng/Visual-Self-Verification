#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 RUN_LABEL" >&2
  exit 2
fi

run_label=$1
if [[ ! "$run_label" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "RUN_LABEL must contain only letters, digits, dot, underscore, or dash" >&2
  exit 2
fi

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
python_bin=/data/miyapeng/miniconda3/envs/mmcode/bin/python
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
driver_source="$project_root/scripts/research_goal/interactweb_tool_loop.py"
prereg_source="$project_root/configs/research/interactweb_tool_loop_preregistration.md"
context_source="$project_root/configs/research/interactweb_tool_loop_contexts"
persistent_root="$project_root/runs/research/active_visual_verification/$run_label"
scratch_root=$(mktemp -d /tmp/mmcode-iwtool-repro.XXXXXX)
driver="$scratch_root/interactweb_tool_loop.py"
prereg="$scratch_root/interactweb_tool_loop_preregistration.md"
context_root="$scratch_root/public_context"
result_root="$scratch_root/results"
port=18035
case_ids=(
  interactweb-000045-navigation-repair
  interactweb-000062-question-bank-repair
)

if [[ -e "$persistent_root" ]]; then
  echo "refusing to overwrite existing run: $persistent_root" >&2
  exit 2
fi
mkdir -p "$persistent_root" "$context_root" "$result_root"
cp "$driver_source" "$driver"
cp "$prereg_source" "$prereg"
cp -a "$context_source"/. "$context_root"/
chmod 0444 "$driver" "$prereg"
find "$context_root" -type f -exec chmod 0444 {} +

atomic_copy() {
  local source=$1
  local destination=$2
  local partial="$persistent_root/.${destination}.partial.$$"
  [[ -s "$source" ]] || return 1
  cp "$source" "$partial"
  mv -f "$partial" "$persistent_root/$destination"
}

write_status() {
  local phase=$1
  local detail=${2:-}
  "$python_bin" - "$scratch_root/status.json" "$phase" "$detail" <<'PY'
import json
import os
import socket
import sys
from datetime import datetime, timezone

path, phase, detail = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "schema": "mmcode-tool-loop-job-2",
            "phase": phase,
            "detail": detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
        },
        handle,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    handle.write("\n")
PY
  atomic_copy "$scratch_root/status.json" status.json
}

server_pid=
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  [[ ! -s "$scratch_root/vllm.log" ]] || atomic_copy "$scratch_root/vllm.log" vllm.log || true
  for log in "$scratch_root"/*.driver.log; do
    [[ -s "$log" ]] || continue
    atomic_copy "$log" "$(basename "$log")" || true
  done
}
trap cleanup EXIT

sha256sum \
  "$driver" \
  "$prereg" \
  "$context_root"/cases/*/contexts/iteration-00.json \
  >"$scratch_root/frozen_hashes.sha256"
atomic_copy "$scratch_root/frozen_hashes.sha256" frozen_hashes.sha256
write_status frozen "source and public inputs copied to node-local scratch"

export XDG_CACHE_HOME="$scratch_root/cache"
export HF_HOME="$scratch_root/cache/huggingface"
export TORCHINDUCTOR_CACHE_DIR="$scratch_root/cache/torchinductor"
export TRITON_CACHE_DIR="$scratch_root/cache/triton"
export TMPDIR="$scratch_root/tmp"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.local/runtime/research/playwright"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
mkdir -p \
  "$XDG_CACHE_HOME" \
  "$HF_HOME" \
  "$TORCHINDUCTOR_CACHE_DIR" \
  "$TRITON_CACHE_DIR" \
  "$TMPDIR"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.92 \
  --max-model-len 32768 \
  --max-num-seqs 1 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  >"$scratch_root/vllm.log" 2>&1 &
server_pid=$!

ready=0
for _ in $(seq 1 240); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$scratch_root/models.json"; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    tail -120 "$scratch_root/vllm.log" >&2
    write_status server_failed "vLLM exited during startup"
    exit 1
  fi
  sleep 5
done
if [[ "$ready" -ne 1 ]]; then
  write_status server_failed "vLLM readiness timeout"
  exit 1
fi
atomic_copy "$scratch_root/models.json" models.json
write_status server_ready Qwen3.5-9B

summaries=0
for case_id in "${case_ids[@]}"; do
  case_root="$result_root/$case_id"
  case_tmp="$scratch_root/case-tmp/$case_id"
  case_home="$scratch_root/nobody-home/$case_id"
  mkdir -p "$case_root" "$case_tmp" "$case_home"
  chown -R 65534:65534 "$result_root" "$case_tmp" "$case_home"
  write_status case_running "$case_id"

  set +e
  env \
    HOME="$case_home" \
    XDG_CONFIG_HOME="$case_home/config" \
    MSWEA_GLOBAL_CONFIG_DIR="$case_home/mini-config" \
    MSWEA_SILENT_STARTUP=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$project_root/src" \
    PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
    TMPDIR="$case_tmp" \
    NO_PROXY=localhost,127.0.0.1 \
    no_proxy=localhost,127.0.0.1 \
    setpriv --reuid=65534 --regid=65534 --clear-groups \
    "$python_bin" "$driver" run \
      --case-id "$case_id" \
      --context-root "$context_root" \
      --output-root "$result_root" \
      --model Qwen3.5-9B \
      --base-url "http://127.0.0.1:$port/v1" \
      --steps 8 \
      --wall-time 900 \
      --command-timeout 120 \
      --max-tokens 4096 \
      >"$scratch_root/$case_id.driver.log" 2>&1
  driver_status=$?
  set -e

  if [[ -s "$case_root/summary.json" && -s "$case_root/compact-summary.json" ]]; then
    mkdir -p "$persistent_root/results/$case_id"
    rsync -a \
      --exclude='/workspace/node_modules/' \
      "$case_root"/ "$persistent_root/results/$case_id"/
    summaries=$((summaries + 1))
    write_status case_persisted "$case_id exit=$driver_status"
  else
    write_status case_failed "$case_id exit=$driver_status"
  fi
done

kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=

if [[ "$summaries" -ne 2 ]]; then
  write_status incomplete "$summaries/2 summaries"
  exit 1
fi
write_status complete "$summaries/2 summaries"
