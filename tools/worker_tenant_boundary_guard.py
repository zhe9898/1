from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.workers.mqtt_worker import (  # noqa: E402
    _resolve_event_tenant_id,
    export_mqtt_worker_tenant_contract,
    process_event,
)


async def _async_violations() -> list[str]:
    violations: list[str] = []
    event = {
        "type": "new",
        "after": {
            "id": "event-123",
            "has_snapshot": True,
            "label": "person",
            "camera": "front",
            "snapshot": base64.b64encode(b"fake-image-bytes").decode("utf-8"),
        },
    }
    with (
        patch("backend.workers.mqtt_worker._async_session_factory") as session_factory,
        patch("backend.workers.mqtt_worker.logger.error") as log_error,
    ):
        await process_event(event)
    if session_factory.called:
        violations.append("backend.workers.mqtt_worker.process_event:tenantless events must be dropped before opening a DB session")
    if log_error.call_count != 1:
        violations.append("backend.workers.mqtt_worker.process_event:tenantless events must emit a single error log")
    return violations


def worker_tenant_boundary_violations() -> list[str]:
    violations: list[str] = []
    if _resolve_event_tenant_id({"tenant_id": "tenant-event"}, {"tenant_id": " tenant-after "}) != "tenant-after":
        violations.append("backend.workers.mqtt_worker._resolve_event_tenant_id:after payload tenant must take precedence")
    if _resolve_event_tenant_id({"tenant_id": " tenant-event "}, {}) != "tenant-event":
        violations.append("backend.workers.mqtt_worker._resolve_event_tenant_id:event payload tenant must be normalized")
    if _resolve_event_tenant_id({}, {}) is not None:
        violations.append("backend.workers.mqtt_worker._resolve_event_tenant_id:missing tenant payload must not fall back to default")
    violations.extend(asyncio.run(_async_violations()))
    return violations


def main() -> int:
    violations = worker_tenant_boundary_violations()
    if not violations:
        return 0
    print("worker tenant boundary violations detected:")
    print(export_mqtt_worker_tenant_contract())
    for violation in violations:
        print(violation)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
