#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 RUN_LABEL FULL_RUN_LABEL" >&2
  exit 2
fi
run_label=$1
full_run_label=$2
for value in "$run_label" "$full_run_label"; do
  if [[ ! "$value" =~ ^[a-zA-Z0-9._-]+$ ]]; then
    echo "run labels contain unsafe characters" >&2
    exit 2
  fi
done

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
python_bin=/data/miyapeng/miniconda3/envs/mmcode/bin/python
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
case_input_root="$project_root/runs/research/self_verify_public_cases-004"
source_cases="$case_input_root/cases.jsonl"
source_provenance="$case_input_root/provenance.json"
interact_freeze_provenance="$project_root/runs/research/interact_first_runnable_programs-001/provenance.json"
vision_dependency_root="$project_root/.runtime/research/vision2web_dependencies/57fd10d9b1bc13644edfd7585e4da89beb38efcc0208e575279a907e0ff07317"
full_run="$project_root/runs/research/$full_run_label"
persistent_root="$project_root/runs/research/$run_label"
scratch_root=$(mktemp -d /tmp/mmcode-controlled-comparison.XXXXXX)
frozen_project="$scratch_root/project"
result_root="$scratch_root/results"
full_run_snapshot="$scratch_root/full-run-snapshot"
port=18037

if [[ -e "$persistent_root" ]]; then
  echo "refusing to overwrite existing run: $persistent_root" >&2
  exit 2
fi
if [[ ! -s "$full_run/results/summary.json" ]]; then
  echo "full-method summary is missing: $full_run" >&2
  exit 2
fi
if [[ ! -s "$source_cases" || ! -s "$source_provenance" || ! -s "$interact_freeze_provenance" ]]; then
  echo "frozen public case input is incomplete: $case_input_root" >&2
  exit 2
fi
mkdir -p "$persistent_root" "$frozen_project/src" "$frozen_project/scripts/research_goal" "$result_root" "$full_run_snapshot"
exec >>"$persistent_root/job.log" 2>&1

cp -a "$project_root/src/multimodalcode" "$frozen_project/src/"
cp "$project_root/scripts/interactive_judge_playwright.js" "$frozen_project/scripts/"
cp "$project_root/scripts/research_goal/materialize_self_verify_cases.py" "$frozen_project/scripts/research_goal/"
cp "$project_root/scripts/research_goal/preflight_self_verify_runtimes.py" "$frozen_project/scripts/research_goal/"
cp "$project_root/scripts/research_goal/compare_self_verify_conditions.py" "$frozen_project/scripts/research_goal/"
cp "$source_cases" "$scratch_root/source-cases.jsonl"
cp "$source_provenance" "$scratch_root/case-provenance.json"
cp "$interact_freeze_provenance" "$scratch_root/interact-freeze-provenance.json"
cp -a "$full_run/results" "$full_run_snapshot/"
(
  cd "$full_run_snapshot"
  find results -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
) >"$scratch_root/full-run-input-hashes.sha256"
"$python_bin" "$frozen_project/scripts/research_goal/materialize_self_verify_cases.py" \
  --cases "$scratch_root/source-cases.jsonl" \
  --copy-dependencies \
  --output-dir "$scratch_root/materialized-cases" \
  >"$scratch_root/materialization.log"

atomic_copy() {
  local source=$1 destination=$2 partial="$persistent_root/.${2}.partial.$$"
  [[ -e "$source" ]] || return 1
  cp -a "$source" "$partial"
  mv -f "$partial" "$persistent_root/$destination"
}
write_status() {
  local phase=$1 detail=${2:-}
  "$python_bin" - "$scratch_root/status.json" "$phase" "$detail" <<'PY'
import json, os, socket, sys
from datetime import datetime, timezone
path, phase, detail = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"schema":"multimodalcode-controlled-comparison-job-1","phase":phase,
               "detail":detail,"updated_at":datetime.now(timezone.utc).isoformat(),
               "host":socket.gethostname(),"pid":os.getpid()}, handle, indent=2)
    handle.write("\n")
PY
  atomic_copy "$scratch_root/status.json" status.json
}

server_pid=
sync_pid=
cleanup() {
  set +e
  if [[ -n "$sync_pid" ]]; then kill "$sync_pid" 2>/dev/null; wait "$sync_pid" 2>/dev/null; fi
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null; wait "$server_pid" 2>/dev/null; fi
  [[ ! -s "$scratch_root/vllm.log" ]] || atomic_copy "$scratch_root/vllm.log" vllm.log
  [[ ! -s "$scratch_root/runner.log" ]] || atomic_copy "$scratch_root/runner.log" runner.log
  [[ ! -s "$scratch_root/runtime-preflight.log" ]] || atomic_copy "$scratch_root/runtime-preflight.log" runtime-preflight.log
}
trap cleanup EXIT

