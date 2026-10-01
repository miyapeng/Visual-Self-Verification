#!/usr/bin/env bash
set -euo pipefail

project_root=/data/miyapeng/mmcode/MultimodalCode
run_root="$project_root/runs/swe_mm_docker_smoke"
docker_root=/shared/swe-mm-docker
dataset="$project_root/data/swe_mm/dev/evaluator_private/instances.full.jsonl"
instance_id=${SWE_MM_SMOKE_INSTANCE:-markedjs__marked-2811}

mkdir -p "$run_root" "$docker_root"
exec > >(tee -a "$run_root/job.log") 2>&1

echo "[smoke] started_at=$(date -u +%FT%TZ)"
echo "[smoke] host=$(hostname) instance=$instance_id"
echo "[smoke] proxy_http_set=$([[ -n ${HTTP_PROXY:-${http_proxy:-}} ]] && echo yes || echo no)"
echo "[smoke] proxy_https_set=$([[ -n ${HTTPS_PROXY:-${https_proxy:-}} ]] && echo yes || echo no)"
echo "[smoke] capabilities"
grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status || true

export DEBIAN_FRONTEND=noninteractive
if ! command -v dockerd >/dev/null 2>&1 || ! command -v docker >/dev/null 2>&1; then
    echo "[smoke] installing Docker packages inside the ClusterX job"
    apt-get update
    apt-get install -y docker.io iptables ca-certificates
fi

export NO_PROXY="localhost,127.0.0.1,::1,${NO_PROXY:-${no_proxy:-}}"
export no_proxy="$NO_PROXY"

rm -f /var/run/docker.sock /var/run/docker.pid
echo "[smoke] starting dockerd with data root $docker_root"
dockerd \
    --host=unix:///var/run/docker.sock \
    --data-root="$docker_root" \
    >"$run_root/dockerd.log" 2>&1 &
dockerd_pid=$!

cleanup() {
    kill "$dockerd_pid" 2>/dev/null || true
    wait "$dockerd_pid" 2>/dev/null || true
}
trap cleanup EXIT

ready=false
for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
        ready=true
        break
    fi
    if ! kill -0 "$dockerd_pid" 2>/dev/null; then
        echo "[smoke] ERROR: dockerd exited before becoming ready"
        tail -200 "$run_root/dockerd.log" || true
        exit 1
    fi
    sleep 2
done

if [[ $ready != true ]]; then
    echo "[smoke] ERROR: dockerd did not become ready"
    tail -200 "$run_root/dockerd.log" || true
    exit 1
fi

echo "[smoke] Docker daemon is ready"
docker version
docker info --format 'driver={{.Driver}} root={{.DockerRootDir}} server={{.ServerVersion}}'

echo "[smoke] running hello-world"
docker run --rm hello-world

echo "[smoke] running one official SWE-MM gold evaluation"
cd "$project_root"
conda run -n swemm swebench eval \
    "$dataset" \
    --gold \
    --instance "$instance_id" \
    --run-id swe-mm-gold-smoke \
    --workers 1 \
    --timeout 3600 \
    --report-dir "$run_root/evaluation"

echo "[smoke] finished_at=$(date -u +%FT%TZ)"
echo "SWE_MM_DOCKER_SMOKE_OK"
