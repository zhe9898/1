"""
ZEN70 Alert Manager Worker - 告警分发（Bark / Server酱 / 数据库日志）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel

from backend.models.system import SystemLog

logger = logging.getLogger("zen70.alert_manager")


class AlertPayload(BaseModel):
    level: str = "info"
    title: str = ""
    message: str = ""
    source: str = "ZEN70_Sentinel"


async def push_to_bark(
    bark_url: str,
    title: str,
    message: str,
    level: str = "info",
    icon_url: str = "",
) -> None:
    params: dict[str, str] = {}
    if level == "critical":
        params["sound"] = "alarm"
        params["level"] = "timeSensitive"
    if icon_url:
        params["icon"] = icon_url

    url = f"{bark_url}/{title}/{message}"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.get(url, params=params)


async def push_to_serverchan(
    base_url: str,
    key: str,
    title: str,
    message: str,
) -> None:
    url = f"{base_url}/{key}.send"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, data={"title": title, "desp": message})


async def trigger_alert_endpoint(
    payload: AlertPayload,
    settings: Any,
    db: Any,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    log_entry = SystemLog()
    log_entry.action = f"ALERT_{payload.level.upper()}"
    log_entry.details = f"{payload.title}: {payload.message}"
    db.add(log_entry)
    await db.commit()

    if payload.level == "info":
        return {"status": "logged"}

    # Dispatch to external channels
    bark_url = os.getenv("BARK_URL", "")
    sc_key = os.getenv("SERVER_CHAN_KEY", "")
    icon_url = os.getenv("BARK_ICON_URL", "")
    channels = 0
    tasks: list[Any] = []

    if bark_url:
        tasks.append(push_to_bark(bark_url, payload.title, payload.message, payload.level, icon_url=icon_url))
        channels += 1
    if sc_key:
        tasks.append(push_to_serverchan("https://sctapi.ftqq.com", sc_key, payload.title, payload.message))
        channels += 1

    if tasks:
        asyncio.gather(*tasks)

    return {"status": "alert_dispatched", "channels": channels}
