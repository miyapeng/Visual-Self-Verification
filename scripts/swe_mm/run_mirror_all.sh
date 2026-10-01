#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/data/miyapeng/mmcode/MultimodalCode
AUTHFILE=${1:-/tmp/swe-mm-ccr-auth.json}
PYTHON=/data/miyapeng/miniconda3/envs/swemm/bin/python
MIRROR_SCRIPT=$PROJECT_ROOT/scripts/swe_mm/mirror_images.py

if [[ ! -s "$AUTHFILE" ]]; then
  echo "CCR authfile is missing or empty: $AUTHFILE" >&2
  echo "Run skopeo login again, then restart this script." >&2
  exit 2
fi

export PYTHONUNBUFFERED=1
export NO_PROXY=registry.pjlab.org.cn,xceph-inside.pjlab.org.cn,localhost,127.0.0.1
export no_proxy=$NO_PROXY

exec "$PYTHON" -u "$MIRROR_SCRIPT" \
  --authfile "$AUTHFILE" \
  --retry-times 8 \
  --all
