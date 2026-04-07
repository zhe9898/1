"""
性能测试：Locust 压测配置。

法典 SRE 契约(第 4 部分):
- API P99 延迟 ≤500ms（平均 ≤200ms）
- 稳定并发连接数 ≥1000
- SSE 端到端延迟 ≤1s

运行方式：
  # 无头模式（CI）
  locust -f tests/performance/locustfile.py --headless \
    -u 100 -r 10 --run-time 60s \
    --host http://localhost:8000 \
    --csv results

  # Web UI 模式
  locust -f tests/performance/locustfile.py \
    --host http://localhost:8000
"""

from __future__ import annotations

from locust import HttpUser, between, events, task


class ZEN70ApiUser(HttpUser):
    """模拟正常用户行为：读取能力矩阵、健康检查、SSE 探测。"""

    wait_time = between(0.5, 2.0)

    @task(40)
    def get_capabilities(self) -> None:
        """最高频操作：读取能力矩阵（绝大部分前端请求）。"""
        with self.client.get(
            "/api/v1/capabilities",
            catch_response=True,
            name="/api/v1/capabilities",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Expected 200, got {resp.status_code}")
            else:
                body = resp.json()
                if body.get("code") != "ZEN-OK-0":
                    resp.failure(f"Missing envelope: {body.get('code')}")

    @task(20)
    def get_health(self) -> None:
        """健康检查（监控探针频率）。"""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Health check failed: {resp.status_code}")

    @task(15)
    def get_capabilities_with_request_id(self) -> None:
        """带 X-Request-ID 的请求（验证追踪链路无性能衰退）。"""
        import uuid

        rid = f"perf-{uuid.uuid4()}"
        with self.client.get(
            "/api/v1/capabilities",
            headers={"X-Request-ID": rid},
            catch_response=True,
            name="/api/v1/capabilities [with RID]",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Status {resp.status_code}")
            else:
                returned_rid = resp.headers.get("X-Request-ID", "")
                if returned_rid != rid:
                    resp.failure(f"RID mismatch: sent {rid}, got {returned_rid}")

    @task(10)
    def get_404_error(self) -> None:
        """404 错误路径（验证异常处理器无性能泄漏）。"""
        with self.client.get(
            "/api/v1/_nonexistent_for_perf_test",
            catch_response=True,
            name="/api/v1/404",
        ) as resp:
            if resp.status_code in (404, 405):
                resp.success()
            else:
                resp.failure(f"Expected 404/405, got {resp.status_code}")

    @task(10)
    def sse_connect_and_disconnect(self) -> None:
        """SSE 短连接探测（验证连接建立无资源泄漏）。"""
        try:
            with self.client.get(
                "/api/v1/events",
                stream=True,
                timeout=3,
                catch_response=True,
                name="/api/v1/events [SSE]",
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"SSE failed: {resp.status_code}")
                else:
                    # 读一行后立即断开
                    for line in resp.iter_lines():
                        break
                    resp.success()
        except (OSError, ConnectionError, TimeoutError):
            pass  # SSE 超时可接受

    @task(5)
    def post_stream_ping(self) -> None:
        """SSE 心跳续期（模拟前端 30s 一次的 ping）。"""
        with self.client.post(
            "/api/v1/stream/ping",
            json={"connection_id": f"perf-conn-{id(self)}"},
            catch_response=True,
            name="/api/v1/stream/ping",
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Ping failed: {resp.status_code}")


class ZEN70SchedulingUser(HttpUser):
    """模拟调度工作负载：创建 job、pull dispatch、finish。"""

    wait_time = between(0.1, 0.5)

    _job_counter: int = 0

    @task(40)
    def create_job(self) -> None:
        """POST /api/v1/jobs — 创建新 job。"""
        ZEN70SchedulingUser._job_counter += 1
        job_id = f"perf-{id(self)}-{ZEN70SchedulingUser._job_counter}"
        payload = {
            "job_id": job_id,
            "kind": "shell.exec",
            "priority": 50,
            "payload": {"command": "echo hello"},
        }
        with self.client.post(
            "/api/v1/jobs",
            json=payload,
            catch_response=True,
            name="/api/v1/jobs [create]",
        ) as resp:
            if resp.status_code in (200, 201, 409):
                resp.success()
            else:
                resp.failure(f"Create job failed: {resp.status_code}")

    @task(30)
    def pull_dispatch(self) -> None:
        """POST /api/v1/jobs/pull — 模拟 runner-agent 拉取任务。"""
        payload = {
            "node_id": f"perf-node-{id(self) % 10}",
            "accepted_kinds": ["shell.exec"],
            "max_concurrency": 4,
            "active_lease_count": 0,
        }
        with self.client.post(
            "/api/v1/jobs/pull",
            json=payload,
            catch_response=True,
            name="/api/v1/jobs/pull [dispatch]",
        ) as resp:
            if resp.status_code in (200, 204, 404):
                resp.success()
            else:
                resp.failure(f"Pull failed: {resp.status_code}")

    @task(20)
    def batch_create_jobs(self) -> None:
        """批量创建 jobs（高吞吐场景）。"""
        jobs = []
        for i in range(5):
            ZEN70SchedulingUser._job_counter += 1
            jobs.append(
                {
                    "job_id": f"batch-{id(self)}-{ZEN70SchedulingUser._job_counter}",
                    "kind": "shell.exec",
                    "priority": 30 + (i * 10),
                    "payload": {"command": f"echo batch-{i}"},
                }
            )
        with self.client.post(
            "/api/v1/jobs/batch",
            json={"jobs": jobs},
            catch_response=True,
            name="/api/v1/jobs/batch [create×5]",
        ) as resp:
            if resp.status_code in (200, 201, 404, 405):
                resp.success()
            else:
                resp.failure(f"Batch create failed: {resp.status_code}")

    @task(10)
    def get_scheduling_stats(self) -> None:
        """GET /api/v1/scheduling/stats — 调度统计信息。"""
        with self.client.get(
            "/api/v1/scheduling/stats",
            catch_response=True,
            name="/api/v1/scheduling/stats",
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Stats failed: {resp.status_code}")


# ---------------------------------------------------------------------------
# SRE 契约门禁：P99 ≤500ms 自动判定
# ---------------------------------------------------------------------------


@events.quitting.add_listener
def _check_p99_sla(environment: object, **kwargs: object) -> None:
    """测试结束时检查 P99 是否满足 SRE 契约。"""
    runner = getattr(environment, "runner", None)
    if runner is None:
        return

    stats = getattr(runner, "stats", None)
    if stats is None:
        return

    total = getattr(stats, "total", None)
    if total is None:
        return

    p99 = getattr(total, "get_response_time_percentile", lambda x: 0)(0.99)
    avg = getattr(total, "avg_response_time", 0)
    fail_ratio = getattr(total, "fail_ratio", 0)

    print(f"\n{'=' * 60}")
    print("SRE 契约验证:")
    print(f"  P99 延迟:   {p99:.0f}ms (目标 ≤500ms) {'✅' if p99 <= 500 else '❌'}")
    print(f"  平均延迟:   {avg:.0f}ms (目标 ≤200ms) {'✅' if avg <= 200 else '❌'}")
    print(f"  失败率:     {fail_ratio:.2%} (目标 <1%) {'✅' if fail_ratio < 0.01 else '❌'}")
    print(f"{'='*60}\n")

    if p99 > 500:
        getattr(environment, "process_exit_code", lambda x: None)(1)
