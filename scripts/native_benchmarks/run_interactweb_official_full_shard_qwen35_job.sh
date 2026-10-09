#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/archive_interactweb_guard.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 SHARD_INDEX RUN_LABEL" >&2
  exit 2
fi

project_root=/data/miyapeng/mmcode/MultimodalCode
model_path=/data/miyapeng/model/Qwen3.5-9B
vllm_bin=/data/miyapeng/miniconda3/envs/vllm/bin/vllm
interact_python=/data/miyapeng/miniconda3/envs/interactweb/bin/python
interact_bin=/data/miyapeng/miniconda3/envs/interactweb/bin
port=18023
num_shards=8
shard_index=$((10#$1))
run_label=$2

if (( shard_index < 0 || shard_index >= num_shards )); then
  echo "invalid shard index: $shard_index" >&2
  exit 2
fi
if [[ ! $run_label =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "RUN_LABEL contains unsafe characters: $run_label" >&2
  exit 2
fi
if [[ -z ${INTERACTWEB_CREDENTIALS_FILE:-} ]]; then
  echo "INTERACTWEB_CREDENTIALS_FILE is required" >&2
  exit 2
fi
if [[ ! -f $INTERACTWEB_CREDENTIALS_FILE ]]; then
  echo "credentials file not found: $INTERACTWEB_CREDENTIALS_FILE" >&2
  exit 2
fi
credential_mode=$(stat -c '%a' "$INTERACTWEB_CREDENTIALS_FILE")
if (( (8#$credential_mode & 077) != 0 )); then
  echo "credentials file must not be group/world accessible (mode=$credential_mode)" >&2
  exit 2
fi

# Parse only the four allowed KEY=VALUE records. This deliberately avoids
# sourcing the file, so shell syntax inside a credential value is never run.
while IFS='=' read -r credential_name credential_value; do
  credential_name=${credential_name%$'\r'}
  credential_value=${credential_value%$'\r'}
  [[ -z $credential_name || $credential_name == \#* ]] && continue
  case "$credential_name" in
    USER_MODEL_BASE_URL|USER_MODEL_API_KEY|WEBVOYAGER_BASE_URL|WEBVOYAGER_API_KEY)
      printf -v "$credential_name" '%s' "$credential_value"
      export "$credential_name"
      ;;
    *)
      echo "unsupported record in credentials file: $credential_name" >&2
      exit 2
      ;;
  esac
done < "$INTERACTWEB_CREDENTIALS_FILE"
for required_name in USER_MODEL_BASE_URL USER_MODEL_API_KEY WEBVOYAGER_BASE_URL WEBVOYAGER_API_KEY; do
  if [[ -z ${!required_name:-} ]]; then
    echo "missing $required_name in credentials file" >&2
    exit 2
  fi
  if [[ ${!required_name} == *replace-me* || ${!required_name} == *your-openai-compatible* ]]; then
    echo "placeholder value remains for $required_name" >&2
    exit 2
  fi
done

shard_tag=$(printf '%02d' "$shard_index")
run_root="$project_root/runs/native_benchmarks/$run_label"
shard_root="$run_root/shards/shard-$shard_tag"
output_root="$shard_root/interactweb"
data_path="$project_root/data/interactweb_bench/shards-full-08/shard-${shard_tag}-of-08.jsonl"
expected=$(wc -l < "$data_path")

mkdir -p "$shard_root"
exec > >(tee -a "$shard_root/job.log") 2>&1
echo "[interactweb-official] shard=$shard_tag started_at=$(date -u +%FT%TZ) host=$(hostname) expected=$expected"
echo "[interactweb-official] roles: builder=Qwen3.5-9B copilot=Qwen3.5-9B user=deepseek-v3.2 judge=gpt-5-mini"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

export PATH="$interact_bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$project_root/.local/runtime/interactweb/playwright"
export NO_PROXY=localhost,127.0.0.1
export no_proxy="$NO_PROXY"

npm ping --registry=https://registry.npmjs.org --fetch-timeout=30000 --fetch-retries=1
"$interact_python" "$project_root/evaluate/interactweb_bench/run_official.py" preflight
"$interact_python" "$project_root/evaluate/verify_official_sources.py" interactweb_bench

"$vllm_bin" serve "$model_path" \
  --host 0.0.0.0 \
  --port "$port" \
  --served-model-name Qwen3.5-9B \
  --gpu-memory-utilization 0.92 \
  --max-model-len 128000 \
  --max-num-seqs 1 \
  >"$shard_root/vllm.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 360); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >"$shard_root/models.json"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup" >&2
    tail -200 "$shard_root/vllm.log" >&2
    exit 1
  fi
  sleep 5
done
curl --noproxy '*' -fsS "http://127.0.0.1:$port/v1/models" >/dev/null
echo "[interactweb-official] local Qwen3.5-9B ready"

export OPENAILIKE_API_KEY=EMPTY
export OPENAILIKE_BASE_URL="http://127.0.0.1:$port/v1"
export OPENAILIKE_VLM_API_KEY=EMPTY
export OPENAILIKE_VLM_BASE_URL="http://127.0.0.1:$port/v1"
export BUILDER_API_KEY=EMPTY
export BUILDER_BASE_URL="http://127.0.0.1:$port/v1"
export COPILOT_API_KEY=EMPTY
export COPILOT_BASE_URL="http://127.0.0.1:$port/v1"

# Run the frozen released pipeline directly, including its UserSimulator and
# terminal WebVoyager evaluator rather than the deferred-evaluation adapter.
"$interact_python" \
  "$project_root/evaluate/interactweb_bench/upstream/src/experiment/run_simulation.py" \
  --data_path "$data_path" \
  --output_dir "$output_root" \
  --builder_model Qwen3.5-9B \
  --visual_copilot_model Qwen3.5-9B \
  --user_model deepseek-v3.2 \
  --webvoyager_model gpt-5-mini \
  --max_workers 1

"$interact_python" "$project_root/scripts/interactweb_bench/validate_official_shard.py" \
  --output-dir "$output_root" \
  --model Qwen3.5-9B \
  --data-path "$data_path"
echo "[interactweb-official] shard=$shard_tag completed_at=$(date -u +%FT%TZ)"
