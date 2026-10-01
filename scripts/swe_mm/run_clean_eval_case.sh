#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 INSTANCE_ID MODEL_NAME RUN_LABEL RUNTIME_ROOT" >&2
    exit 2
fi

instance_id=$1
model_name=$2
run_label=$3
runtime_root=$4
project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
eval_python=/data/miyapeng/miniconda3/envs/swemm/bin/python
run_root="$project_root/runs/swe_mm_official/$run_label"
agent_case="$run_root/agents/$model_name/swe-mm/$instance_id"
patch="$agent_case/patch.diff"
evaluation_root="$run_root/clean_evaluation/$model_name"
case_root="$evaluation_root/$instance_id"
summary="$case_root/clean-eval-summary.json"
job_log="$case_root/clusterx-job.log"

mkdir -p "$case_root"
exec > >(tee -a "$job_log") 2>&1

echo "[clean-eval] started_at=$(date -u +%FT%TZ) instance=$instance_id model=$model_name"
echo "[clean-eval] fresh_clusterx_instance_image=true timeout=1800"

if [[ ! -s $patch ]]; then
    echo "[clean-eval] prediction has no non-empty patch"
    evaluator_status=2
else
    "$eval_python" "$runtime_root/scripts/swe_mm/direct_case_eval.py" \
        --instance-id "$instance_id" \
        --patch "$patch" \
        --output-root "$evaluation_root" \
        --timeout 1800
    evaluator_status=$?
fi

"$python" - "$summary" "$case_root/report.json" "$instance_id" "$model_name" "$evaluator_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

summary_path, report_path, instance_id, model, evaluator_status = sys.argv[1:]
report_file = Path(report_path)
report = {}
if report_file.is_file():
    try:
        report = json.loads(report_file.read_text(encoding="utf-8")).get(instance_id, {})
    except json.JSONDecodeError:
        report = {}
if report:
    outcome = "resolved" if report.get("resolved", False) else "unresolved"
elif int(evaluator_status) == 2:
    outcome = "no_patch"
else:
    outcome = "evaluation_error"
Path(summary_path).write_text(json.dumps({
    "instance_id": instance_id,
    "model": model,
    "transport": "fresh-clusterx-instance-image",
    "evaluator_status": int(evaluator_status),
    "report_exists": bool(report),
    "resolved": bool(report.get("resolved", False)),
    "outcome": outcome,
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY

echo "[clean-eval] finished_at=$(date -u +%FT%TZ) evaluator_status=$evaluator_status"
# Resolved, unresolved, and invalid patches are all completed evaluations.
# The controller uses the durable summary marker rather than the job exit code.
exit 0
