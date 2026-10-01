#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
run_label=${1:?usage: $0 RUN_LABEL [EXPECTED_RUNS]}
expected_runs=${2:-9}
run_root="$project_root/runs/vision2web_generation/$run_label"
controller="$run_root/controller-smoke.json"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python

echo "[finalize] waiting for $expected_runs terminal trajectories in $controller"
while true; do
    terminal=$($python - "$controller" <<'PY'
import json, sys
try:
    counts = json.load(open(sys.argv[1], encoding="utf-8")).get("counts", {})
except (FileNotFoundError, json.JSONDecodeError):
    counts = {}
print(int(counts.get("COMPLETED", 0)) + int(counts.get("INFRA_FAILED", 0)))
PY
)
    if (( terminal >= expected_runs )); then
        break
    fi
    echo "[finalize] terminal=$terminal/$expected_runs"
    sleep 60
done

cd "$project_root"
export PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
$python scripts/vision2web/rebuild_development_timelines.py --run-root "$run_root"
$python scripts/vision2web/analyze_self_verify_runs.py \
    --run-root "$run_root" \
    --output "$run_root/trajectory-analysis.json"
$python scripts/vision2web/build_self_verify_training_manifest.py \
    --analysis "$run_root/trajectory-analysis.json" \
    --output "$run_root/training-candidates.jsonl"
echo "[finalize] complete"
