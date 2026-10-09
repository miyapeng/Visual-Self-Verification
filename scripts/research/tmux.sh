#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"
VLLM_BIN="${VLLM_BIN:-/data/miyapeng/miniconda3/envs/vllm/bin/vllm}"
EVAL_PYTHON="${EVAL_PYTHON:-/data/miyapeng/miniconda3/envs/mmcode/bin/python}"
MODEL_PATH="${MODEL_PATH:-/data/miyapeng/model/Qwen3.5-4B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3.5-4B}"
GPU_IDS="${GPU_IDS:-0}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8001}"
BASE_URL="${BASE_URL:-http://${HOST}:${PORT}/v1}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
SERVER_SESSION="${SERVER_SESSION:-mmcode-vllm-4b}"
RUN_SESSION="${RUN_SESSION:-mmcode-exp-4b}"
CASES="${CASES:-configs/research/webcompass_local_cases.json}"
CASE_IDS="${CASE_IDS:-}"
OUTPUT_ROOT="${OUTPUT_ROOT:-runs/research/webcompass-qwen35-4b}"
POLICIES="${POLICIES:-residual}"
MAX_REVISIONS="${MAX_REVISIONS:-2}"
MAX_CONTRACT_RETRIES="${MAX_CONTRACT_RETRIES:-1}"
CONTEXT_TOKENS="${CONTEXT_TOKENS:-4096}"
CONTEXT_IMAGES="${CONTEXT_IMAGES:-4}"
SEED="${SEED:-0}"
RECORD_VIDEO="${RECORD_VIDEO:-1}"
ALLOCATED_GPUS="${ALLOCATED_GPUS:-1}"
LIMIT="${LIMIT:-1}"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
LOG_ROOT="${LOG_ROOT:-${PROJECT_ROOT}/runs/research/logs}"
SERVER_LOG="${SERVER_LOG:-${LOG_ROOT}/${SERVER_SESSION}.log}"
RUN_LOG="${RUN_LOG:-${LOG_ROOT}/${RUN_SESSION}.log}"
SERVER_PID_FILE="${SERVER_PID_FILE:-${LOG_ROOT}/${SERVER_SESSION}.pid}"
RUN_PID_FILE="${RUN_PID_FILE:-${LOG_ROOT}/${RUN_SESSION}.pid}"
SERVER_CONFIG="${SERVER_CONFIG:-${LOG_ROOT}/${SERVER_SESSION}.config.txt}"
RUN_CONFIG="${RUN_CONFIG:-${LOG_ROOT}/${RUN_SESSION}.config.txt}"

mkdir -p "$LOG_ROOT"

usage() {
  printf '%s\n' \
    "Usage: bash scripts/research/tmux.sh COMMAND" \
    "Commands:" \
    "  start-server  Start the vLLM server in a detached tmux session" \
    "  start-run     Start configured policies in a detached tmux session" \
    "  start-all     Start server, wait for readiness, then start the run" \
    "  resume        Resume the run using existing successful artifacts" \
    "  status        Show tmux sessions, endpoint state, and result summary" \
    "  attach        Attach to the experiment session (or server if absent)" \
    "  tail          Follow experiment/server logs" \
    "  stop-run      Stop only the experiment session" \
    "  stop-server   Stop only the vLLM session" \
    "  stop-all      Stop both sessions"
}

has_session() {
  tmux has-session -t "$1" 2>/dev/null
}

session_pid() {
  tmux list-panes -t "$1" -F '#{pane_pid}' 2>/dev/null | head -n 1
}

record_session_pid() {
  local session="$1"
  local target="$2"
  local pid
  pid="$(session_pid "$session")"
  if [[ -n "$pid" ]]; then
    printf '%s\n' "$pid" > "$target"
  fi
}

endpoint_ready() {
  curl -fsS --max-time 3 "${BASE_URL}/models" >/dev/null 2>&1
}

endpoint_serves_model() {
  local response
  response="$(curl -fsS --max-time 3 "${BASE_URL}/models" 2>/dev/null)" || return 1
  printf '%s' "$response" | "$EVAL_PYTHON" -c \
      'import json,sys; expected=sys.argv[1]; data=json.load(sys.stdin); names={str(row.get("id")) for row in data.get("data", [])}; raise SystemExit(0 if expected in names else 1)' \
      "$SERVED_MODEL_NAME"
}

