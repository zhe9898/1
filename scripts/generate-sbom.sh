#!/bin/bash
# Generate Software Bill of Materials (SBOM) for reproducible builds
# Uses CycloneDX format

set -euo pipefail

SBOM_OUTPUT="${1:-sbom.json}"

echo "Generating SBOM..."

# Check if cyclonedx-bom is installed
if ! command -v cyclonedx-py &> /dev/null; then
    echo "Installing cyclonedx-bom..."
    pip install cyclonedx-bom
fi

# Generate Python SBOM
echo "Generating Python dependencies SBOM..."
cyclonedx-py requirements \
    --input-file backend/requirements-ci.lock \
    --output-file "${SBOM_OUTPUT}" \
    --format json

echo "SBOM generated: ${SBOM_OUTPUT}"

# Validate SBOM
if command -v jq &> /dev/null; then
    echo "SBOM components count: $(jq '.components | length' "${SBOM_OUTPUT}")"
fi
