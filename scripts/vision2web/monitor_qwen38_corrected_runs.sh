#!/usr/bin/env bash
set -uo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
tools_label=smartrecruiters-tools-qwen-correct-v3
self_verify_label=smartrecruiters-self-verify-qwen-correct-v3
server_run=qwen38-27b-v2w-official-v3
server_job=mmc-q38-27b-v2w-v3
deadline=$(( $(date +%s) + 43200 ))

tools_root="$project_root/runs/vision2web_scaffold_comparison/$tools_label"
self_verify_root="$project_root/runs/vision2web_scaffold_comparison/$self_verify_label"
tools_oh="$tools_root/agents/litellm_proxy__Qwen3.8-27B/vision2web/tools/frontend__smartrecruiters/result.json"
tools_cc="$tools_root/agents/Qwen3.8-27B/vision2web/claude_code/tools/frontend__smartrecruiters/result.json"
self_verify_oh="$self_verify_root/agents/litellm_proxy__Qwen3.8-27B/vision2web/self_verify/frontend__smartrecruiters/result.json"
self_verify_cc="$self_verify_root/agents/Qwen3.8-27B/vision2web/claude_code/self_verify/frontend__smartrecruiters/result.json"

mkdir -p "$tools_root"
exec > >(tee -a "$tools_root/corrected-run-monitor.log") 2>&1

if [[ -z ${HTTP_PROXY:-${http_proxy:-}} || -z ${HTTPS_PROXY:-${https_proxy:-}} ]]; then
    echo "[monitor] proxy variables are missing; refusing to start"
    exit 2
fi

cd "$project_root" || exit 1
echo "[monitor] started_at=$(date -u +%FT%TZ)"
self_verify_submitted=false

while (( $(date +%s) < deadline )); do
    if [[ $self_verify_submitted == false && -f $tools_oh && -f $tools_cc ]]; then
        if "$python" scripts/vision2web/submit_scaffold_comparison.py \
            --mode self_verify \
            --group local \
            --run-label "$self_verify_label" \
            --server-run "$server_run" \
            --job-prefix mmc-v2wq3s3 \
            --wall-time 14400; then
            self_verify_submitted=true
            echo "[monitor] submitted/resumed corrected self_verify runs at $(date -u +%FT%TZ)"
        else
            echo "[monitor] self_verify submission failed; retrying"
        fi
    fi

    tools_count=$(find "$tools_root/agents" -path '*/tools/frontend__smartrecruiters/result.json' -type f 2>/dev/null | wc -l)
    self_verify_count=$(find "$self_verify_root/agents" -path '*/self_verify/frontend__smartrecruiters/result.json' -type f 2>/dev/null | wc -l)
    echo "[monitor] $(date -u +%FT%TZ) tools=$tools_count/2 self_verify=$self_verify_count/2"

    if [[ $tools_count -eq 2 && $self_verify_count -eq 2 ]]; then
        env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
            -u http_proxy -u https_proxy -u all_proxy \
            NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
            no_proxy=compute.pjlab.org.cn,10.140.100.1 \
            clusterx stop -j "$server_job" --confirm || true
        echo "[monitor] all corrected runs have result.json; stopped server"
        exit 0
    fi
    sleep 120
done

echo "[monitor] deadline reached; results preserved and server left running for diagnosis"
exit 3
