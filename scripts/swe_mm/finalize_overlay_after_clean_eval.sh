#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 BASE_RUN_LABEL OVERLAY_RUN_LABEL MODEL OUTPUT_DIR_NAME" >&2
    exit 2
fi

base_run_label=$1
overlay_run_label=$2
model=$3
output_dir_name=$4
project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
clean_root="$project_root/runs/swe_mm_official/$overlay_run_label/clean_evaluation/$model"

mapfile -t overlay_ids < <(
    find "$project_root/runs/swe_mm_official/$overlay_run_label/agents/$model/swe-mm" \
        -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
)
if [[ ${#overlay_ids[@]} -eq 0 ]]; then
    echo "No overlay cases found for $overlay_run_label" >&2
    exit 1
fi

echo "[finalize] started_at=$(date -u +%FT%TZ) overlay_cases=${#overlay_ids[@]}"
while true; do
    pending=()
    for instance_id in "${overlay_ids[@]}"; do
        summary="$clean_root/$instance_id/clean-eval-summary.json"
        [[ -f $summary ]] || pending+=("$instance_id")
    done
    if [[ ${#pending[@]} -eq 0 ]]; then
        break
    fi
    echo "[finalize] waiting_at=$(date -u +%FT%TZ) pending=${pending[*]}"
    sleep 30
done

"$python" "$project_root/scripts/swe_mm/collect_clean_eval.py" \
    --run-label "$base_run_label" \
    --overlay-run-label "$overlay_run_label" \
    --model "$model" \
    --output-dir-name "$output_dir_name"
echo "[finalize] finished_at=$(date -u +%FT%TZ)"
