from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from backend.kernel.contracts.errors import zen

ALLOWED_CONNECTOR_ENDPOINT_SCHEMES = frozenset({"http", "https", "mqtt", "tcp"})
BLOCKED_CONNECTOR_ENDPOINT_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)


def normalize_connector_endpoint(endpoint: str | None) -> str | None:
    if endpoint is None:
        return None
    normalized = endpoint.strip()
    return normalized or None


def validate_connector_endpoint(endpoint: str | None, *, connector_id: str | None = None) -> str | None:
    normalized = normalize_connector_endpoint(endpoint)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme not in ALLOWED_CONNECTOR_ENDPOINT_SCHEMES or not hostname:
        raise zen(
            "ZEN-CONN-4002",
            "Connector endpoint must be a valid routable URL",
            status_code=400,
            recovery_hint="Use an http/https/mqtt/tcp endpoint with an explicit host",
            details={"connector_id": connector_id, "endpoint": normalized},
        )
    if parsed.username or parsed.password:
        raise zen(
            "ZEN-CONN-4002",
            "Connector endpoint must not embed credentials",
            status_code=400,
            recovery_hint="Store connector credentials in config instead of the URL",
            details={"connector_id": connector_id, "endpoint": normalized},
        )
    if hostname in BLOCKED_CONNECTOR_ENDPOINT_HOSTS or hostname.endswith(".localhost"):
        raise zen(
            "ZEN-CONN-4002",
            "Connector endpoint must not target local metadata or loopback hosts",
            status_code=400,
            recovery_hint="Use a routable integration endpoint instead of a host-local address",
            details={"connector_id": connector_id, "endpoint": normalized, "host": hostname},
        )
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    if not parsed_ip.is_global:
        raise zen(
            "ZEN-CONN-4002",
            "Connector endpoint must not target private, loopback, or link-local IP ranges",
            status_code=400,
            recovery_hint="Publish the integration through a routable address instead of an internal IP literal",
            details={"connector_id": connector_id, "endpoint": normalized, "host": hostname},
        )
    return normalized
