#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-35B-A3B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
mmcode_python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
cases="$project_root/configs/research/interactweb_active_repair_cases.json"
persistent_root="$project_root/runs/research/active_visual_verification/interactweb-repair-capacity-qwen35b-003"
scratch_root=/tmp/mmcode-iwcap-q3535-0821r2
port=18034
case_ids=(interactweb-000045-navigation-repair interactweb-000062-question-bank-repair)

mkdir -p "$persistent_root" "$scratch_root"
exec > >(tee -a "$scratch_root/job.log") 2>&1

# Never copy directly onto a final result. If /data fills mid-copy, only a new
# hidden partial is damaged and a valid summary cannot be truncated.
atomic_copy() {
  local source=$1 destination=$2
  local partial="$persistent_root/.${destination}.partial.$$"
  [[ -s "$source" ]] || return 1
  cp "$source" "$partial" || return 1
  mv -f "$partial" "$persistent_root/$destination"
}

write_status() {
  local phase=$1 detail=${2:-}
  printf '{"schema":"mmcode-capacity-recovery-1","phase":"%s","detail":"%s","updated_at":"%s","host":"%s"}\n' \
    "$phase" "$detail" "$(date -u +%FT%TZ)" "$(hostname)" >"$scratch_root/status.json"
  atomic_copy "$scratch_root/status.json" status.json
}

persist_tails() {
  local name
  for name in job vllm; do
    if [[ -s "$scratch_root/$name.log" ]]; then
      tail -c 65536 "$scratch_root/$name.log" >"$scratch_root/$name.tail.log"
      atomic_copy "$scratch_root/$name.tail.log" "$name.tail.log" || true
    fi
  done
  [[ ! -s "$scratch_root/models.json" ]] || atomic_copy "$scratch_root/models.json" models.json || true
}

server_pid=
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  persist_tails
}
trap cleanup EXIT

echo "[repair-capacity-r2] started_at=$(date -u +%FT%TZ) host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
df -h /data /tmp
write_status starting "scratch-only mutable state"

export XDG_CACHE_HOME="$scratch_root/cache"
export HF_HOME="$scratch_root/cache/huggingface"
export TORCHINDUCTOR_CACHE_DIR="$scratch_root/cache/torchinductor"
export TRITON_CACHE_DIR="$scratch_root/cache/triton"
export TMPDIR="$scratch_root/tmp"
mkdir -p "$XDG_CACHE_HOME" "$HF_HOME" "$TORCHINDUCTOR_CACHE_DIR" "$TRITON_CACHE_DIR" "$TMPDIR"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.runtime/research/playwright"

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 --port "$port" \
  --served-model-name Qwen3.5-35B-A3B \
  --gpu-memory-utilization 0.98 --max-model-len 32768 --max-num-seqs 1 \
  >"$scratch_root/vllm.log" 2>&1 &
server_pid=$!

ready=0
for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$scratch_root/models.json"; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    tail -200 "$scratch_root/vllm.log" >&2
    write_status server_failed "vLLM exited during startup" || true
    exit 1
  fi
  sleep 5
done
if [[ "$ready" -ne 1 ]]; then
  write_status server_failed "readiness timeout" || true
  exit 1
fi
atomic_copy "$scratch_root/models.json" models.json
write_status server_ready Qwen3.5-35B-A3B

cd "$project_root"
persisted_cases=0
for case_id in "${case_ids[@]}"; do
  output_root="$scratch_root/results/$case_id"
  write_status case_running "$case_id"
  set +e
  PYTHONPATH=src "$mmcode_python" research_run.py run \
    --cases "$cases" --case-id "$case_id" --output-root "$output_root" \
    --policy guarded_frontier --source-context execution_rooted \
    --backend vllm --model Qwen3.5-35B-A3B \
    --base-url "http://127.0.0.1:$port/v1" \
    --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' \
    --max-revisions 1 --max-contract-retries 1 --max-tokens 8192 \
    --temperature 0 --seed 0 --context-tokens 4096 --context-images 4 \
    --context-image-pixels 8000000 --timeout 600 --allocated-gpus 1 --no-video
  experimental_status=$?
  set -e

  run_dir="$output_root/guarded_frontier"
  summary="$run_dir/summary.json"
  run_config="$run_dir/run_config.json"
  if [[ ! -s "$summary" || ! -s "$run_config" ]]; then
    write_status case_missing_summary "$case_id exit=$experimental_status" || true
    continue
  fi

  # Persist decisive small files first, independently for every case.
  atomic_copy "$summary" "$case_id.summary.json"
  atomic_copy "$run_config" "$case_id.run_config.json"
  persisted_cases=$((persisted_cases + 1))
  write_status case_summary_persisted "$case_id exit=$experimental_status"

  # Audit model prompts/responses, reports, source snapshots, and final code;
  # large reproducible browser screenshots are intentionally omitted.
  audit="$scratch_root/$case_id.audit.tar.gz"
  tar -C "$run_dir" -czf "$audit" summary.json run_config.json run_snapshots summary_history cases
  if atomic_copy "$audit" "$case_id.audit.tar.gz"; then
    sha256sum "$persistent_root/$case_id.audit.tar.gz" >"$scratch_root/$case_id.audit.sha256"
    atomic_copy "$scratch_root/$case_id.audit.sha256" "$case_id.audit.sha256"
    write_status case_audit_persisted "$case_id exit=$experimental_status"
  else
    write_status case_audit_deferred "$case_id summary preserved" || true
  fi
done

kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=
persist_tails
if [[ "$persisted_cases" -ne "${#case_ids[@]}" ]]; then
  write_status incomplete "$persisted_cases/${#case_ids[@]} summaries" || true
  exit 1
fi
write_status complete "$persisted_cases/${#case_ids[@]} summaries"
echo "[repair-capacity-r2] completed_at=$(date -u +%FT%TZ) summaries=$persisted_cases"
