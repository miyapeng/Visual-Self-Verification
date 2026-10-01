#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT=/data/miyapeng/mmcode/MultimodalCode
AUTHFILE=${1:-/tmp/swe-mm-ccr-auth.json}
PYTHON=/data/miyapeng/miniconda3/envs/swemm/bin/python
MIRROR_SCRIPT=$PROJECT_ROOT/scripts/swe_mm/mirror_images.py
DATASET=$PROJECT_ROOT/data/swe_mm/dev/evaluator_private/instances.full.jsonl
STATE=$PROJECT_ROOT/data/swe_mm/dev/private_images.jsonl
LOG_ROOT=$PROJECT_ROOT/runs/swe_mm_image_sync/resume_supervisor
SHARDS=${SWE_MM_MIRROR_SHARDS:-4}
MAX_PASSES=${SWE_MM_MIRROR_MAX_PASSES:-8}

if [[ ! -s "$AUTHFILE" ]]; then
  echo "CCR authfile is missing or empty: $AUTHFILE" >&2
  exit 2
fi

mkdir -p "$LOG_ROOT"

remaining_count() {
  "$PYTHON" - "$DATASET" "$STATE" <<'PY'
import json
import sys
from pathlib import Path

dataset, state = map(Path, sys.argv[1:])
all_ids = {
    json.loads(line)["instance_id"]
    for line in dataset.read_text(encoding="utf-8").splitlines()
    if line.strip()
}
done_ids = set()
if state.exists():
    for line in state.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") in {"mirrored", "already_present"}:
            done_ids.add(row["instance_id"])
print(len(all_ids - done_ids))
PY
}

for ((pass = 1; pass <= MAX_PASSES; pass++)); do
  before=$(remaining_count)
  if [[ "$before" -eq 0 ]]; then
    echo "[complete] 102/102 SWE-MM images are present"
    exit 0
  fi
  echo "[pass $pass/$MAX_PASSES] remaining before pass: $before"

  pass_dir=$LOG_ROOT/pass-$pass
  mkdir -p "$pass_dir"
  pids=()
  for ((shard = 0; shard < SHARDS; shard++)); do
    "$PYTHON" -u "$MIRROR_SCRIPT" \
      --authfile "$AUTHFILE" \
      --retry-times 8 \
      --resume \
      --source-registry docker.io \
      --verification-registry dockerproxy.net \
      --num-shards "$SHARDS" \
      --shard-index "$shard" \
      > "$pass_dir/shard-$shard.log" 2>&1 &
    pids+=("$!")
  done

  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done

  after=$(remaining_count)
  echo "[pass $pass/$MAX_PASSES] remaining after pass: $after"
  if [[ "$after" -eq 0 ]]; then
    echo "[complete] 102/102 SWE-MM images are present"
    exit 0
  fi
  if [[ "$after" -ge "$before" ]]; then
    echo "[retry] no progress in pass $pass; waiting 30 seconds" >&2
    sleep 30
  fi
done

remaining=$(remaining_count)
echo "[failed] $remaining SWE-MM image(s) remain after $MAX_PASSES passes" >&2
exit 1
