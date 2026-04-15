"""Higher-level host service contracts compiled into generic host entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from scripts.iac_core.host_runtime import validate_host_service_config

_DEFAULT_GATEWAY_GRACEFUL_SHUTDOWN_SECONDS = 15


@dataclass(frozen=True)
class GatewayServeContract:
    app: str
    host: str
    port: int
    workers: int
    graceful_shutdown_seconds: int

    def to_entrypoint(self) -> dict[str, object]:
        return {
            "type": "python-module",
            "module": "uvicorn",
            "args": [
                self.app,
                "--host",
                self.host,
                "--port",
                str(self.port),
                "--workers",
                str(self.workers),
                "--timeout-graceful-shutdown",
                str(self.graceful_shutdown_seconds),
            ],
        }


def validate_host_service_contract(name: str, svc: Mapping[str, Any]) -> list[str]:
    """Validate service-level host contracts before generic runtime normalization."""
    gateway_serve, errors = _parse_gateway_serve_contract(name, svc)
    if errors:
        return errors
    if gateway_serve is None:
        return validate_host_service_config(name, svc)
    return validate_host_service_config(name, normalize_host_service_contract(name, svc))


def normalize_host_service_contract(name: str, svc: Mapping[str, Any]) -> dict[str, Any]:
    """Compile higher-level service semantics into a generic host-runtime contract."""
    gateway_serve, errors = _parse_gateway_serve_contract(name, svc)
    if errors:
        msg = "; ".join(errors)
        raise ValueError(msg)
    normalized = dict(svc)
    if gateway_serve is None:
        return normalized
    normalized.pop("serve", None)
    normalized["entrypoint"] = gateway_serve.to_entrypoint()
    normalized["port"] = gateway_serve.port
    return normalized


def _parse_gateway_serve_contract(
    name: str,
    svc: Mapping[str, Any],
) -> tuple[GatewayServeContract | None, list[str]]:
    serve = svc.get("serve")
    if serve is None:
        return None, []

    errors: list[str] = []
    if svc.get("runtime") != "host":
        errors.append(f"services.{name}.serve requires runtime=host")
    if name != "gateway":
        errors.append(f"services.{name}.serve is only supported for gateway")
    if "entrypoint" in svc:
        errors.append(f"services.{name}.serve cannot be combined with entrypoint")
    if not isinstance(serve, Mapping):
        errors.append(f"services.{name}.serve must be a mapping")
        return None, errors

    engine = str(serve.get("engine") or "").strip().lower()
    if engine != "uvicorn":
        errors.append(f"services.{name}.serve.engine must be uvicorn")

    app = str(serve.get("app") or "").strip()
    if not app:
        errors.append(f"services.{name}.serve.app is required")

    host = str(serve.get("host") or "").strip()
    if not host:
        errors.append(f"services.{name}.serve.host is required")

    port = _parse_port(serve.get("port"), field=f"services.{name}.serve.port", errors=errors)
    workers = _parse_positive_int(
        serve.get("workers"),
        field=f"services.{name}.serve.workers",
        errors=errors,
    )
    graceful_shutdown_seconds = _parse_positive_int(
        serve.get("graceful_shutdown_seconds", _DEFAULT_GATEWAY_GRACEFUL_SHUTDOWN_SECONDS),
        field=f"services.{name}.serve.graceful_shutdown_seconds",
        errors=errors,
    )

    top_level_port = svc.get("port")
    if top_level_port is not None:
        normalized_port = _parse_port(top_level_port, field=f"services.{name}.port", errors=errors)
        if normalized_port is not None and port is not None and normalized_port != port:
            errors.append(
                f"services.{name}.port must match services.{name}.serve.port when both are set"
            )

    if errors or port is None or workers is None or graceful_shutdown_seconds is None:
        return None, errors

    return GatewayServeContract(
        app=app,
        host=host,
        port=port,
        workers=workers,
        graceful_shutdown_seconds=graceful_shutdown_seconds,
    ), []


def _parse_positive_int(raw: Any, *, field: str, errors: list[str]) -> int | None:
    value = _parse_int(raw, field=field, errors=errors)
    if value is None:
        return None
    if value <= 0:
        errors.append(f"{field} must be greater than 0")
        return None
    return value


def _parse_port(raw: Any, *, field: str, errors: list[str]) -> int | None:
    value = _parse_int(raw, field=field, errors=errors)
    if value is None:
        return None
    if not 1 <= value <= 65535:
        errors.append(f"{field} must be between 1 and 65535")
        return None
    return value


def _parse_int(raw: Any, *, field: str, errors: list[str]) -> int | None:
    if raw in (None, ""):
        errors.append(f"{field} is required")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        errors.append(f"{field} must be an integer")
        return None
