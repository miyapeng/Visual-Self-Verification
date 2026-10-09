#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

MODEL_PATH="${MODEL_PATH:-/data/miyapeng/model/Qwen3-VL-30B-A3B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3-VL-30B-A3B-Instruct}"
GPU_IDS="${GPU_IDS:-0 1 2 3}"
BASE_PORT="${BASE_PORT:-8001}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.98}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-900}"
WORKERS="${WORKERS:-8}"
RENDER_WORKERS="${RENDER_WORKERS:-4}"
JUDGE_WORKERS="${JUDGE_WORKERS:-8}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/runs/${SERVED_MODEL_NAME}-4gpu}"
LOG_DIR="${LOG_DIR:-${OUTPUT_ROOT}/server_logs}"
CONDA_BASE="${CONDA_BASE:-/data/miyapeng/miniconda3}"
VLLM_ENV_NAME="${VLLM_ENV_NAME:-vllm}"
EVAL_ENV_NAME="${EVAL_ENV_NAME:-mmcode}"
VLLM_ENV_PREFIX="${VLLM_ENV_PREFIX:-${CONDA_BASE}/envs/${VLLM_ENV_NAME}}"
EVAL_ENV_PREFIX="${EVAL_ENV_PREFIX:-${CONDA_BASE}/envs/${EVAL_ENV_NAME}}"
VLLM_BIN="${VLLM_BIN:-${VLLM_ENV_PREFIX}/bin/vllm}"
PYTHON_BIN="${PYTHON_BIN:-${EVAL_ENV_PREFIX}/bin/python}"
START_STAGE="${START_STAGE:-generate}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  bash scripts/serving/run_4gpu_vllm.sh [scripts/run.py options]

Example:
  bash scripts/serving/run_4gpu_vllm.sh --limit 2

Environment overrides:
  GPU_IDS="0 1 2 3"  MODEL_PATH=...  SERVED_MODEL_NAME=...
  BASE_PORT=8001  WORKERS=8  JUDGE_WORKERS=8  LOCAL_EVAL_GPU=0
  GPU_MEMORY_UTILIZATION=0.98  MAX_MODEL_LEN=16384
  VLLM_ENV_NAME=vllm  EVAL_ENV_NAME=mmcode
  START_STAGE=generate|render|judge|design2code

Resume an existing run after generation:
  START_STAGE=render bash scripts/serving/run_4gpu_vllm.sh
EOF
  exit 0
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Evaluation Python not found: ${PYTHON_BIN}" >&2
  echo "Expected the '${EVAL_ENV_NAME}' Conda environment at ${EVAL_ENV_PREFIX}" >&2
  exit 2
fi
command -v curl >/dev/null || {
  echo "curl is required for endpoint readiness checks" >&2
  exit 2
}
case "${START_STAGE}" in
  generate|render|judge|design2code) ;;
  *)
    echo "Invalid START_STAGE: ${START_STAGE}" >&2
    echo "Choose: generate, render, judge, or design2code" >&2
    exit 2
    ;;
esac

read -r -a GPU_ARRAY <<< "${GPU_IDS}"
if [[ "${#GPU_ARRAY[@]}" -eq 0 ]]; then
  echo "GPU_IDS must contain at least one GPU index" >&2
  exit 2
fi

mkdir -p "${LOG_DIR}"
PIDS=()
URLS=()

stop_servers() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    wait "${pid}" 2>/dev/null || true
  done
  PIDS=()
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  stop_servers
  exit "${status}"
}
trap cleanup EXIT INT TERM

EXTRA_ARGS=()
if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
  read -r -a EXTRA_ARGS <<< "${VLLM_EXTRA_ARGS}"
fi

