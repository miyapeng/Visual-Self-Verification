#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 RUN_LABEL MODEL_NAME" >&2
    exit 2
fi

run_label=$1
model_name=$2
project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
safe_model=${model_name//\//__}
predictions="$project_root/runs/swe_mm_official/$run_label/submission/predictions.json"
report_dir="$project_root/runs/swe_mm_official/$run_label/sb-cli-reports"

"$python" "$project_root/scripts/swe_mm/collect_official_dev.py" \
    --run-label "$run_label" --model "$model_name"

"$python" - "$project_root/runs/swe_mm_official/$run_label/submission/local_summary.json" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1]))
if summary["completed_agents"] != summary["denominator"]:
    raise SystemExit(
        "Refusing an incomplete official submission: "
        f"{summary['completed_agents']}/{summary['denominator']} agent cases have finished"
    )
PY

sb_cli=${SB_CLI:-$project_root/.envs/sbcli/bin/sb-cli}
if [[ ! -x $sb_cli ]]; then
    sb_cli=$(command -v sb-cli || true)
fi
if [[ -z $sb_cli || ! -x $sb_cli ]]; then
    echo "sb-cli is not installed. Create the isolated .envs/sbcli environment first." >&2
    exit 1
fi
if [[ ! -s $predictions ]]; then
    echo "predictions file is missing: $predictions" >&2
    exit 1
fi

exec "$sb_cli" submit swe-bench-m dev \
    --predictions_path "$predictions" \
    --run_id "$run_label" \
    --output_dir "$report_dir"
