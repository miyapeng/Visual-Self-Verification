#!/usr/bin/env bash
set -euo pipefail
: "${VSV_DISTILLED_MODEL:?Set the merged distilled model directory}"
: "${VSV_TRAIN_DATA:?Set the training parquet path}"
: "${VSV_VALIDATION_DATA:?Set the validation parquet path}"
: "${VSV_ROLLOUT_DIR:?Set a shared directory for rollout evidence}"
VSV_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export VSV_AGENT_LOOP_CONFIG="$VSV_REPO_ROOT/configs/training/agent_loop.yaml"
export VERL_USE_EXTERNAL_MODULES=multimodalcode.training.verl_integration
export VSV_ROLLOUT_DIR
python -m verl.trainer.main_ppo \
  --config-path "$VSV_REPO_ROOT/configs/training" \
  --config-name verification_grpo "$@"