wait_endpoint() {
  local waited=0
  while (( waited < WAIT_SECONDS )); do
    if endpoint_serves_model; then
      printf '[ready] %s after %ss\n' "$BASE_URL" "$waited"
      return 0
    fi
    if endpoint_ready && ! endpoint_serves_model; then
      printf '[error] endpoint is occupied but does not serve %s\n' \
        "$SERVED_MODEL_NAME" >&2
      return 1
    fi
    if ! has_session "$SERVER_SESSION"; then
      printf '[error] server tmux session exited before readiness\n' >&2
      tail -n 120 "$SERVER_LOG" 2>/dev/null || true
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  printf '[error] endpoint did not become ready within %ss\n' "$WAIT_SECONDS" >&2
  tail -n 120 "$SERVER_LOG" 2>/dev/null || true
  return 1
}

write_server_snapshot() {
  {
    printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'model_path=%s\n' "$MODEL_PATH"
    printf 'served_model_name=%s\n' "$SERVED_MODEL_NAME"
    printf 'gpu_ids=%s\n' "$GPU_IDS"
    printf 'base_url=%s\n' "$BASE_URL"
    printf 'gpu_memory_utilization=%s\n' "$GPU_MEMORY_UTILIZATION"
    printf 'max_model_len=%s\n' "$MAX_MODEL_LEN"
    "$VLLM_BIN" --version 2>/dev/null | sed 's/^/vllm_version=/'
  } > "$SERVER_CONFIG"
}

write_run_snapshot() {
  {
    printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'eval_python=%s\n' "$EVAL_PYTHON"
    printf 'served_model_name=%s\n' "$SERVED_MODEL_NAME"
    printf 'base_url=%s\n' "$BASE_URL"
    printf 'cases=%s\n' "$CASES"
    printf 'case_ids=%s\n' "$CASE_IDS"
    printf 'output_root=%s\n' "$OUTPUT_ROOT"
    printf 'policies=%s\n' "$POLICIES"
    printf 'limit=%s\n' "$LIMIT"
    printf 'max_revisions=%s\n' "$MAX_REVISIONS"
    printf 'max_contract_retries=%s\n' "$MAX_CONTRACT_RETRIES"
    printf 'context_tokens=%s\n' "$CONTEXT_TOKENS"
    printf 'context_images=%s\n' "$CONTEXT_IMAGES"
    printf 'seed=%s\n' "$SEED"
    printf 'record_video=%s\n' "$RECORD_VIDEO"
    printf 'allocated_gpus=%s\n' "$ALLOCATED_GPUS"
  } > "$RUN_CONFIG"
}

start_server() {
  if endpoint_serves_model; then
    printf '[reuse] existing endpoint already serves %s at %s\n' \
      "$SERVED_MODEL_NAME" "$BASE_URL"
    return
  fi
  if endpoint_ready; then
    printf '[error] refusing to start: %s is occupied by a different model\n' \
      "$BASE_URL" >&2
    return 1
  fi
  if has_session "$SERVER_SESSION"; then
    printf '[skip] server session already exists: %s\n' "$SERVER_SESSION"
    wait_endpoint
    return
  fi
  write_server_snapshot
  : > "$SERVER_LOG"
  local command
  printf -v command \
    'cd %q && exec env CUDA_VISIBLE_DEVICES=%q OMP_NUM_THREADS=1 %q serve %q --host %q --port %q --served-model-name %q --gpu-memory-utilization %q --max-model-len %q 2>&1 | tee -a %q' \
    "$PROJECT_ROOT" "$GPU_IDS" "$VLLM_BIN" "$MODEL_PATH" "$HOST" "$PORT" \
    "$SERVED_MODEL_NAME" "$GPU_MEMORY_UTILIZATION" "$MAX_MODEL_LEN" "$SERVER_LOG"
  tmux new-session -d -s "$SERVER_SESSION" "bash -lc $(printf '%q' "$command")"
  record_session_pid "$SERVER_SESSION" "$SERVER_PID_FILE"
  printf '[start] vLLM tmux=%s pid=%s gpu=%s endpoint=%s log=%s config=%s\n' \
    "$SERVER_SESSION" "$(session_pid "$SERVER_SESSION")" "$GPU_IDS" \
    "$BASE_URL" "$SERVER_LOG" "$SERVER_CONFIG"
}

