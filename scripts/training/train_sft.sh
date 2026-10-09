#!/usr/bin/env bash
set -euo pipefail
: "${VSV_STUDENT_MODEL:?Set the student model path or Hugging Face ID}"
: "${VSV_SFT_DATA:?Set the exported train.jsonl path}"
: "${VSV_SFT_OUTPUT:?Set a new output directory}"
swift sft \
  --model "$VSV_STUDENT_MODEL" \
  --dataset "$VSV_SFT_DATA" \
  --output_dir "$VSV_SFT_OUTPUT" \
  --tuner_type lora \
  --torch_dtype bfloat16 \
  --loss_scale default \
  --split_dataset_ratio 0 \
  --max_length 24576 \
  --truncation_strategy raise \
  --per_device_train_batch_size 1 \
  --gradient_accumulation_steps 8 \
  --learning_rate 1e-4 \
  --num_train_epochs 1 "$@"
