from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

_BLOCKED_PUBLIC_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)
_LOOPBACK_CONTROL_HOSTNAMES = frozenset({"localhost"})
_INTEGER_METRIC_RE = re.compile(r"^\d{1,4}$")
_RESTIC_DEFAULT_ROOT_NAMES = ("data", "runtime", "config", "backups", "logs")
_DEFAULT_WEBPUSH_HOST_SUFFIXES = (
    ".fcm.googleapis.com",
    ".push.services.mozilla.com",
    ".updates.push.services.mozilla.com",
    ".notify.windows.com",
    ".web.push.apple.com",
)


def _is_filesystem_root(path: Path) -> bool:
    return path == Path(path.anchor)


def normalize_nonempty_string(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def normalize_local_filesystem_path(value: str, *, field_name: str) -> str:
    normalized = normalize_nonempty_string(value, field_name=field_name)
    parsed = urlparse(normalized)
    has_windows_drive = len(parsed.scheme) == 1 and normalized[1:3] in {":\\", ":/"}
    if (parsed.scheme or parsed.netloc) and not has_windows_drive:
        raise ValueError(f"{field_name} must be a local filesystem path, not a URI")
    return normalized


def _is_public_ip(hostname: str) -> bool:
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return bool(parsed_ip.is_global)


def normalize_public_network_url(
    value: str,
    *,
    field_name: str,
    allowed_schemes: set[str] | None = None,
    require_https: bool = False,
) -> str:
    allowed = allowed_schemes or {"http", "https"}
    normalized = normalize_nonempty_string(value, field_name=field_name)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if scheme not in allowed or not hostname:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be a valid routable URL using one of: {allowed_text}")
    if require_https and scheme != "https":
        raise ValueError(f"{field_name} must use https")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not embed credentials")
    if hostname in _BLOCKED_PUBLIC_HOSTNAMES or hostname.endswith(".localhost"):
        raise ValueError(f"{field_name} must not target loopback or metadata hosts")
    if not _is_public_ip(hostname):
        raise ValueError(f"{field_name} must not target private, loopback, or link-local IP ranges")
    return normalized


def normalize_webpush_endpoint(value: str, *, field_name: str) -> str:
    normalized = normalize_public_network_url(
        value,
        field_name=field_name,
        allowed_schemes={"https"},
        require_https=True,
    )
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not parsed.path or parsed.path == "/":
        raise ValueError(f"{field_name} must include a provider-issued subscription path")

    configured_suffixes = split_csv_values(
        os.getenv("WEBPUSH_ALLOWED_HOST_SUFFIXES", ""),
        field_name="WEBPUSH_ALLOWED_HOST_SUFFIXES",
    ) if os.getenv("WEBPUSH_ALLOWED_HOST_SUFFIXES", "").strip() else list(_DEFAULT_WEBPUSH_HOST_SUFFIXES)

    normalized_suffixes = tuple(
        suffix if suffix.startswith(".") else f".{suffix}"
        for suffix in configured_suffixes
    )
    if not any(hostname == suffix[1:] or hostname.endswith(suffix) for suffix in normalized_suffixes):
        raise ValueError(f"{field_name} must target an approved Web Push provider")
    return normalized


def normalize_managed_uri(
    value: str,
    *,
    field_name: str,
    allowed_schemes: set[str],
    allow_public_http: bool = False,
    require_suffix: str | None = None,
) -> str:
    normalized = normalize_nonempty_string(value, field_name=field_name)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ValueError(f"{field_name} must use an approved URI scheme")
    if scheme in {"http", "https"}:
        allowed_network_schemes = {candidate for candidate in allowed_schemes if candidate in {"http", "https"}}
        if not allow_public_http or scheme not in allowed_network_schemes:
            allowed_text = ", ".join(sorted(allowed_schemes))
            raise ValueError(f"{field_name} must use one of: {allowed_text}")
        normalized = normalize_public_network_url(
            normalized,
            field_name=field_name,
            allowed_schemes=allowed_network_schemes,
        )
    elif scheme not in allowed_schemes:
        allowed_text = ", ".join(sorted(allowed_schemes))
        raise ValueError(f"{field_name} must use one of: {allowed_text}")
    elif parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not embed credentials")
    elif not parsed.netloc:
        raise ValueError(f"{field_name} must include a target location")
    if require_suffix and not parsed.path.lower().endswith(require_suffix.lower()):
        raise ValueError(f"{field_name} must end with {require_suffix}")
    return normalized


def normalize_loopback_control_url(
    value: str,
    *,
    field_name: str,
    allowed_schemes: set[str] | None = None,
    required_path: str | None = None,
) -> str:
    allowed = allowed_schemes or {"http", "https"}
    normalized = normalize_nonempty_string(value, field_name=field_name)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if scheme not in allowed or not hostname:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be a valid loopback control URL using one of: {allowed_text}")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include query strings or fragments")
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname not in _LOOPBACK_CONTROL_HOSTNAMES:
            raise ValueError(f"{field_name} must target a loopback host") from None
    else:
        if not parsed_ip.is_loopback:
            raise ValueError(f"{field_name} must target a loopback IP")
    if required_path is not None and parsed.path.rstrip("/") != required_path.rstrip("/"):
        raise ValueError(f"{field_name} must target {required_path}")
    return normalized


