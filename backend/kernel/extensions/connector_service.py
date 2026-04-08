from __future__ import annotations

import datetime

from backend.kernel.extensions.connector_secret_service import ConnectorSecretService
from backend.models.connector import Connector


class ConnectorService:
    @staticmethod
    def upsert(
        connector: Connector | None,
        *,
        tenant_id: str,
        connector_id: str,
        name: str,
        kind: str,
        status: str,
        endpoint: str | None,
        profile: str,
        config: dict[str, object],
        now: datetime.datetime,
    ) -> tuple[Connector, str]:
        sealed_config = ConnectorSecretService.seal_config(
            config,
            tenant_id=tenant_id,
            connector_id=connector_id,
            kind=kind,
            profile=profile,
        )
        if connector is None:
            connector = Connector(
                tenant_id=tenant_id,
                connector_id=connector_id,
                name=name,
                kind=kind,
                status=status,
                endpoint=endpoint,
                profile=profile,
                config=sealed_config,
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
            return connector, "upserted"
        connector.name = name
        connector.kind = kind
        connector.status = status
        connector.endpoint = endpoint
        connector.profile = profile
        connector.config = sealed_config
        connector.updated_at = now
        return connector, "updated"

    @staticmethod
    def mark_invoked(
        connector: Connector,
        *,
        job_id: str,
        now: datetime.datetime,
        message: str = "job queued",
    ) -> None:
        connector.last_invoke_status = "pending"
        connector.last_invoke_message = message
        connector.last_invoke_job_id = job_id
        connector.last_invoke_at = now
        connector.updated_at = now

    @staticmethod
    def mark_tested(
        connector: Connector,
        *,
        ok: bool,
        status: str,
        message: str,
        checked_at: datetime.datetime,
    ) -> None:
        connector.last_test_ok = ok
        connector.last_test_status = status
        connector.last_test_message = message
        connector.last_test_at = checked_at
        connector.updated_at = checked_at
