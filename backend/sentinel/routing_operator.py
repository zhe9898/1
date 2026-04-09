"""Dynamic routing operator for Caddy route compilation and reloads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx

from backend.platform.events.channels import CHANNEL_ROUTING_MELTDOWN
from backend.platform.events.subscriber import AsyncInternalSignalSubscriber
from backend.platform.redis.client import RedisClient
from backend.platform.redis.runtime_state import sentinel_override_key
from backend.platform.security.normalization import normalize_loopback_control_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ROUTING-OPERATOR] %(message)s",
)
logger = logging.getLogger(__name__)


def _load_service_ports() -> dict[str, str]:
    raw = os.getenv("SWITCH_SERVICE_PORTS", "{}")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in obj.items()} if isinstance(obj, dict) else {}


def _resolve_routes_state_file(project_root: Path) -> Path:
    raw = os.getenv("ROUTING_STATE_FILE", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else project_root / path
    return project_root / "runtime" / "control-plane" / "routes.json"


class RoutingOperator:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.routes_state_file = _resolve_routes_state_file(self.project_root)
        self.last_hash = ""
        self.service_ports = _load_service_ports()
        self._redis_client: RedisClient | None = None

        raw_caddy_api_url = os.getenv("CADDY_ADMIN_URL", "").strip()
        try:
            self.caddy_api_url = (
                normalize_loopback_control_url(
                    raw_caddy_api_url,
                    field_name="CADDY_ADMIN_URL",
                    required_path="/load",
                )
                if raw_caddy_api_url
                else ""
            )
        except ValueError as exc:
            logger.error("[Operator] Invalid CADDY_ADMIN_URL: %s", exc)
            self.caddy_api_url = ""

        raw_map = os.getenv("SWITCH_CONTAINER_MAP", "{}")
        try:
            obj = json.loads(raw_map)
            self.switch_map = obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            logger.warning("SWITCH_CONTAINER_MAP JSON decode failed; routing disabled until fixed")
            self.switch_map = {}

    async def _get_redis(self) -> RedisClient:
        if self._redis_client is not None:
            return self._redis_client
        client = RedisClient(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
        )
        await client.connect()
        self._redis_client = client
        return client

    async def _compile_routes(self, routes: list[dict[str, str]]) -> None:
        routes_path = self.routes_state_file
        routes_path.parent.mkdir(parents=True, exist_ok=True)
        routes_path.write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")

        compiler_script = self.project_root / "scripts" / "compiler.py"
        subprocess.run(
            [
                sys.executable,
                str(compiler_script),
                "system.yaml",
                "-o",
                ".",
                "--render-target",
                "caddy",
                "--dynamic-routes-file",
                str(routes_path),
            ],
            cwd=str(self.project_root),
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        logger.info("[Operator] Compiler rendered updated Caddyfile")

    async def _reload_caddy(self) -> None:
        if not self.caddy_api_url:
            logger.warning("[Operator] CADDY_ADMIN_URL not configured; skipping reload")
            return
        caddyfile_path = self.project_root / "config" / "Caddyfile"
        if not caddyfile_path.exists():
            logger.warning("[Operator] Generated Caddyfile not found; skipping reload")
            return

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                self.caddy_api_url,
                headers={"Content-Type": "text/caddyfile"},
                content=caddyfile_path.read_bytes(),
            )
        if response.status_code == 200:
            logger.info("[Operator] Caddy hot reload succeeded")
            return
        logger.error("[Operator] Caddy reload failed: %s - %s", response.status_code, response.text)

    async def _switch_routes_enabled(self, redis_client: RedisClient, switch_name: str) -> bool:
        switch_state = await redis_client.switches.get(switch_name)
        if switch_state is None or str(switch_state.get("state") or "").upper() != "ON":
            return False
        override = await redis_client.kv.get(sentinel_override_key(switch_name))
        return not override or str(override).strip().upper() == "ON"

    async def _meltdown_listener(self) -> None:
        while True:
            subscription = None
            try:
                redis_client = await self._get_redis()
                subscription = await AsyncInternalSignalSubscriber(redis_client).subscribe((CHANNEL_ROUTING_MELTDOWN,))
                logger.info("[Operator] Meltdown subscriber ready on %s", CHANNEL_ROUTING_MELTDOWN)
                while True:
                    message = await subscription.get_message(timeout=1.0)
                    if message is None:
                        await asyncio.sleep(0.1)
                        continue
                    logger.info("[Operator] Meltdown event received; forcing immediate reconcile")
                    self.last_hash = ""
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logger.warning("[Operator] Meltdown listener error: %s; retrying in 5s", exc)
                if self._redis_client is not None:
                    await self._redis_client.close()
                    self._redis_client = None
            finally:
                if subscription is not None:
                    await subscription.close()
            await asyncio.sleep(5)

    async def spin_loop(self) -> None:
        logger.info("Routing Operator started")

        task = asyncio.create_task(self._meltdown_listener())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        while True:
            try:
                redis_client = await self._get_redis()
                current_routes: list[dict[str, str]] = []
                for switch_key, container_name in self.switch_map.items():
                    if await self._switch_routes_enabled(redis_client, switch_key):
                        target_port = self.service_ports.get(switch_key, "80")
                        current_routes.append({"path": f"/{switch_key}/*", "target": f"{container_name}:{target_port}"})

                routes_hash = hashlib.sha256(json.dumps(current_routes, sort_keys=True).encode("utf-8")).hexdigest()
                if routes_hash != self.last_hash:
                    logger.info("[Operator] Route topology changed; reconciling")
                    await self._compile_routes(current_routes)
                    await self._reload_caddy()
                    self.last_hash = routes_hash
            except (OSError, ValueError, KeyError, RuntimeError, TypeError, subprocess.CalledProcessError, httpx.HTTPError) as exc:
                logger.debug("Operator loop issue: %s", exc)
                if self._redis_client is not None:
                    await self._redis_client.close()
                    self._redis_client = None
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(RoutingOperator().spin_loop())
