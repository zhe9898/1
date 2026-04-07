#!/bin/bash
# Generate build metadata for reproducible builds
# This script should be run during the build process

set -euo pipefail

BUILD_METADATA_FILE="${1:-build-metadata.json}"

# Get git information
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "none")
GIT_DIRTY=$(git diff --quiet 2>/dev/null && echo "false" || echo "true")

# Get build timestamp (ISO 8601 format)
BUILD_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Get Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 || echo "unknown")

# Get Node version
NODE_VERSION=$(node --version 2>/dev/null | sed 's/v//' || echo "unknown")

# Generate JSON metadata
cat > "$BUILD_METADATA_FILE" << EOF
{
  "build": {
    "timestamp": "$BUILD_TIMESTAMP",
    "reproducible": true
  },
  "git": {
    "commit": "$GIT_COMMIT",
    "branch": "$GIT_BRANCH",
    "tag": "$GIT_TAG",
    "dirty": $GIT_DIRTY
  },
  "environment": {
    "python_version": "$PYTHON_VERSION",
    "node_version": "$NODE_VERSION"
  },
  "dependencies": {
    "backend_lock": "backend/requirements-ci.lock",
    "frontend_lock": "frontend/package-lock.json"
  }
}
EOF

echo "Build metadata generated: $BUILD_METADATA_FILE"
cat "$BUILD_METADATA_FILE"
