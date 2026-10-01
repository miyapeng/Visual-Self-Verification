#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 RUN_DIR" >&2
    exit 2
fi

run_dir=$1
source_workspace="$run_dir/workspace"
validation_log="$run_dir/start-validation.log"
validation_json="$run_dir/start-validation.json"

if [[ ! -f "$source_workspace/start.sh" ]]; then
    echo "missing generated start.sh: $source_workspace/start.sh" >&2
    exit 1
fi

mkdir -p /workspace
cp -a "$source_workspace"/. /workspace/
chmod +x /workspace/start.sh

set +e
timeout 120 bash /workspace/start.sh >"$validation_log" 2>&1
start_status=$?
set -e

http_status=0
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:3000/ >/dev/null; then
        http_status=200
        break
    fi
    sleep 1
done

if [[ -s /tmp/server.pid ]]; then
    server_pid=$(cat /tmp/server.pid)
    kill "$server_pid" 2>/dev/null || true
fi

python3.12 - "$validation_json" "$start_status" "$http_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
start_status = int(sys.argv[2])
http_status = int(sys.argv[3])
record = {
    "start_status": start_status,
    "http_status": http_status,
    "ready": start_status == 0 and http_status == 200,
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if record["ready"] else 1)
PY