def default_restic_allowed_roots(project_root: Path) -> list[Path]:
    return [project_root / name for name in _RESTIC_DEFAULT_ROOT_NAMES]


def parse_allowed_roots(
    raw_value: str,
    *,
    field_name: str,
    default_roots: Iterable[Path] = (),
) -> list[Path]:
    roots: list[Path] = []
    if raw_value.strip():
        candidates = [part.strip() for part in raw_value.split(os.pathsep) if part.strip()]
        if not candidates:
            raise ValueError(f"{field_name} must contain at least one absolute path")
        for candidate in candidates:
            path = Path(candidate).expanduser().resolve(strict=False)
            if not path.is_absolute():
                raise ValueError(f"{field_name} entries must be absolute paths")
            if _is_filesystem_root(path):
                raise ValueError(f"{field_name} must not allow the filesystem root")
            roots.append(path)
        return roots

    for candidate in default_roots:
        path = Path(candidate).expanduser().resolve(strict=False)
        if _is_filesystem_root(path):
            continue
        roots.append(path)
    if not roots:
        raise ValueError(f"{field_name} has no allowed roots configured")
    return roots


def split_csv_values(raw_value: str, *, field_name: str) -> list[str]:
    values = [part.strip() for part in raw_value.split(",") if part.strip()]
    if not values:
        raise ValueError(f"{field_name} must contain at least one value")
    return values


def resolve_path_within_roots(
    value: str,
    *,
    field_name: str,
    roots: Iterable[Path],
    must_exist: bool,
) -> Path:
    normalized = normalize_local_filesystem_path(value, field_name=field_name)
    candidate = Path(normalized).expanduser().resolve(strict=False)
    if not candidate.is_absolute():
        raise ValueError(f"{field_name} must resolve to an absolute path")
    for root in roots:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if must_exist and not candidate.exists():
            raise ValueError(f"{field_name} path does not exist: {candidate}")
        return candidate
    allowed = [str(root) for root in roots]
    raise ValueError(f"{field_name} must stay within allowed roots: {allowed}")


def normalize_metric_integer(
    value: str,
    *,
    field_name: str,
    min_value: int = 0,
    max_value: int = 9999,
) -> str | None:
    trimmed = value.strip()
    if not _INTEGER_METRIC_RE.fullmatch(trimmed):
        return None
    parsed = int(trimmed)
    if parsed < min_value or parsed > max_value:
        return None
    return str(parsed)
