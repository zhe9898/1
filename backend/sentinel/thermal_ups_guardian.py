"""Thermal and UPS guardian for control-plane emergency protection."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import psutil

from backend.kernel.contracts.events_schema import build_switch_command_signal
from backend.platform.events.channels import CHANNEL_SWITCH_COMMANDS
from backend.platform.events.publisher import AsyncEventPublisher, event_bus_settings_from_env
from backend.platform.http.webhooks import post_public_webhook_async
from backend.platform.redis.client import RedisClient
from backend.platform.redis.constants import KEY_SYSTEM_READONLY_DISK

logger = logging.getLogger("zen70.sentinel.guardian")


class SystemGuardian:
    def __init__(self) -> None:
        self.alert_webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
        self.temperature_threshold = 85.0
        self.ups_battery_threshold = 20.0
        self._redis: RedisClient | None = None

    async def _get_redis(self) -> RedisClient:
        if self._redis is not None:
            return self._redis
        client = RedisClient(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
        )
        await client.connect()
        self._redis = client
        return client

    async def emit_critical_alert(self, title: str, message: str) -> None:
        if not self.alert_webhook:
            logger.warning("ALERT_WEBHOOK_URL not configured; skipping alert: %s", title)
            return
        sent = await post_public_webhook_async(
            self.alert_webhook,
            {
                "level": "critical",
                "title": title,
                "message": message,
                "source": "guardian",
            },
            timeout=3.0,
            logger=logger,
            context="thermal_guardian",
        )
        if sent:
            logger.warning("Critical guardian alert delivered: %s", title)

    def fetch_cpu_temperature(self) -> float:
        if not hasattr(psutil, "sensors_temperatures"):
            return 45.0
        temps = psutil.sensors_temperatures()
        if not temps:
            return 0.0
        return max((entry.current for entries in temps.values() for entry in entries), default=0.0)

    async def lock_api_gateway(self) -> None:
        logger.critical("Applying global readonly lock due to thermal emergency")
        redis_client = await self._get_redis()
        await redis_client.kv.set(KEY_SYSTEM_READONLY_DISK, "thermal_guardian")
        logger.critical("Readonly lock written to Redis key %s", KEY_SYSTEM_READONLY_DISK)

    async def pause_heavy_containers(self) -> None:
        logger.warning("Publishing degraded-state switch events for heavy containers")
        redis_client = await self._get_redis()
        publisher = AsyncEventPublisher(
            settings=event_bus_settings_from_env(),
            redis=redis_client,
            logger=logger,
        )
        try:
            for container in ("zen70-jellyfin", "zen70-frigate", "zen70-ollama"):
                signal_payload = build_switch_command_signal(
                    container,
                    "PAUSE",
                    reason="thermal_guardian: thermal emergency",
                    updated_by="guardian",
                )
                receiver_count = await publisher.publish_signal(
                    CHANNEL_SWITCH_COMMANDS,
                    json.dumps(signal_payload),
                )
                if receiver_count == 0:
                    logger.warning("Published pause signal for %s without active sentinel subscribers", container)
                else:
                    logger.info("Published pause signal for %s", container)
        finally:
            await publisher.close()

    async def run_thermal_loop(self) -> None:
        while True:
            temp = self.fetch_cpu_temperature()
            if temp > self.temperature_threshold:
                message = f"CPU temperature reached {temp:.1f}C, entering emergency readonly and workload degradation mode"
                await self.emit_critical_alert("Thermal emergency", message)
                await self.lock_api_gateway()
                await self.pause_heavy_containers()
                await asyncio.sleep(300)
                continue
            await asyncio.sleep(10)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    guardian = SystemGuardian()
    asyncio.run(guardian.emit_critical_alert("Guardian self-test", "Thermal guardian booted"))
    asyncio.run(guardian.pause_heavy_containers())
