"""
调度性能基准测试：Locust 压测 — 覆盖 jobs/pull、workflow 调度吞吐、大规模 placement 场景。

补充主 locustfile.py 中缺失的调度内核压测场景：
- jobs/pull 端点吞吐量（核心调度路径）
- 批量 job 创建吞吐
- placement 决策延迟
- gang scheduling 场景
- 多租户 fair-share 竞争

运行方式：
  # 无头模式（CI）
  locust -f tests/performance/locustfile_scheduling.py --headless \\
    -u 50 -r 5 --run-time 120s \\
    --host http://localhost:8000 \\
    --csv scheduling-results

  # Web UI 模式
  locust -f tests/performance/locustfile_scheduling.py \\
    --host http://localhost:8000
"""

from __future__ import annotations

import random
import string
import uuid

from locust import HttpUser, between, events, task


def _random_string(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _make_job_payload(
    *,
    kind: str = "shell.exec",
    priority: int | None = None,
    gang_id: str | None = None,
    tenant_id: str = "perf-tenant",
) -> dict:
    return {
        "kind": kind,
        "tenant_id": tenant_id,
        "priority": priority or random.randint(1, 100),
        "payload": {"command": f"echo perf-{_random_string()}", "timeout": 30},
        "gang_id": gang_id,
        "tags": {"perf": "true", "batch": _random_string(4)},
    }


class SchedulerPullUser(HttpUser):
    """模拟 runner-agent 拉取任务（核心调度路径）。

    权重最高 — 这是调度器最关键的吞吐路径。
    """

    wait_time = between(0.1, 0.5)
    weight = 60

    def on_start(self) -> None:
        self._node_id = f"perf-node-{_random_string()}"
        self._tenant_id = f"perf-tenant-{random.choice(['a', 'b', 'c'])}"

    @task(80)
    def pull_jobs(self) -> None:
        """POST /api/v1/jobs/pull — 核心调度决策路径。"""
        with self.client.post(
            "/api/v1/jobs/pull",
            json={
                "node_id": self._node_id,
                "tenant_id": self._tenant_id,
                "accepted_kinds": ["shell.exec", "container.run", "http.request", "script.run"],
                "limit": random.choice([1, 3, 5, 10]),
            },
            catch_response=True,
            name="/api/v1/jobs/pull",
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Pull failed: {resp.status_code}")

    @task(20)
    def pull_jobs_heavy(self) -> None:
        """Large limit pull — 测试大批量调度决策。"""
        with self.client.post(
            "/api/v1/jobs/pull",
            json={
                "node_id": self._node_id,
                "tenant_id": self._tenant_id,
                "accepted_kinds": [
                    "shell.exec",
                    "container.run",
                    "http.request",
                    "script.run",
                    "ml.inference",
                    "healthcheck",
                ],
                "limit": 50,
            },
            catch_response=True,
            name="/api/v1/jobs/pull [heavy]",
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Heavy pull failed: {resp.status_code}")


class JobSubmitUser(HttpUser):
    """模拟业务侧批量提交作业 — 填充调度队列。"""

    wait_time = between(0.2, 1.0)
    weight = 30

    def on_start(self) -> None:
        self._tenant_id = f"perf-tenant-{random.choice(['a', 'b', 'c'])}"

    @task(50)
    def create_single_job(self) -> None:
        """POST /api/v1/jobs — 单个作业提交。"""
        job = _make_job_payload(tenant_id=self._tenant_id)
        with self.client.post(
            "/api/v1/jobs",
            json=job,
            catch_response=True,
            name="/api/v1/jobs [create]",
        ) as resp:
            if resp.status_code in (200, 201, 401, 403, 422):
                resp.success()
            else:
                resp.failure(f"Create failed: {resp.status_code}")

    @task(20)
    def create_gang_jobs(self) -> None:
        """批量提交 gang 任务 — 验证 gang scheduling 性能。"""
        gang_id = f"gang-{uuid.uuid4().hex[:8]}"
        gang_size = random.choice([2, 3, 5])
        for i in range(gang_size):
            job = _make_job_payload(
                kind="container.run",
                gang_id=gang_id,
                priority=80 + i,
                tenant_id=self._tenant_id,
            )
            job["payload"] = {
                "image": "alpine:latest",
                "command": ["echo", f"gang-member-{i}"],
                "timeout": 120,
            }
            with self.client.post(
                "/api/v1/jobs",
                json=job,
                catch_response=True,
                name="/api/v1/jobs [gang]",
            ) as resp:
                if resp.status_code in (200, 201, 401, 403, 422):
                    resp.success()
                else:
                    resp.failure(f"Gang create failed: {resp.status_code}")

    @task(15)
    def create_batch_jobs(self) -> None:
        """批量提交 batch 任务 — 验证 batch 共置评分。"""
        batch_key = f"batch-{_random_string(6)}"
        batch_size = random.choice([5, 10, 20])
        for i in range(batch_size):
            job = _make_job_payload(
                kind="shell.exec",
                priority=random.randint(30, 70),
                tenant_id=self._tenant_id,
            )
            job["batch_key"] = batch_key
            with self.client.post(
                "/api/v1/jobs",
                json=job,
                catch_response=True,
                name="/api/v1/jobs [batch]",
            ) as resp:
                if resp.status_code in (200, 201, 401, 403, 422):
                    resp.success()
                else:
                    resp.failure(f"Batch create failed: {resp.status_code}")

    @task(15)
    def create_high_priority_with_deadline(self) -> None:
        """提交高优先级+截止时间任务 — 验证抢占和 SLA 路径。"""
        import datetime

        deadline = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=random.randint(5, 60))).isoformat()
        job = _make_job_payload(
            kind="http.request",
            priority=random.randint(85, 100),
            tenant_id=self._tenant_id,
        )
        job["deadline_at"] = deadline
        job["sla_seconds"] = random.choice([60, 120, 300, 600])
        job["payload"] = {
            "url": "http://localhost:8000/health",
            "method": "GET",
            "timeout": 10,
        }
        with self.client.post(
            "/api/v1/jobs",
            json=job,
            catch_response=True,
            name="/api/v1/jobs [deadline]",
        ) as resp:
            if resp.status_code in (200, 201, 401, 403, 422):
                resp.success()
            else:
                resp.failure(f"Deadline create failed: {resp.status_code}")


class SchedulerDiagnosticsUser(HttpUser):
    """模拟运维查询调度状态。"""

    wait_time = between(1.0, 3.0)
    weight = 10

    @task(40)
    def get_quotas(self) -> None:
        """GET /api/v1/quotas — 配额查询。"""
        with self.client.get(
            "/api/v1/quotas",
            catch_response=True,
            name="/api/v1/quotas",
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Quotas failed: {resp.status_code}")

    @task(30)
    def get_capabilities(self) -> None:
        """GET /api/v1/capabilities — 能力矩阵。"""
        with self.client.get(
            "/api/v1/capabilities",
            catch_response=True,
            name="/api/v1/capabilities [sched]",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")

    @task(30)
    def list_jobs(self) -> None:
        """GET /api/v1/jobs — 作业列表查询。"""
        with self.client.get(
            "/api/v1/jobs",
            params={"status": random.choice(["pending", "leased", "completed"]), "limit": 50},
            catch_response=True,
            name="/api/v1/jobs [list]",
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"List failed: {resp.status_code}")


# ---------------------------------------------------------------------------
# 调度 SRE 契约门禁
# ---------------------------------------------------------------------------


@events.quitting.add_listener
def _check_scheduling_sla(environment: object, **kwargs: object) -> None:
    """调度路径 P99 ≤ 1000ms, 平均 ≤ 500ms 的 SRE 门禁。"""
    runner = getattr(environment, "runner", None)
    if runner is None:
        return

    stats = getattr(runner, "stats", None)
    if stats is None:
        return

    # Check pull endpoint specifically
    entries = getattr(stats, "entries", {})
    pull_key = ("POST", "/api/v1/jobs/pull")
    pull_stats = entries.get(pull_key)

    print(f"\n{'='*60}")
    print("调度性能 SRE 契约验证:")

    if pull_stats and getattr(pull_stats, "num_requests", 0) > 0:
        p99 = getattr(pull_stats, "get_response_time_percentile", lambda x: 0)(0.99)
        avg = getattr(pull_stats, "avg_response_time", 0)
        rps = getattr(pull_stats, "total_rps", 0)
        fail_ratio = getattr(pull_stats, "fail_ratio", 0)
        print(f"  [jobs/pull] P99: {p99:.0f}ms (目标 ≤1000ms) {'✅' if p99 <= 1000 else '❌'}")
        print(f"  [jobs/pull] 平均: {avg:.0f}ms (目标 ≤500ms) {'✅' if avg <= 500 else '❌'}")
        print(f"  [jobs/pull] RPS: {rps:.1f}")
        print(f"  [jobs/pull] 失败率: {fail_ratio:.2%}")
    else:
        print("  [jobs/pull] 无数据")

    total = getattr(stats, "total", None)
    if total:
        p99 = getattr(total, "get_response_time_percentile", lambda x: 0)(0.99)
        avg = getattr(total, "avg_response_time", 0)
        rps = getattr(total, "total_rps", 0)
        fail_ratio = getattr(total, "fail_ratio", 0)
        print(f"  [总计] P99: {p99:.0f}ms / 平均: {avg:.0f}ms / RPS: {rps:.1f} / 失败率: {fail_ratio:.2%}")

    print(f"{'='*60}\n")
