"""Protocol version validation for node registration and job leasing.

Ensures backward compatibility and prevents version mismatches between
gateway and runner-agent.
"""

from __future__ import annotations

from typing import Final

# ============================================================================
# Supported Protocol Versions
# ============================================================================

# Node protocol versions (registration, heartbeat, capabilities)
SUPPORTED_PROTOCOL_VERSIONS: Final[frozenset[str]] = frozenset({
    "runner.v1",  # legacy — still accepted for backward compat
    "runner.v2",  # current — Go Runner / v3.43 baseline
})

# Job lease protocol versions (pull, renew, complete, fail)
SUPPORTED_LEASE_VERSIONS: Final[frozenset[str]] = frozenset({
    "job-lease.v1",  # legacy — still accepted
    "job-lease.v2",  # current — Go Runner / v3.43 baseline
})

# Current recommended versions (for new nodes)
CURRENT_PROTOCOL_VERSION: Final[str] = "runner.v2"   # v3.43 baseline
CURRENT_LEASE_VERSION: Final[str] = "job-lease.v2"  # v3.43 baseline


def is_protocol_version_supported(version: str | None) -> bool:
    """Check if node protocol version is supported.

    Args:
        version: Protocol version string (e.g., "runner.v1")

    Returns:
        True if supported, False otherwise

    Examples:
        >>> is_protocol_version_supported("runner.v1")
        True

        >>> is_protocol_version_supported("runner.v0")
        False

        >>> is_protocol_version_supported(None)
        False
    """
    if not version:
        return False
    return version.strip() in SUPPORTED_PROTOCOL_VERSIONS


def is_lease_version_supported(version: str | None) -> bool:
    """Check if job lease protocol version is supported.

    Args:
        version: Lease version string (e.g., "job-lease.v1")

    Returns:
        True if supported, False otherwise

    Examples:
        >>> is_lease_version_supported("job-lease.v1")
        True

        >>> is_lease_version_supported("job-lease.v0")
        False

        >>> is_lease_version_supported(None)
        False
    """
    if not version:
        return False
    return version.strip() in SUPPORTED_LEASE_VERSIONS


def validate_protocol_version(version: str | None) -> str:
    """Validate and normalize protocol version.

    Args:
        version: Protocol version string

    Returns:
        Normalized version string

    Raises:
        ValueError: If version is not supported

    Examples:
        >>> validate_protocol_version("runner.v1")
        'runner.v1'

        >>> validate_protocol_version("runner.v0")
        Traceback (most recent call last):
        ValueError: Unsupported protocol version: runner.v0
    """
    if not version:
        raise ValueError("Protocol version is required")

    normalized = version.strip()
    if not is_protocol_version_supported(normalized):
        raise ValueError(
            f"Unsupported protocol version: {normalized}. "
            f"Supported versions: {', '.join(sorted(SUPPORTED_PROTOCOL_VERSIONS))}"
        )

    return normalized


def validate_lease_version(version: str | None) -> str:
    """Validate and normalize lease version.

    Args:
        version: Lease version string

    Returns:
        Normalized version string

    Raises:
        ValueError: If version is not supported

    Examples:
        >>> validate_lease_version("job-lease.v1")
        'job-lease.v1'

        >>> validate_lease_version("job-lease.v0")
        Traceback (most recent call last):
        ValueError: Unsupported lease version: job-lease.v0
    """
    if not version:
        raise ValueError("Lease version is required")

    normalized = version.strip()
    if not is_lease_version_supported(normalized):
        raise ValueError(
            f"Unsupported lease version: {normalized}. "
            f"Supported versions: {', '.join(sorted(SUPPORTED_LEASE_VERSIONS))}"
        )

    return normalized


def get_version_compatibility_info() -> dict[str, object]:
    """Get version compatibility information for API responses.

    Returns:
        Dictionary with version compatibility info
    """
    return {
        "current_protocol_version": CURRENT_PROTOCOL_VERSION,
        "current_lease_version": CURRENT_LEASE_VERSION,
        "supported_protocol_versions": sorted(SUPPORTED_PROTOCOL_VERSIONS),
        "supported_lease_versions": sorted(SUPPORTED_LEASE_VERSIONS),
    }
