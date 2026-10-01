#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/data/miyapeng/mmcode/MultimodalCode
UPSTREAM_ROOT=$PROJECT_ROOT/evaluate/vision2web/upstream
AUTHFILE=${1:-/tmp/swe-mm-ccr-auth.json}
BUILD_ROOT=${VISION2WEB_BUILDAH_ROOT:-/tmp/vision2web-buildah-root}
RUN_ROOT=${VISION2WEB_BUILDAH_RUNROOT:-/tmp/vision2web-buildah-runroot}
CONTEXT=${VISION2WEB_BUILDAH_CONTEXT:-/tmp/vision2web-buildah-context}
TARGET=registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web
TAG=official
PINNED_TAG=official-577f939
STATE="$PROJECT_ROOT/data/vision2web/image.json"
SKOPEO="$PROJECT_ROOT/.envs/imgsync/bin/skopeo"

export CONTAINERS_REGISTRIES_CONF="$PROJECT_ROOT/scripts/swe_mm/registries.conf"
export NO_PROXY="registry.pjlab.org.cn,xceph-inside.pjlab.org.cn,localhost,127.0.0.1${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

test -r "$AUTHFILE"
test -r "$UPSTREAM_ROOT/docker/Dockerfile.sandbox"
mkdir -p "$BUILD_ROOT" "$RUN_ROOT" "$CONTEXT" "$(dirname "$STATE")"

# Preserve the official recipe byte-for-byte except for selecting a reachable,
# digest-equivalent Ubuntu registry mirror. The base digest was independently
# matched against a second mirror before this build.
sed 's|^FROM ubuntu:22.04$|FROM docker.1ms.run/library/ubuntu:22.04|' \
    "$UPSTREAM_ROOT/docker/Dockerfile.sandbox" > "$CONTEXT/Containerfile"

echo "[build] official Vision2Web sandbox (upstream 577f939)"
buildah \
    --root "$BUILD_ROOT" \
    --runroot "$RUN_ROOT" \
    --storage-driver vfs \
    bud \
    --isolation chroot \
    --network host \
    --layers \
    --label org.opencontainers.image.title=Vision2Web-sandbox \
    --label org.opencontainers.image.source=https://github.com/zai-org/Vision2Web \
    --label org.opencontainers.image.revision=577f9397b3db8fc6d828adde254a830caa65d515 \
    --tag "localhost/vision2web:$TAG" \
    "$CONTEXT"

echo "[push] $TARGET:$TAG"
buildah \
    --root "$BUILD_ROOT" \
    --runroot "$RUN_ROOT" \
    --storage-driver vfs \
    push --authfile "$AUTHFILE" \
    "localhost/vision2web:$TAG" "docker://$TARGET:$TAG"

echo "[tag] $TARGET:$PINNED_TAG"
"$SKOPEO" copy --authfile "$AUTHFILE" \
    "docker://$TARGET:$TAG" "docker://$TARGET:$PINNED_TAG"

DIGEST=$("$SKOPEO" inspect --authfile "$AUTHFILE" \
    --format '{{.Digest}}' "docker://$TARGET:$TAG")
cat > "$STATE" <<EOF
{
  "base_digest": "sha256:3b06811b2afd352be909dd088a004166d665dc76d38b13eada33522a9d915c6f",
  "builder": "buildah-chroot-vfs",
  "image": "$TARGET:$TAG",
  "immutable_image": "$TARGET:$PINNED_TAG",
  "source_commit": "577f9397b3db8fc6d828adde254a830caa65d515",
  "target_digest": "$DIGEST"
}
EOF
echo "[done] $TARGET:$TAG@$DIGEST"
