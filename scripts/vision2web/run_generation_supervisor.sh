#!/usr/bin/env bash
set -uo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
controller="$project_root/scripts/vision2web/submit_generation.py"
run_root="$project_root/runs/vision2web_generation/qwen35-9b-openhands-official-v2"
supervisor_log="$run_root/supervisor.log"

mkdir -p "$run_root"

while true; do
    echo "[supervisor] controller_start=$(date -u +%FT%TZ)" >>"$supervisor_log"
    "$python" "$controller" \
        --max-active 2 \
        --poll-seconds 60 \
        --max-infra-retries 2 \
        >>"$run_root/controller.log" 2>&1
    controller_status=$?

    if "$python" - "$run_root" >>"$supervisor_log" 2>&1 <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
completed = len(list(root.glob("agents/**/generation-summary.json")))
print(f"[supervisor] persisted_completion_markers={completed}/193")
raise SystemExit(0 if completed == 193 else 1)
PY
    then
        echo "[supervisor] generation_complete=$(date -u +%FT%TZ)" >>"$supervisor_log"
        exit 0
    fi

    echo "[supervisor] controller_exit=$controller_status restart_after_seconds=30 at=$(date -u +%FT%TZ)" >>"$supervisor_log"
    sleep 30
done
