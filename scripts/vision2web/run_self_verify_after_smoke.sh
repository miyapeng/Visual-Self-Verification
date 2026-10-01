#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
probe=${1:?usage: $0 PROBE_JSON RUN_LABEL [MAX_ACTIVE]}
run_label=${2:?usage: $0 PROBE_JSON RUN_LABEL [MAX_ACTIVE]}
max_active=${3:-3}

echo "[guard] waiting for browser probe: $probe"
while [[ ! -s $probe ]]; do
    sleep 30
done

status=$(/data/miyapeng/miniconda3/envs/mmcode/bin/python - "$probe" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", "missing"))
PY
)
if [[ $status != ok ]]; then
    echo "[guard] browser probe failed with status=$status; experiment not started" >&2
    exit 1
fi

echo "[guard] browser probe passed; starting controlled smoke experiment"
exec /bin/bash "$project_root/scripts/vision2web/run_self_verify_supervisor.sh" \
    smoke "$max_active" "$run_label"