sha256sum \
  "$project_root/scripts/research_goal/run_controlled_comparison_qwen35_job.sh" \
  "$frozen_project/scripts/research_goal/compare_self_verify_conditions.py" \
  "$frozen_project/scripts/research_goal/materialize_self_verify_cases.py" \
  "$frozen_project/scripts/research_goal/preflight_self_verify_runtimes.py" \
  "$frozen_project/src/multimodalcode/research/self_verify.py" \
  "$frozen_project/src/multimodalcode/research/events.py" \
  "$frozen_project/src/multimodalcode/research/interactive.py" \
  "$frozen_project/src/multimodalcode/research/schema.py" \
  "$frozen_project/src/multimodalcode/research/tools.py" \
  "$frozen_project/scripts/interactive_judge_playwright.js" \
  "$vision_dependency_root/package.json" \
  "$vision_dependency_root/package-lock.json" \
  "$vision_dependency_root/node_modules.sha256" \
  "$scratch_root/materialized-cases/cases.jsonl" \
  "$scratch_root/case-provenance.json" \
  "$scratch_root/interact-freeze-provenance.json" \
  "$full_run_snapshot/results/summary.json" \
  "$scratch_root/full-run-input-hashes.sha256" \
  >"$scratch_root/frozen_hashes.sha256"
atomic_copy "$scratch_root/frozen_hashes.sha256" frozen_hashes.sha256
atomic_copy "$scratch_root/case-provenance.json" case-provenance.json
atomic_copy "$scratch_root/interact-freeze-provenance.json" interact-freeze-provenance.json
atomic_copy "$scratch_root/full-run-input-hashes.sha256" full-run-input-hashes.sha256
atomic_copy "$scratch_root/materialization.log" materialization.log
write_status frozen "comparison code, public cases, and complete full-run inputs frozen"

export XDG_CACHE_HOME="$scratch_root/cache"
export HF_HOME="$scratch_root/cache/huggingface"
export TORCHINDUCTOR_CACHE_DIR="$scratch_root/cache/torchinductor"
export TRITON_CACHE_DIR="$scratch_root/cache/triton"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.runtime/research/playwright"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
mkdir -p "$XDG_CACHE_HOME" "$HF_HOME" "$TORCHINDUCTOR_CACHE_DIR" "$TRITON_CACHE_DIR"

browser_home="$scratch_root/browser-home"
browser_tmp="$scratch_root/browser-tmp"
mkdir -p "$browser_home" "$browser_tmp"
chmod 0755 "$scratch_root"
chmod -R a+rX "$frozen_project" "$scratch_root/materialized-cases" "$full_run_snapshot"
chown -R 65534:65534 "$browser_home" "$browser_tmp" "$result_root"

runtime_preflight_root="$scratch_root/runtime-preflight"
mkdir -p "$runtime_preflight_root"
chown -R 65534:65534 "$runtime_preflight_root"
set +e
env HOME="$browser_home" TMPDIR="$browser_tmp" PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$frozen_project/src" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
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
  --host 0.0.0.0 --port "$port" --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.92 --max-model-len 131072 --max-num-seqs 1 --enforce-eager \
  >"$scratch_root/vllm.log" 2>&1 &
server_pid=$!
ready=0
for _ in $(seq 1 240); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$scratch_root/models.json"; then ready=1; break; fi
  if ! kill -0 "$server_pid" 2>/dev/null; then write_status server_failed "vLLM exited"; exit 1; fi
  sleep 5
done
if [[ "$ready" -ne 1 ]]; then write_status server_failed "readiness timeout"; exit 1; fi
atomic_copy "$scratch_root/models.json" models.json
write_status runner_started "baseline/generic/full diagnostic; official evaluator excluded"

mkdir -p "$persistent_root/results"
(
  while true; do
    sleep 30
    chmod -R a+rX "$result_root" 2>/dev/null || true
    rsync -a --delay-updates --exclude='/cases/*/conditions/*/workspace/node_modules/' \
      "$result_root"/ "$persistent_root/results"/ || true
  done
) &
sync_pid=$!
set +e
env HOME="$browser_home" TMPDIR="$browser_tmp" PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$frozen_project/src" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  setpriv --reuid=65534 --regid=65534 --clear-groups \
  "$python_bin" "$frozen_project/scripts/research_goal/compare_self_verify_conditions.py" \
    --cases "$scratch_root/materialized-cases/cases.jsonl" \
    --full-run "$full_run_snapshot" \
    --source-full-run-label "$full_run_label" \
    --output-root "$result_root" \
    --backend vllm --model Qwen3.5-9B --base-url "http://127.0.0.1:$port/v1" \
    --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' \
    --timeout 600 --max-tokens 4096 --temperature 0 --seed 0 \
    --max-schema-retries 1 --max-checks 6 --max-actions-per-check 8 \
    --max-evidence-images 6 --max-patch-edits 4 --max-patch-chars 24000 \
    --browser-timeout-ms 10000 \
    >"$scratch_root/runner.log" 2>&1
runner_status=$?
set -e

kill "$sync_pid" 2>/dev/null || true
wait "$sync_pid" 2>/dev/null || true
sync_pid=
chmod -R a+rX "$result_root"
rsync -a --delay-updates --exclude='/cases/*/conditions/*/workspace/node_modules/' \
  "$result_root"/ "$persistent_root/results"/
atomic_copy "$scratch_root/runner.log" runner.log
if [[ ! -s "$persistent_root/results/summary.json" ]]; then
  write_status incomplete "runner exit=$runner_status; summary missing"
  exit 1
fi
write_status complete "runner exit=$runner_status; controlled comparison persisted"
exit "$runner_status"
