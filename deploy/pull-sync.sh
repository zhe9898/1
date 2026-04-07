#!/bin/bash
# ZEN70 镜像同步脚本：从 Docker Hub（或原仓库）拉取镜像，打私有仓库 tag 并推送
# 支持增量：已存在且 digest 一致可跳过（初期简化为每次全量拉取）
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${ZEN70_REGISTRY:-localhost:5000}"
IMAGES_LIST="${SCRIPT_DIR}/images.list"
LOG_FILE="${SCRIPT_DIR}/pull-sync.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
}

log_err() {
    echo -e "${RED}$(date '+%Y-%m-%d %H:%M:%S') $*${NC}" | tee -a "$LOG_FILE"
}

log_ok() {
    echo -e "${GREEN}$(date '+%Y-%m-%d %H:%M:%S') $*${NC}" | tee -a "$LOG_FILE"
}

if [ ! -f "$IMAGES_LIST" ]; then
    log_err "Error: images.list not found at $IMAGES_LIST"
    exit 1
fi

if ! command -v docker &>/dev/null; then
    log_err "Error: docker not found"
    exit 1
fi

# 检查私有仓库是否可访问（/v2/ 返回 200 或 401）
code="$(curl -s -o /dev/null -w "%{http_code}" "http://${REGISTRY}/v2/" 2>/dev/null)" || true
if [ "$code" != "200" ] && [ "$code" != "401" ]; then
    log_err "Registry at ${REGISTRY} is not reachable (got ${code}). Run deploy/registry-setup.sh first."
    exit 1
fi

log "Starting image sync to ${REGISTRY}"

failed=0
while IFS= read -r image || [ -n "$image" ]; do
    image="$(echo "$image" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [ -z "$image" ] || [[ "$image" =~ ^# ]]; then
        continue
    fi

    if [[ "$image" != *@sha256:* ]]; then
        log_err "Image ref must be digest pinned: $image"
        failed=1
        break
    fi

    base_ref="${image%%@sha256:*}"

    log "Pulling ${image}..."
    if ! docker pull "$image" >> "$LOG_FILE" 2>&1; then
        log_err "Failed to pull ${image}"
        failed=1
        break
    fi

    image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
    if [ -z "$image_id" ]; then
        image_id="$(docker image inspect --format '{{.Id}}' "$base_ref" 2>/dev/null || true)"
    fi
    if [ -z "$image_id" ]; then
        log_err "Failed to resolve image id for ${image}"
        failed=1
        break
    fi

    local_tag="${REGISTRY}/${base_ref}"
    log "Tagging ${image} -> ${local_tag}"
    docker tag "$image_id" "$local_tag"

    log "Pushing ${local_tag}..."
    if ! docker push "$local_tag" >> "$LOG_FILE" 2>&1; then
        log_err "Failed to push ${local_tag}"
        failed=1
        break
    fi
    log_ok "Synced ${image}"
done < "$IMAGES_LIST"

if [ "$failed" -eq 1 ]; then
    log_err "Image sync failed. Check $LOG_FILE"
    exit 1
fi

log_ok "All images synced successfully"