start_run() {
  wait_endpoint
  if has_session "$RUN_SESSION"; then
    printf '[skip] experiment session already exists: %s\n' "$RUN_SESSION"
    return
  fi
  write_run_snapshot
  : > "$RUN_LOG"
  local body="run_status=0; "
  local video_flag=""
  if [[ "$RECORD_VIDEO" == "0" ]]; then
    video_flag=" --no-video"
  fi
  local case_flags=""
  local case_id
  for case_id in $CASE_IDS; do
    case_flags+=" --case-id $(printf '%q' "$case_id")"
  done
  local policy
  for policy in $POLICIES; do
    local one
    printf -v one \
      '%q scripts/research/run.py run --cases %q --output-root %q --policy %q --backend vllm --model %q --base-url %q --limit %q --max-revisions %q --max-contract-retries %q --context-tokens %q --context-images %q --seed %q --allocated-gpus %q --extra-body %q' \
      "$EVAL_PYTHON" "$CASES" "$OUTPUT_ROOT" "$policy" "$SERVED_MODEL_NAME" \
      "$BASE_URL" "$LIMIT" "$MAX_REVISIONS" "$MAX_CONTRACT_RETRIES" "$CONTEXT_TOKENS" "$CONTEXT_IMAGES" \
      "$SEED" "$ALLOCATED_GPUS" \
      '{"chat_template_kwargs":{"enable_thinking":false}}'
    one+="$video_flag"
    one+="$case_flags"
    body+="$one || run_status=1; "
  done
  body+='exit "$run_status"; '
  local command
  printf -v command 'cd %q && export PYTHONPATH=%q && { %s } 2>&1 | tee -a %q' \
    "$PROJECT_ROOT" "${PROJECT_ROOT}/src" "$body" "$RUN_LOG"
  tmux new-session -d -s "$RUN_SESSION" "bash -lc $(printf '%q' "$command")"
  record_session_pid "$RUN_SESSION" "$RUN_PID_FILE"
  printf '[start] experiment tmux=%s pid=%s policies=%s cases=%s limit=%s output=%s log=%s config=%s\n' \
    "$RUN_SESSION" "$(session_pid "$RUN_SESSION")" "$POLICIES" "$CASES" \
    "$LIMIT" "$OUTPUT_ROOT" "$RUN_LOG" "$RUN_CONFIG"
}

status() {
  printf 'server_session=%s\n' "$(has_session "$SERVER_SESSION" && printf running || printf stopped)"
  printf 'run_session=%s\n' "$(has_session "$RUN_SESSION" && printf running || printf stopped)"
  printf 'server_pid=%s\n' "$(has_session "$SERVER_SESSION" && session_pid "$SERVER_SESSION" || printf none)"
  printf 'run_pid=%s\n' "$(has_session "$RUN_SESSION" && session_pid "$RUN_SESSION" || printf none)"
  printf 'endpoint=%s\n' "$(endpoint_ready && printf ready || printf unavailable)"
  printf 'server_log=%s\nrun_log=%s\nserver_config=%s\nrun_config=%s\n' \
    "$SERVER_LOG" "$RUN_LOG" "$SERVER_CONFIG" "$RUN_CONFIG"
  local policy
  for policy in $POLICIES; do
    local summary="${PROJECT_ROOT}/${OUTPUT_ROOT}/${policy}/summary.json"
    if [[ -f "$summary" ]]; then
      printf 'summary[%s]=%s\n' "$policy" "$summary"
      "$EVAL_PYTHON" -c \
        'import json,sys; x=json.load(open(sys.argv[1])); print(json.dumps({k:x.get(k) for k in ("total","complete","error","mean_final_score")},ensure_ascii=False))' \
        "$summary"
    fi
  done
}

case "${1:-status}" in
  start-server) start_server; wait_endpoint ;;
  start-run) start_run ;;
  start-all) start_server; wait_endpoint; start_run ;;
  resume) start_run ;;
  status) status ;;
  attach)
    if has_session "$RUN_SESSION"; then tmux attach-session -t "$RUN_SESSION";
    elif has_session "$SERVER_SESSION"; then tmux attach-session -t "$SERVER_SESSION";
    else printf '[error] no research tmux session is running\n' >&2; exit 1; fi
    ;;
  tail) tail -n 120 -F "$RUN_LOG" "$SERVER_LOG" ;;
  stop-run) has_session "$RUN_SESSION" && tmux kill-session -t "$RUN_SESSION" || true ;;
  stop-server) has_session "$SERVER_SESSION" && tmux kill-session -t "$SERVER_SESSION" || true ;;
  stop-all)
    has_session "$RUN_SESSION" && tmux kill-session -t "$RUN_SESSION" || true
    has_session "$SERVER_SESSION" && tmux kill-session -t "$SERVER_SESSION" || true
    ;;
  *) usage; exit 2 ;;
esac
