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
case_input_root="$project_root/runs/research/self_verify_public_cases-004"
case_manifest="$case_input_root/cases.jsonl"
interact_freeze_provenance="$project_root/runs/research/interact_first_runnable_programs-001/provenance.json"
vision_dependency_root="$project_root/.runtime/research/vision2web_dependencies/57fd10d9b1bc13644edfd7585e4da89beb38efcc0208e575279a907e0ff07317"
persistent_root="$project_root/runs/research/$run_label"
scratch_root=$(mktemp -d /tmp/mmcode-same-policy.XXXXXX)
frozen_project="$scratch_root/project"
result_root="$scratch_root/results"
port=18037

if [[ -e "$persistent_root" ]]; then
  echo "refusing to overwrite existing run: $persistent_root" >&2
  exit 2
fi
if [[ ! -s "$case_manifest" ]]; then
  echo "missing frozen case manifest: $case_manifest" >&2
  exit 2
fi
for required_path in \
  "$project_root/src/multimodalcode" \
  "$project_root/self_verify_run.py" \
  "$project_root/scripts/interactive_judge_playwright.js" \
  "$project_root/scripts/research_goal/materialize_self_verify_cases.py" \
  "$project_root/scripts/research_goal/preflight_self_verify_runtimes.py" \
  "$project_root/tests/test_self_verify.py" \
  "$vision_dependency_root/package.json" \
  "$vision_dependency_root/package-lock.json" \
  "$vision_dependency_root/node_modules.sha256" \
  "$case_input_root/provenance.json" \
  "$interact_freeze_provenance"; do
  if [[ ! -e "$required_path" ]]; then
    echo "missing frozen smoke input: $required_path" >&2
    exit 2
  fi
done

mkdir -p \
  "$persistent_root" \
  "$frozen_project/src" \
  "$frozen_project/scripts/research_goal" \
  "$frozen_project/tests" \
  "$result_root"
exec >>"$persistent_root/job.log" 2>&1
echo "[job] started run_label=$run_label host=$(hostname)"
cp -a "$project_root/src/multimodalcode" "$frozen_project/src/"
cp "$project_root/self_verify_run.py" "$frozen_project/"
cp "$project_root/scripts/interactive_judge_playwright.js" "$frozen_project/scripts/"
cp "$project_root/scripts/research_goal/materialize_self_verify_cases.py" \
  "$frozen_project/scripts/research_goal/"
cp "$project_root/scripts/research_goal/preflight_self_verify_runtimes.py" \
  "$frozen_project/scripts/research_goal/"
cp "$project_root/tests/test_self_verify.py" "$frozen_project/tests/"
cp "$case_manifest" "$scratch_root/source-cases.jsonl"
cp "$case_input_root/provenance.json" \
  "$scratch_root/case-provenance.json"
cp "$interact_freeze_provenance" "$scratch_root/interact-freeze-provenance.json"

"$python_bin" "$frozen_project/scripts/research_goal/materialize_self_verify_cases.py" \
  --cases "$scratch_root/source-cases.jsonl" \
  --copy-dependencies \
  --output-dir "$scratch_root/materialized-cases" \
  >"$scratch_root/materialization.log"

atomic_copy() {
  local source=$1
  local destination=$2
  local partial="$persistent_root/.${destination}.partial.$$"
  [[ -e "$source" ]] || return 1
  cp -a "$source" "$partial"
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
            "schema": "multimodalcode-same-policy-smoke-job-1",
            "phase": phase,
            "detail": detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
    handle.write("\n")
PY
  atomic_copy "$scratch_root/status.json" status.json
}

server_pid=
sync_pid=
on_error() {
  local return_code=$1
  local line_number=$2
  trap - ERR
  set +e
  write_status script_failed "exit=$return_code line=$line_number" || true
  return "$return_code"
}
cleanup() {
  if [[ -n "$sync_pid" ]]; then
    kill "$sync_pid" 2>/dev/null || true
    wait "$sync_pid" 2>/dev/null || true
  fi
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  [[ ! -s "$scratch_root/vllm.log" ]] || atomic_copy "$scratch_root/vllm.log" vllm.log || true
  [[ ! -s "$scratch_root/browser-tests.log" ]] || atomic_copy "$scratch_root/browser-tests.log" browser-tests.log || true
  [[ ! -s "$scratch_root/runtime-preflight.log" ]] || atomic_copy "$scratch_root/runtime-preflight.log" runtime-preflight.log || true
  [[ ! -s "$scratch_root/runner.log" ]] || atomic_copy "$scratch_root/runner.log" runner.log || true
}
trap cleanup EXIT
trap 'on_error $? $LINENO' ERR

