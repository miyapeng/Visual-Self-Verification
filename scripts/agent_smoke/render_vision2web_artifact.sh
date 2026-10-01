#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 ARTIFACT_DIR OUTPUT_DIR" >&2
    exit 2
fi

artifact_dir=$1
output_dir=$2
mkdir -p /workspace "$output_dir"
cp -a "$artifact_dir/." /workspace/

bash /workspace/start.sh >"$output_dir/start.log" 2>&1 &
server_pid=$!
cleanup() {
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:3000/ >/dev/null; then
        break
    fi
    sleep 2
done
curl -fsS http://127.0.0.1:3000/ >/dev/null

browser=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium)
timeout 120 "$browser" \
    --headless \
    --no-sandbox \
    --disable-dev-shm-usage \
    --hide-scrollbars \
    --window-size=1920,1080 \
    --screenshot="$output_dir/generated-desktop.png" \
    http://127.0.0.1:3000/
test -s "$output_dir/generated-desktop.png"
