#!/bin/bash
# ZEN70 offline seed export: package the full Git repository and digest-pinned
# runtime images into one tarball for deterministic offline bootstrap.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SEED_DIR="$REPO_ROOT/zen70-seed"
SEED_TAR="$REPO_ROOT/zen70-seed.tar.gz"
REGISTRY="${ZEN70_REGISTRY:-localhost:5000}"
IMAGES_LIST="${SCRIPT_DIR}/images.list"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Creating offline seed package...${NC}"

if [ ! -f "$IMAGES_LIST" ]; then
    echo -e "${RED}Error: images.list not found at $IMAGES_LIST${NC}" >&2
    exit 1
fi

rm -rf "$SEED_DIR"
mkdir -p "$SEED_DIR"/{git-repo,images}

echo -e "${YELLOW}Cloning Git repository (full clone)...${NC}"
cd "$REPO_ROOT"
if [ -d .git ]; then
    git clone --no-hardlinks . "$SEED_DIR/git-repo"
else
    echo -e "${RED}Error: not a git repository (no .git)${NC}" >&2
    exit 1
fi

cp "$IMAGES_LIST" "$SEED_DIR/images.list"
cp "$SCRIPT_DIR/README-registry.md" "$SEED_DIR/" 2>/dev/null || true

echo -e "${YELLOW}Saving Docker images...${NC}"
while IFS= read -r image || [ -n "$image" ]; do
    image="$(echo "$image" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [ -z "$image" ] || [[ "$image" =~ ^# ]]; then
        continue
    fi

    if [[ "$image" != *@sha256:* ]]; then
        echo "Error: image ref must be digest pinned: $image" >&2
        exit 1
    fi

    base_ref="${image%%@sha256:*}"
    local_tag="${REGISTRY}/${base_ref}"
    if ! docker image inspect "$local_tag" &>/dev/null; then
        echo "  Pulling $local_tag from registry..."
        if ! docker pull "$local_tag" 2>/dev/null; then
            echo "  Pulling $image from origin (registry miss)..."
            docker pull "$image"
            image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
            if [ -z "$image_id" ]; then
                image_id="$(docker image inspect --format '{{.Id}}' "$base_ref" 2>/dev/null || true)"
            fi
            if [ -z "$image_id" ]; then
                echo "  Error: failed to resolve image id for $image" >&2
                exit 1
            fi
            docker tag "$image_id" "$local_tag"
        fi
    fi
    safe_name="$(echo "$base_ref" | tr '/' '_' | tr ':' '_' )"
    echo "  Saving $local_tag -> images/${safe_name}.tar"
    docker save "$local_tag" -o "$SEED_DIR/images/${safe_name}.tar"
done < "$IMAGES_LIST"

cat > "$SEED_DIR/bootstrap-offline.sh" << 'BOOTEOF'
#!/bin/bash
set -e
echo "ZEN70 offline bootstrap"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Loading Docker images..."
for img in "$SCRIPT_DIR/images/"*.tar; do
    [ -f "$img" ] || continue
    echo "  Loading $img"
    docker load -i "$img"
done

echo "Running bootstrap (offline)..."
cd "$SCRIPT_DIR/git-repo"
if [ -f scripts/bootstrap.py ]; then
    python3 scripts/bootstrap.py --offline
else
    echo "Error: scripts/bootstrap.py not found" >&2
    exit 1
fi

echo "Offline bootstrap completed"
BOOTEOF
chmod +x "$SEED_DIR/bootstrap-offline.sh"

echo -e "${YELLOW}Packaging $SEED_TAR...${NC}"
cd "$REPO_ROOT"
tar -czf "$SEED_TAR" zen70-seed

echo -e "${GREEN}Seed package created: $SEED_TAR${NC}"
ls -lh "$SEED_TAR"
