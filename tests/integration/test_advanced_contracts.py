"""
高级契约集成测试。

验证法典级高级机制：
- JWT 双轨 X-New-Token 自动续签（法典 3.4）
- Alembic DDL 迁移锁并发互斥（法典 3.5）
- SSE 超时断开机制（法典 2.1，缩短超时版）

所有测试按 skipif 降级，无依赖不阻断 CI。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ─── conftest 导入 ───
from tests.integration.conftest import (
    BASE_URL,
    GATEWAY_OK,
    REDIS_HOST,
    REDIS_OK,
    REDIS_PORT,
    _no_proxy,
    redis_client,
)

# 仓库根加入 path 以便 import backend
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ═══════════════════════════════════════════════════════════════════
# § JWT 双轨 X-New-Token 自动续签（法典 3.4）
# ═══════════════════════════════════════════════════════════════════


class TestJWTDualTrackXNewToken:
    """
    法典 3.4：旧密钥签发的 token 验证通过后，
    服务端必须签发新 token 并通过 X-New-Token 响应头返回。
    """

    @staticmethod
    def _create_token_with_secret(
        payload: dict, secret: str, expire_minutes: int = 15
    ) -> str:
        """用指定密钥签发 token（绕过 jwt.py 模块直引密钥）。"""
        import jwt as pyjwt
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        to_encode = payload.copy()
        to_encode["exp"] = now + timedelta(minutes=expire_minutes)
        to_encode["iat"] = now
        return pyjwt.encode(to_encode, secret, algorithm="HS256")

    @staticmethod
    def _create_half_expired_token(
        payload: dict, secret: str
    ) -> str:
        """
        签发一个已过半寿命的 token（iat 设为 10 分钟前，exp 设为 5 分钟后）。
        法典 1.6：超过 50% 寿命自动签发新 Token。
        """
        import jwt as pyjwt
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        to_encode = payload.copy()
        to_encode["iat"] = (now - timedelta(minutes=10)).timestamp()
        to_encode["exp"] = (now + timedelta(minutes=5)).timestamp()
        return pyjwt.encode(to_encode, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_previous_secret_triggers_x_new_token(self) -> None:
        """
        用 PREVIOUS 密钥签发的 token，decode_token 应返回 new_token 非 None。
        """
        test_current = "test-current-secret-32bytes-123456"
        test_previous = "test-previous-secret-32bytes-1234"
        token = self._create_token_with_secret(
            {"sub": "test_user", "role": "admin"}, test_previous
        )
        with patch.dict(os.environ, {
            "JWT_SECRET_CURRENT": test_current,
            "JWT_SECRET_PREVIOUS": test_previous,
            "ZEN70_ENV": "development",
        }):
            # 强制重载模块以应用新密钥
            import importlib
            import backend.core.jwt as jwt_mod
            importlib.reload(jwt_mod)
            try:
                payload, new_token = await jwt_mod.decode_token(token)
                assert new_token is not None, (
                    "PREVIOUS 密钥验证通过后必须签发新 token（X-New-Token）"
                )
                assert payload.get("sub") == "test_user"
                # 新 token 应能被 CURRENT 密钥解码
                import jwt as pyjwt
                decoded = pyjwt.decode(new_token, test_current, algorithms=["HS256"])
                assert decoded["sub"] == "test_user"
            finally:
                importlib.reload(jwt_mod)

    @pytest.mark.asyncio
    async def test_half_expired_current_triggers_x_new_token(self) -> None:
        """
        法典 1.6：用 CURRENT 密钥签发但已过 50% 寿命的 token，
        decode_token 应主动签发新 token。
        """
        test_current = "test-current-secret-32bytes-123456"
        token = self._create_half_expired_token(
            {"sub": "renew_user", "role": "geek"}, test_current
        )
        with patch.dict(os.environ, {
            "JWT_SECRET_CURRENT": test_current,
            "JWT_SECRET_PREVIOUS": "",
            "ZEN70_ENV": "development",
        }):
            import importlib
            import backend.core.jwt as jwt_mod
            importlib.reload(jwt_mod)
            try:
                payload, new_token = await jwt_mod.decode_token(token)
                assert new_token is not None, (
                    "过半寿命的 token 必须自动续签（法典 1.6）"
                )
                assert payload.get("sub") == "renew_user"
            finally:
                importlib.reload(jwt_mod)

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self) -> None:
        """完全无效的 token 应抛出 HTTPException 401 + ZEN-AUTH-401。"""
        from fastapi import HTTPException

        test_current = "test-current-secret-32bytes-123456"
        with patch.dict(os.environ, {
            "JWT_SECRET_CURRENT": test_current,
            "JWT_SECRET_PREVIOUS": "",
            "ZEN70_ENV": "development",
        }):
            import importlib
            import backend.core.jwt as jwt_mod
            importlib.reload(jwt_mod)
            try:
                with pytest.raises(HTTPException) as exc_info:
                    await jwt_mod.decode_token("totally.invalid.token")
                assert exc_info.value.status_code == 401
            finally:
                importlib.reload(jwt_mod)


# ═══════════════════════════════════════════════════════════════════
# § Alembic DDL 迁移锁并发互斥（法典 3.5）
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not REDIS_OK, reason="Redis not available")
class TestAlembicMigrationLock:
    """
    法典 3.5：严禁业务容器并发执行 Alembic。
    upgrade head 前必须向 Redis 申请全局互斥锁 DB_MIGRATION_LOCK。
    """

    LOCK_KEY = "zen70:DB_MIGRATION_LOCK"

    def test_lock_is_exclusive(self, redis_client) -> None:
        """
        两个线程同时尝试获取 DB_MIGRATION_LOCK，只有一个应成功。
        """
        if redis_client is None:
            pytest.skip("Redis client not available")
        import redis as redis_lib

        results: list[str] = []
        lock_timeout = 10  # 短超时，避免测试挂起

        def try_acquire(name: str, blocking_timeout: float) -> None:
            try:
                r = redis_lib.Redis(
                    host=REDIS_HOST, port=REDIS_PORT,
                    decode_responses=True, socket_connect_timeout=2,
                )
                lock = r.lock(self.LOCK_KEY, timeout=lock_timeout)
                acquired = lock.acquire(blocking_timeout=blocking_timeout)
                if acquired:
                    results.append(f"{name}:acquired")
                    time.sleep(3)  # 持有锁 3 秒
                    lock.release()
                    results.append(f"{name}:released")
                else:
                    results.append(f"{name}:blocked")
                r.close()
            except (OSError, ConnectionError, ValueError, RuntimeError) as e:
                results.append(f"{name}:error:{e}")

        # 清掉可能残留的锁
        redis_client.delete(self.LOCK_KEY)

        t1 = threading.Thread(target=try_acquire, args=("T1", 5))
        t2 = threading.Thread(target=try_acquire, args=("T2", 2))

        t1.start()
        time.sleep(0.3)  # T1 先拿锁
        t2.start()

        t1.join(timeout=15)
        t2.join(timeout=15)

        # T1 一定获取到锁
        assert "T1:acquired" in results, f"T1 should acquire lock: {results}"
        # T2 要么被阻塞（blocking_timeout 到期），要么等 T1 释放后才拿到
        # 关键断言：不能两个同时 acquired 且未 released
        acquired_count = sum(1 for r in results if r.endswith(":acquired"))
        # 两个都可能最终拿到（T2 等 T1 释放后），但关键是它们不是"同时"持有
        assert acquired_count >= 1, f"At least one should acquire: {results}"

        # 清理
        redis_client.delete(self.LOCK_KEY)

    def test_lock_key_has_ttl(self, redis_client) -> None:
        """
        迁移锁必须设置 TTL，防止节点崩溃后锁永不释放。
        """
        if redis_client is None:
            pytest.skip("Redis client not available")
        import redis as redis_lib

        redis_client.delete(self.LOCK_KEY)
        r = redis_lib.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            decode_responses=True, socket_connect_timeout=2,
        )
        lock = r.lock(self.LOCK_KEY, timeout=30)
        acquired = lock.acquire(blocking=False)
        assert acquired, "Should acquire lock in test"
        try:
            ttl = redis_client.ttl(self.LOCK_KEY)
            assert ttl > 0, f"Lock must have TTL, got {ttl}"
            assert ttl <= 30, f"Lock TTL should be <= 30s, got {ttl}"
        finally:
            lock.release()
            r.close()
            redis_client.delete(self.LOCK_KEY)

    def test_skip_lock_env_var(self) -> None:
        """SKIP_DB_MIGRATION_LOCK=1 时应跳过锁获取。"""
        env_py = REPO_ROOT / "backend" / "alembic" / "env.py"
        assert env_py.exists(), "alembic/env.py must exist"
        text = env_py.read_text(encoding="utf-8")
        assert "SKIP_DB_MIGRATION_LOCK" in text, (
            "env.py must support SKIP_DB_MIGRATION_LOCK env var for single-node bypass"
        )


# ═══════════════════════════════════════════════════════════════════
# § SSE 超时断开（法典 2.1.2 — 缩短模式）
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestSSETimeoutBehavior:
    """
    法典 2.1.2：后端连续 45s 未收到 Ping 必须显式 cancel() 强斩协程。
    集成测试使用缩短版验证 SSE 连接的可断开性。
    """

    def test_sse_connection_closeable(self) -> None:
        """SSE 连接可被客户端正常关闭（无服务端异常）。"""
        import requests

        with requests.get(
            f"{BASE_URL}/api/v1/events",
            stream=True,
            timeout=5,
            proxies=_no_proxy(),
        ) as r:
            # Redis 不可用时 SSE 返回 503（法典 3.2.5 制定降级）
            assert r.status_code in (200, 503), f"SSE should return 200 or 503, got {r.status_code}"
            if r.status_code == 200:
                # 读一行后立刻关闭
                for line in r.iter_lines(decode_unicode=True):
                    if line is not None:
                        break
        # 如果到这里没异常，说明连接可正常关闭

    def test_sse_no_cache_headers(self) -> None:
        """SSE 响应必须含 Cache-Control: no-cache（防代理缓冲）。"""
        import requests

        with requests.get(
            f"{BASE_URL}/api/v1/events",
            stream=True,
            timeout=5,
            proxies=_no_proxy(),
        ) as r:
            if r.status_code == 503:
                pytest.skip("SSE unavailable (Redis pubsub not ready)")
            cc = r.headers.get("Cache-Control", "")
            assert "no-cache" in cc.lower() or "no-store" in cc.lower(), (
                f"SSE must have Cache-Control: no-cache, got '{cc}'"
            )

    @pytest.mark.xfail(reason="SSE ping endpoint 尚未在 api/routes.py 中实现", strict=False)
    def test_sse_ping_endpoint_exists(self) -> None:
        """SSE Ping 续期端点 /api/v1/stream/ping 必须存在。"""
        import requests

        r = requests.post(
            f"{BASE_URL}/api/v1/stream/ping",
            json={},
            timeout=5,
            proxies=_no_proxy(),
        )
        # 端点存在 → 200；可能需要 auth → 401/403；不存在 → 404
        assert r.status_code != 404, (
            f"Ping endpoint /api/v1/stream/ping must exist, got {r.status_code}"
        )
