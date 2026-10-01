#!/usr/bin/env bash
set -uo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
source_workspace="$project_root/runs/vision2web_generation/qwen35-9b-openhands-official-v2/agents/litellm_proxy__Qwen3.5-9B/vision2web/official/webpage__brother/workspace"
output_dir="$project_root/runs/vision2web_official_eval/toolchain_diagnostics/playwright-$(date -u +%Y%m%dT%H%M%SZ)"
work_dir=/workspace

mkdir -p "$output_dir"
rm -rf "$work_dir"
mkdir -p "$work_dir"
cp -a "$source_workspace/." "$work_dir/"
cd "$work_dir"

exec >"$output_dir/diagnostic.log" 2>&1

echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "python=$(python3.12 --version 2>&1)"
echo "node=$(node --version 2>&1)"
echo "playwright_cli=$(command -v playwright-cli || true)"
echo "playwright_cli_version=$(playwright-cli --version 2>&1)"
playwright-cli --help || true

# ClusterX launches the frozen image as root without Chromium's kernel sandbox.
# This is the Playwright-supported container setting; it changes process
# isolation only, not page behavior, workflows, screenshots, or scoring.
export PLAYWRIGHT_MCP_CONFIG="$project_root/evaluate/vision2web/clusterx_transport/playwright-cli.config.json"

bash start.sh >"$output_dir/deploy.log" 2>&1 &
deploy_pid=$!
echo "deploy_pid=$deploy_pid"

ready=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3000/ >"$output_dir/homepage.html"; then
    ready=1
    break
  fi
  sleep 1
done
echo "service_ready=$ready"

set +e
playwright-cli open http://127.0.0.1:3000/
echo "open_rc=$?"
playwright-cli resize 1024 768
echo "resize_rc=$?"
playwright-cli snapshot
echo "snapshot_rc=$?"
playwright-cli screenshot --full-page --filename="$output_dir/actual.png"
echo "screenshot_rc=$?"
playwright-cli close
echo "close_rc=$?"
set -e

if [[ -s "$output_dir/actual.png" ]]; then
  echo "screenshot_bytes=$(stat -c %s "$output_dir/actual.png")"
else
  echo "screenshot_bytes=0"
fi

kill "$deploy_pid" 2>/dev/null || true
wait "$deploy_pid" 2>/dev/null || true
