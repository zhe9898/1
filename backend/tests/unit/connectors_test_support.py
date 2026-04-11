from __future__ import annotations

import datetime

from backend.models.connector import Connector
from backend.tests.unit.shared_db_test_support import scalar_result


def connector_utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def build_connector(**overrides: object) -> Connector:
    now = connector_utcnow()
    connector = Connector(
        tenant_id="tenant-a",
        connector_id="connector-a",
        name="Connector A",
        kind="http",
        status="healthy",
        endpoint="https://example.test",
        profile="manual",
        config={},
        last_test_ok=None,
        last_test_status=None,
        last_test_message=None,
        last_test_at=None,
        last_invoke_status=None,
        last_invoke_message=None,
        last_invoke_job_id=None,
        last_invoke_at=None,
        created_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


def first_scalar_result(value: object | None):
    return scalar_result(value)