launch_servers() {
  if [[ ! -e "${MODEL_PATH}" ]]; then
    echo "Model path does not exist: ${MODEL_PATH}" >&2
    exit 2
  fi
  if [[ ! -x "${VLLM_BIN}" ]]; then
    echo "vLLM executable not found: ${VLLM_BIN}" >&2
    echo "Expected the '${VLLM_ENV_NAME}' Conda environment at ${VLLM_ENV_PREFIX}" >&2
    exit 2
  fi
  PIDS=()
  URLS=()
  local run_tag
  run_tag="$(date -u +%Y%m%dT%H%M%SZ)"
  for index in "${!GPU_ARRAY[@]}"; do
    gpu="${GPU_ARRAY[$index]}"
    port=$((BASE_PORT + index))
    log_file="${LOG_DIR}/gpu${gpu}-port${port}-${run_tag}.log"
    echo "[launch:${VLLM_ENV_NAME}] physical GPU ${gpu} -> http://127.0.0.1:${port}/v1"
    CUDA_VISIBLE_DEVICES="${gpu}" OMP_NUM_THREADS=1 \
      PATH="${VLLM_ENV_PREFIX}/bin:${PATH}" CONDA_PREFIX="${VLLM_ENV_PREFIX}" \
      "${VLLM_BIN}" serve "${MODEL_PATH}" \
        --host 0.0.0.0 \
        --port "${port}" \
        --served-model-name "${SERVED_MODEL_NAME}" \
        --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
        --max-model-len "${MAX_MODEL_LEN}" \
        "${EXTRA_ARGS[@]}" \
        >"${log_file}" 2>&1 &
    PIDS+=("$!")
    URLS+=("http://127.0.0.1:${port}/v1")
  done

  for index in "${!URLS[@]}"; do
    url="${URLS[$index]}"
    pid="${PIDS[$index]}"
    deadline=$((SECONDS + STARTUP_TIMEOUT))
    echo "[wait] ${url}"
    until curl --fail --silent --show-error "${url}/models" >/dev/null 2>&1; do
      if ! kill -0 "${pid}" 2>/dev/null; then
        echo "vLLM replica exited early: ${url}" >&2
        echo "See ${LOG_DIR}" >&2
        exit 1
      fi
      if (( SECONDS >= deadline )); then
        echo "Timed out waiting for ${url}" >&2
        echo "See ${LOG_DIR}" >&2
        exit 1
      fi
      sleep 2
    done
    echo "[ready] ${url}"
  done
  BASE_URLS="$(IFS=,; echo "${URLS[*]}")"
}

run_eval() {
  local visible_devices="$1"
  shift
  CUDA_VISIBLE_DEVICES="${visible_devices}" \
    PATH="${EVAL_ENV_PREFIX}/bin:${PATH}" CONDA_PREFIX="${EVAL_ENV_PREFIX}" \
    PYTHONNOUSERSITE=1 "${PYTHON_BIN}" "$@"
}

cd "${PROJECT_ROOT}"
echo "[environment] vLLM=${VLLM_ENV_PREFIX} evaluation=${EVAL_ENV_PREFIX}"
echo "[resume] output=${OUTPUT_ROOT} start_stage=${START_STAGE}"

if [[ "${START_STAGE}" == "generate" ]]; then
  launch_servers
  echo "[generate] endpoints=${BASE_URLS}"
  run_eval "" scripts/run.py \
    design2code flame-react-eval web2code ui2code-real \
    --output-root "${OUTPUT_ROOT}" \
    --stages generate \
    --backend vllm \
    --model "${SERVED_MODEL_NAME}" \
    --base-url "${BASE_URLS}" \
    --workers "${WORKERS}" \
    "$@"
fi

if [[ "${START_STAGE}" == "generate" || "${START_STAGE}" == "render" ]]; then
  echo "[render:${EVAL_ENV_NAME}] existing successful renders are skipped"
  run_eval "" scripts/run.py \
    design2code flame-react-eval web2code ui2code-real \
    --output-root "${OUTPUT_ROOT}" \
    --stages render \
    --render-workers "${RENDER_WORKERS}" \
    "$@"

  echo "[evaluate:${EVAL_ENV_NAME}] Flame local metric"
  run_eval "" scripts/run.py \
    flame-react-eval \
    --output-root "${OUTPUT_ROOT}" \
    --stages evaluate \
    "$@"
fi

if [[ "${START_STAGE}" != "design2code" ]]; then
  if [[ "${#PIDS[@]}" -eq 0 ]]; then
    launch_servers
  fi
  BASE_URLS="$(IFS=,; echo "${URLS[*]}")"
  echo "[judge] endpoints=${BASE_URLS}"
  run_eval "" scripts/run.py \
    web2code ui2code-real \
    --output-root "${OUTPUT_ROOT}" \
    --stages evaluate \
    --judge-backend vllm \
    --judge-model "${SERVED_MODEL_NAME}" \
    --judge-base-url "${BASE_URLS}" \
    --judge-workers "${JUDGE_WORKERS}" \
    "$@"
fi

echo "[stop] releasing vLLM replicas before Design2Code CLIP evaluation"
stop_servers
run_eval "${LOCAL_EVAL_GPU:-0}" scripts/run.py \
  design2code \
  --output-root "${OUTPUT_ROOT}" \
  --stages evaluate \
  "$@"