sha256sum \
  "$project_root/scripts/research_goal/run_same_policy_smoke_qwen35_job.sh" \
  "$frozen_project/self_verify_run.py" \
  "$frozen_project/src/multimodalcode/research/self_verify.py" \
  "$frozen_project/src/multimodalcode/research/events.py" \
  "$frozen_project/src/multimodalcode/research/interactive.py" \
  "$frozen_project/src/multimodalcode/research/schema.py" \
  "$frozen_project/src/multimodalcode/research/tools.py" \
  "$frozen_project/scripts/interactive_judge_playwright.js" \
  "$frozen_project/scripts/research_goal/materialize_self_verify_cases.py" \
  "$frozen_project/scripts/research_goal/preflight_self_verify_runtimes.py" \
  "$vision_dependency_root/package.json" \
  "$vision_dependency_root/package-lock.json" \
  "$vision_dependency_root/node_modules.sha256" \
  "$scratch_root/source-cases.jsonl" \
  "$scratch_root/case-provenance.json" \
  "$scratch_root/interact-freeze-provenance.json" \
  "$scratch_root/materialized-cases/cases.jsonl" \
  >"$scratch_root/frozen_hashes.sha256"
atomic_copy "$scratch_root/frozen_hashes.sha256" frozen_hashes.sha256
atomic_copy "$scratch_root/case-provenance.json" case-provenance.json
atomic_copy "$scratch_root/interact-freeze-provenance.json" interact-freeze-provenance.json
atomic_copy "$scratch_root/materialized-cases/provenance.json" materialization-provenance.json
atomic_copy "$scratch_root/materialization.log" materialization.log
write_status frozen "code and leakage-safe public inputs copied to node-local scratch"

export XDG_CACHE_HOME="$scratch_root/cache"
export HF_HOME="$scratch_root/cache/huggingface"
export TORCHINDUCTOR_CACHE_DIR="$scratch_root/cache/torchinductor"
export TRITON_CACHE_DIR="$scratch_root/cache/triton"
export TMPDIR="$scratch_root/tmp"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.runtime/research/playwright"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
mkdir -p \
  "$XDG_CACHE_HOME" \
  "$HF_HOME" \
  "$TORCHINDUCTOR_CACHE_DIR" \
  "$TRITON_CACHE_DIR" \
  "$TMPDIR"

browser_home="$scratch_root/browser-home"
browser_tmp="$scratch_root/browser-tmp"
mkdir -p "$browser_home" "$browser_tmp"
chmod 0755 "$scratch_root"
chmod -R a+rX "$frozen_project"
chown -R 65534:65534 \
  "$browser_home" \
  "$browser_tmp" \
  "$result_root"
cd "$frozen_project"
set +e
env \
  HOME="$browser_home" \
  TMPDIR="$browser_tmp" \
  PYTHONPATH="$frozen_project/src" \
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  MMCODE_RUN_BROWSER_TESTS=1 \
  NO_PROXY=localhost,127.0.0.1 \
  no_proxy=localhost,127.0.0.1 \
  setpriv --reuid=65534 --regid=65534 --clear-groups \
  "$python_bin" -m unittest -v tests.test_self_verify.MechanicalBrowserTests \
  >"$scratch_root/browser-tests.log" 2>&1
browser_status=$?
set -e
atomic_copy "$scratch_root/browser-tests.log" browser-tests.log
if [[ "$browser_status" -ne 0 ]]; then
  write_status browser_tests_failed "exit=$browser_status"
  exit "$browser_status"
fi
write_status browser_tests_passed "reset isolation, offline resources, and action failure recording"

