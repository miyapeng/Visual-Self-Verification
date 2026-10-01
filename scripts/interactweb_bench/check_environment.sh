#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/env.sh"

echo "[interactweb] Python"
"${INTERACTWEB_PYTHON}" --version
"${INTERACTWEB_PYTHON}" -m pip check

echo "[interactweb] Node.js"
node_version="$(node --version)"
npm_version="$(npm --version)"
echo "node: ${node_version}"
echo "npm: ${npm_version}"
if [[ "${node_version}" != v22.* ]]; then
  echo "Expected the official Dockerfile's Node.js 22.x, got ${node_version}" >&2
  exit 1
fi

echo "[interactweb] Frozen data and official source integrity"
"${INTERACTWEB_PYTHON}" "${MMCODE_ROOT}/evaluate/interactweb_bench/run_official.py" preflight
"${INTERACTWEB_PYTHON}" "${MMCODE_ROOT}/evaluate/verify_official_sources.py" interactweb_bench

echo "[interactweb] Released entry points (offline import/argument checks)"
env \
  OPENAILIKE_API_KEY="environment-check-only" \
  OPENAILIKE_BASE_URL="http://127.0.0.1:9/v1" \
  OPENAILIKE_VLM_API_KEY="environment-check-only" \
  OPENAILIKE_VLM_BASE_URL="http://127.0.0.1:9/v1" \
  "${INTERACTWEB_PYTHON}" \
  "${MMCODE_ROOT}/evaluate/interactweb_bench/run_official.py" run-mini --help >/dev/null
"${INTERACTWEB_PYTHON}" \
  "${MMCODE_ROOT}/evaluate/interactweb_bench/run_official.py" analyze --help >/dev/null
"${INTERACTWEB_PYTHON}" \
  "${MMCODE_ROOT}/evaluate/interactweb_bench/run_official.py" intent-eval --help >/dev/null
"${INTERACTWEB_PYTHON}" \
  "${MMCODE_ROOT}/evaluate/interactweb_bench/run_official.py" aesthetics --help >/dev/null

echo "[interactweb] Official Playwright browser smoke test"
"${INTERACTWEB_PYTHON}" "${script_dir}/check_browser.py"

echo "[interactweb] environment OK"
