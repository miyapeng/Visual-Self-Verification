#!/usr/bin/env bash
set -uo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
run_root="$project_root/runs/vision2web_scaffold_comparison/smartrecruiters-official-prompt-sixway-v1"
rerun_root="$project_root/runs/vision2web_scaffold_comparison/smartrecruiters-self-verify-qwen-r2"
site_root="$project_root/reports/vision2web_scaffold_model_comparison"
python=/data/miyapeng/miniconda3/envs/mmcode/bin/python
server_job=mmc-q38-27b-v2w-op
deadline=$(( $(date +%s) + 28800 ))

qwen_oh="$run_root/agents/litellm_proxy__Qwen3.8-27B/vision2web/tools/frontend__smartrecruiters/result.json"
qwen_cc="$run_root/agents/Qwen3.8-27B/vision2web/claude_code/tools/frontend__smartrecruiters/result.json"
rerun_qwen_oh="$rerun_root/agents/litellm_proxy__Qwen3.8-27B/vision2web/self_verify/frontend__smartrecruiters/result.json"
rerun_qwen_cc="$rerun_root/agents/Qwen3.8-27B/vision2web/claude_code/self_verify/frontend__smartrecruiters/result.json"
server_stopped=false
rerun_submitted=false

mkdir -p "$run_root"
exec > >(tee -a "$run_root/monitor.log") 2>&1

cd "$project_root" || exit 1
echo "[monitor] started_at=$(date -u +%FT%TZ)"

while (( $(date +%s) < deadline )); do
    PYTHONPATH=src "$python" scripts/vision2web/build_combined_scaffold_comparison_site.py \
        >/dev/null 2>&1 || echo "[monitor] site rebuild failed at $(date -u +%FT%TZ)"

    if [[ $rerun_submitted == false && -f $qwen_oh && -f $qwen_cc ]]; then
        if PYTHONPATH=src "$python" scripts/vision2web/submit_scaffold_comparison.py \
            --mode self_verify \
            --group local \
            --run-label smartrecruiters-self-verify-qwen-r2 \
            --server-run qwen38-27b-v2w-official-prompt \
            --job-prefix mmc-v2wsv2 \
            --wall-time 14400; then
            rerun_submitted=true
            echo "[monitor] submitted/resumed Qwen self_verify reruns at $(date -u +%FT%TZ)"
        else
            echo "[monitor] Qwen self_verify submission failed; retrying later"
        fi
    fi

    if [[ $server_stopped == false && -f $rerun_qwen_oh && -f $rerun_qwen_cc ]]; then
        env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
            -u http_proxy -u https_proxy -u all_proxy \
            NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
            no_proxy=compute.pjlab.org.cn,10.140.100.1 \
            clusterx stop -j "$server_job" --confirm || true
        server_stopped=true
        echo "[monitor] stopped shared Qwen server after Qwen reruns at $(date -u +%FT%TZ)"
    fi

    result_count=$(find "$run_root/agents" -path '*/tools/frontend__smartrecruiters/result.json' -type f 2>/dev/null | wc -l)
    rerun_count=$(find "$rerun_root/agents" -path '*/self_verify/frontend__smartrecruiters/result.json' -type f 2>/dev/null | wc -l)
    echo "[monitor] $(date -u +%FT%TZ) tools_results=$result_count/6 qwen_rerun_results=$rerun_count/2"
    if [[ $result_count -eq 6 && $rerun_count -eq 2 ]]; then
        break
    fi
    sleep 120
done

PYTHONPATH=src "$python" scripts/vision2web/build_combined_scaffold_comparison_site.py \
    >/dev/null 2>&1 || true

if [[ $server_stopped == false ]]; then
    env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
        -u http_proxy -u https_proxy -u all_proxy \
        NO_PROXY=compute.pjlab.org.cn,10.140.100.1 \
        no_proxy=compute.pjlab.org.cn,10.140.100.1 \
        clusterx stop -j "$server_job" --confirm || true
    echo "[monitor] stopped shared Qwen server on monitor exit"
fi

echo "[monitor] finished_at=$(date -u +%FT%TZ)"
