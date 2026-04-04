"""
K3s 级动态路由控制器 (Routing Operator)

核心架构规范 (法典 Phase 7):
1. Status Watch (状态监听): 监听开关期望状态的变化。
2. Dynamic Compilation (动态编译): 当发生变化时，写入 routes.json 并调用 compiler.py 渲染全新 Caddyfile。
3. API Reload (热更新): 将新渲染的 Caddyfile 通过 HTTP 原生 API 推送给 Caddy 节点，实现绝对零停机 (Zero-Downtime)。
4. Meltdown Subscribe: 订阅 routing:meltdown 频道，立即响应熔断事件（法典 §3.1 第2步自动化）。
"""

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
import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ROUTING-OPERATOR] %(message)s",
)
logger = logging.getLogger(__name__)


# 配置硬编码，但在实际运行中会依赖系统级 yaml/env 的挂载
def _load_service_ports() -> dict[str, str]:
    """
    端口映射必须由 IaC 注入（system.yaml→compiler→.env），严禁代码硬编码。
    Env: SWITCH_SERVICE_PORTS='{"media":"8096","vision":"5000","llm":"11434"}'
    """
    raw = os.getenv("SWITCH_SERVICE_PORTS", "{}")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items()}
    except json.JSONDecodeError:
        return {}
    return {}


def _resolve_routes_state_file(project_root: Path) -> Path:
    raw = os.getenv("ROUTING_STATE_FILE", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else project_root / path
    return project_root / "runtime" / "control-plane" / "routes.json"


class RoutingOperator:
    def __init__(self) -> None:
        # P0 强引用绑定：防止 GC 静默回收长生命周期后台 Task
        self._tasks: set[asyncio.Task[None]] = set()
        self.redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = os.getenv("REDIS_PASSWORD", None)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.routes_state_file = _resolve_routes_state_file(self.project_root)
        # 严禁写死 Caddy Admin 地址：必须由 env/system.yaml→compiler→.env 注入
        self.caddy_api_url = os.getenv("CADDY_ADMIN_URL", "").strip()
        self.last_hash = ""
        self.service_ports = _load_service_ports()

        # 读取编译器传给 .env 的硬编码字典
        raw_map = os.getenv("SWITCH_CONTAINER_MAP", "{}")
        try:
            obj = json.loads(raw_map)
            self.switch_map: dict[str, str] = obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            logger.warning("SWITCH_CONTAINER_MAP JSON decode failed; routing disabled until fixed")
            self.switch_map = {}

    async def _get_redis(self) -> aioredis.Redis:
        return aioredis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            password=self.redis_password,
            decode_responses=True,
        )

    async def _compile_routes(self, routes: list[dict[str, str]]) -> None:
        """调用 IaC 编译器，生成最新的 Caddyfile"""
        routes_path = self.routes_state_file
        routes_path.parent.mkdir(parents=True, exist_ok=True)
        with routes_path.open("w", encoding="utf-8") as f:
            json.dump(routes, f, ensure_ascii=False, indent=2)

        compiler_script = self.project_root / "scripts" / "compiler.py"
        try:
            # 阻塞执行编译器，生成全新的 ./config/Caddyfile
            subprocess.run(
                [sys.executable, str(compiler_script), "system.yaml", "-o", ".", "--render-target", "caddy", "--dynamic-routes-file", str(routes_path)],
                cwd=str(self.project_root),
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.info("🟢 [Operator] The Compiler 成功渲染新拓扑至 Caddyfile")
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            logger.error("🔴 [Operator] The Compiler 执行失败: %s", err)
            raise

    async def _reload_caddy(self) -> None:
        """调用 Caddy 原生 Admin API 实现微秒级热拉起"""
        if not self.caddy_api_url:
            logger.warning("[Operator] CADDY_ADMIN_URL 未配置，跳过 Caddy 热更新")
            return
        caddyfile_path = self.project_root / "config" / "Caddyfile"
        if not caddyfile_path.exists():
            logger.warning("[Operator] 找不到生成的 Caddyfile，跳过 Reload")
            return

        with caddyfile_path.open("rb") as f:
            caddyfile_data = f.read()

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(
                    self.caddy_api_url,
                    headers={"Content-Type": "text/caddyfile"},
                    content=caddyfile_data,
                )
            if res.status_code == 200:
                logger.info("🟢 [Operator] Caddy 热更 API 调用成功 (Zero-Downtime Reload)!")
            else:
                logger.error(
                    "🔴 [Operator] Caddy Reload 失败: %s - %s",
                    res.status_code,
                    res.text,
                )
        except httpx.HTTPError as e:
            logger.warning("🟠 [Operator] Caddy API 通信异常: %s", e)

    async def _meltdown_listener(self) -> None:
        """法典 §3.1 第2步自动化：订阅 routing:meltdown 频道，立即触发路由重编译。崩溃自动重连。"""
        while True:
            r: aioredis.Redis | None = None
            try:
                r = await self._get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe("routing:meltdown")
                logger.info("[Operator] Meltdown subscriber ready on routing:meltdown")
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    logger.info("[Operator] Meltdown event received, forcing immediate reconcile")
                    self.last_hash = ""
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logger.warning("[Operator] Meltdown listener error: %s — retrying in 5s", exc)
            finally:
                if r is not None:
                    try:
                        await r.aclose()  # type: ignore[attr-defined]
                    except (OSError, ValueError, RuntimeError):
                        logger.debug("Redis close failed in meltdown listener cleanup")
            await asyncio.sleep(5)

    async def spin_loop(self) -> None:
        logger.info("Routing Operator K3s-Controller started")

        task = asyncio.create_task(self._meltdown_listener())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        while True:
            r: aioredis.Redis | None = None
            try:
                r = await self._get_redis()
                current_routes = []

                for switch_key, container_name in self.switch_map.items():
                    # Watch: 探究当前意图状态 (可能被 Taint 影响，或者被用户彻底关停)
                    state = await r.get(f"switch_expected:{switch_key}")
                    if state == "ON":
                        target_port = self.service_ports.get(switch_key, "80")
                        current_routes.append(
                            {
                                "path": f"/{switch_key}/*",
                                "target": f"{container_name}:{target_port}",
                            }
                        )

                # Reconcile: 对比状态指纹，若发生拓扑变更，执行 Dynamic Compilation + Reload
                routes_str = json.dumps(current_routes, sort_keys=True)
                routes_hash = hashlib.sha256(routes_str.encode()).hexdigest()

                if routes_hash != self.last_hash:
                    logger.info("🌀 [Operator] 侦测到节点拓扑变更，开始调谐 (Reconcile)...")
                    try:
                        await self._compile_routes(current_routes)
                        await self._reload_caddy()
                        self.last_hash = routes_hash
                    except (subprocess.CalledProcessError, httpx.HTTPError) as e:
                        logger.error("[Operator] 调谐期失败: %s", e)

            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                logger.debug("Operator loop issue: %s", e)
            finally:
                if r is not None:
                    try:
                        await r.aclose()  # type: ignore[attr-defined]
                    except (OSError, ValueError, RuntimeError):
                        logger.debug("Redis close failed in operator loop cleanup")

            await asyncio.sleep(5)  # 避坑：死循环非常吃 CPU，严格防卫 5 秒间隔


if __name__ == "__main__":
    op = RoutingOperator()
    asyncio.run(op.spin_loop())
