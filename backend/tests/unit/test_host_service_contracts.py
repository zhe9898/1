from __future__ import annotations

from scripts.iac_core.host_service_contracts import (
    normalize_host_service_contract,
    validate_host_service_contract,
)


def _gateway_serve_service() -> dict[str, object]:
    return {
        "runtime": "host",
        "working_dir": ".",
        "serve": {
            "engine": "uvicorn",
            "app": "backend.control_plane.app.entrypoint:app",
            "host": "0.0.0.0",
            "port": 8000,
            "workers": 2,
            "graceful_shutdown_seconds": 15,
        },
    }


def test_normalize_gateway_serve_contract_compiles_to_generic_entrypoint() -> None:
    normalized = normalize_host_service_contract("gateway", _gateway_serve_service())

    assert "serve" not in normalized
    assert normalized["port"] == 8000
    assert normalized["entrypoint"] == {
        "type": "python-module",
        "module": "uvicorn",
        "args": [
            "backend.control_plane.app.entrypoint:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--workers",
            "2",
            "--timeout-graceful-shutdown",
            "15",
        ],
    }


def test_validate_host_service_contract_rejects_dual_track_gateway_runtime_shape() -> None:
    service = _gateway_serve_service()
    service["entrypoint"] = {"type": "python-module", "module": "uvicorn", "args": []}

    errors = validate_host_service_contract("gateway", service)

    assert errors == ["services.gateway.serve cannot be combined with entrypoint"]


def test_validate_host_service_contract_rejects_serve_outside_gateway() -> None:
    errors = validate_host_service_contract("control-worker", _gateway_serve_service())

    assert errors == ["services.control-worker.serve is only supported for gateway"]