runtime_preflight_root="$scratch_root/runtime-preflight"
mkdir -p "$runtime_preflight_root"
chown -R 65534:65534 "$runtime_preflight_root"
set +e
env \
  HOME="$browser_home" \
  TMPDIR="$browser_tmp" \
  PYTHONPATH="$frozen_project/src" \
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  NO_PROXY=localhost,127.0.0.1 \
  no_proxy=localhost,127.0.0.1 \
  setpriv --reuid=65534 --regid=65534 --clear-groups \
  "$python_bin" "$frozen_project/scripts/research_goal/preflight_self_verify_runtimes.py" \
    --cases "$scratch_root/materialized-cases/cases.jsonl" \
    --output-root "$runtime_preflight_root" \
    --browser-timeout-ms 10000 \
    >"$scratch_root/runtime-preflight.log" 2>&1
runtime_preflight_status=$?
set -e
atomic_copy "$scratch_root/runtime-preflight.log" runtime-preflight.log
if [[ -s "$runtime_preflight_root/summary.json" ]]; then
  atomic_copy "$runtime_preflight_root/summary.json" runtime-preflight.json
fi
if [[ "$runtime_preflight_status" -ne 0 ]]; then
  write_status runtime_preflight_failed "one or more frozen public runtimes failed"
  exit "$runtime_preflight_status"
fi
write_status runtime_preflight_passed "all six frozen public runtimes started under UID 65534"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.92 \
  --max-model-len 131072 \
  --max-num-seqs 1 \
  --enforce-eager \
  >"$scratch_root/vllm.log" 2>&1 &
server_pid=$!

ready=0
for _ in $(seq 1 240); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$scratch_root/models.json"; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    write_status server_failed "vLLM exited during startup"
    exit 1
  fi
  sleep 5
done
if [[ "$ready" -ne 1 ]]; then
  write_status server_failed "vLLM readiness timeout"
  exit 1
fi
write_status server_responding "GET /v1/models succeeded"
atomic_copy "$scratch_root/models.json" models.json
write_status server_ready "Qwen3.5-9B; one frozen policy for all semantic stages"

runner_home="$scratch_root/runner-home"
runner_tmp="$scratch_root/runner-tmp"
mkdir -p "$runner_home" "$runner_tmp"
chown -R 65534:65534 "$runner_home" "$runner_tmp" "$result_root"
mkdir -p "$persistent_root/results"
write_status runner_started "six leakage-safe cases; periodic persistence enabled"
(
  while true; do
    sleep 30
    chmod -R a+rX "$result_root" 2>/dev/null || true
    rsync -a --delay-updates \
      --exclude='/case_runs/*/cases/*/workspace/node_modules/' \
      "$result_root"/ "$persistent_root/results"/ || true
  done
) &
sync_pid=$!
set +e
env \
  HOME="$runner_home" \
  TMPDIR="$runner_tmp" \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$frozen_project/src" \
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  NO_PROXY=localhost,127.0.0.1 \
  no_proxy=localhost,127.0.0.1 \
  setpriv --reuid=65534 --regid=65534 --clear-groups \
  "$python_bin" "$frozen_project/self_verify_run.py" \
    --cases "$scratch_root/materialized-cases/cases.jsonl" \
    --output-root "$result_root" \
    --enable-self-verification \
    --backend vllm \
    --model Qwen3.5-9B \
    --base-url "http://127.0.0.1:$port/v1" \
    --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' \
    --timeout 600 \
    --max-tokens 4096 \
    --temperature 0 \
    --seed 0 \
    --max-model-calls 10 \
    --max-browser-actions 72 \
    --max-revisions 2 \
    --max-schema-retries 1 \
    --max-checks 6 \
    --max-actions-per-check 8 \
    --max-evidence-images 6 \
    --max-patch-edits 4 \
    --max-patch-chars 24000 \
    --browser-timeout-ms 10000 \
    --no-video \
    >"$scratch_root/runner.log" 2>&1
runner_status=$?
set -e

kill "$sync_pid" 2>/dev/null || true
wait "$sync_pid" 2>/dev/null || true
sync_pid=
chmod -R a+rX "$result_root"
rsync -a --delay-updates --exclude='/case_runs/*/cases/*/workspace/node_modules/' \
  "$result_root"/ "$persistent_root/results"/
atomic_copy "$scratch_root/runner.log" runner.log
kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=

if [[ ! -s "$persistent_root/results/summary.json" ]]; then
  write_status incomplete "runner exit=$runner_status; summary missing"
  exit 1
fi
write_status complete "runner exit=$runner_status; six smoke cases persisted"
exit "$runner_status"
