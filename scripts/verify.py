#!/usr/bin/env python3
"""
ZEN70 自动化基座验真脚本 (遵循 T-02 规范)
支持直接运行或作为模块导入调用。
智能轮询健康状态、日志扫描、Docker 重试。
"""

from __future__ import annotations

import logging
import subprocess
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import docker
except ImportError:
    docker = None

PROJECT_LABEL = "com.docker.compose.project=zen70"
MAX_WAIT = 30
CHECK_INTERVAL = 3
FATAL_PATTERN = re.compile(
    r"(FATAL|Permission denied|panic|address already in use)",
    re.IGNORECASE,
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_core_containers() -> set[str]:
    """从 render-manifest.json 读取 services_rendered，映射为容器名。

    IaC 唯一事实来源：禁止硬编码容器列表（法典 §1.2）。
    兜底：manifest 缺失时使用 system.yaml 中 enabled=true 的服务。
    """
    manifest_path = PROJECT_ROOT / "render-manifest.json"
    try:
        import json as _json
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        services = manifest.get("services_rendered", [])
        if services:
            return {f"zen70-{svc}" for svc in services}
    except (OSError, ValueError, KeyError):
        pass
    # 二级兜底：从 system.yaml 读取 enabled 服务
    try:
        import yaml as _yaml
        sys_cfg = _yaml.safe_load((PROJECT_ROOT / "system.yaml").read_text(encoding="utf-8"))
        return {
            svc.get("container_name", f"zen70-{name}")
            for name, svc in (sys_cfg.get("services") or {}).items()
            if isinstance(svc, dict) and svc.get("enabled") is not False
        }
    except (OSError, ValueError):
        pass
    # 三级兜底（不可达状态下的安全降级）
    return {"zen70-gateway", "zen70-redis", "zen70-postgres"}


DEFAULT_CORE_CONTAINERS = _load_core_containers()


def _run_ci_step(command: list[str], step_name: str) -> bool:
    """执行单个 CI 步骤并输出标准日志。"""
    logger.info("CI 步骤开始: %s", step_name)
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=False,
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("CI 步骤异常 (%s): %s", step_name, exc)
        return False

    stdout_text = (result.stdout or b"").decode("utf-8", errors="ignore").strip()
    stderr_text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
    if stdout_text:
        logger.info("[%s stdout]\n%s", step_name, stdout_text)
    if stderr_text:
        logger.warning("[%s stderr]\n%s", step_name, stderr_text)
    if result.returncode != 0:
        logger.error("CI 步骤失败: %s (exit=%s)", step_name, result.returncode)
        return False
    logger.info("CI 步骤通过: %s", step_name)
    return True


def run_ci_pipeline(exit_on_fail: bool = True, use_container_e2e: bool = False) -> bool:
    """执行数据完整性 CI 门禁链路。"""
    if use_container_e2e:
        e2e_command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "sentinel",
            "python",
            "/app/backend/scripts/verify_data_integrity_e2e.py",
        ]
        e2e_step_name = "e2e:data_integrity:container"
    else:
        e2e_command = [sys.executable, "scripts/verify_data_integrity_e2e.py"]
        e2e_step_name = "e2e:data_integrity"

    steps = [
        (
            [
                sys.executable,
                "-m",
                "flake8",
                "--max-complexity=15",
                "--max-line-length=160",
                "backend/sentinel/data_integrity.py",
                "backend/tests/unit/test_data_integrity.py",
            ],
            "flake8:data_integrity",
        ),
        ([sys.executable, "-m", "pytest", "backend/tests/unit/test_data_integrity.py", "-q"], "pytest:data_integrity"),
        (e2e_command, e2e_step_name),
    ]
    all_passed = True
    for command, step_name in steps:
        if not _run_ci_step(command, step_name):
            all_passed = False
            if exit_on_fail:
                sys.exit(1)
            break
    return all_passed


def get_docker_client(retries: int = 3, delay: int = 2):  # type: ignore
    """创建 Docker 客户端，带重试机制。"""
    if docker is None:
        logger.error("未安装 docker 包，请 pip install docker")
        return None
    docker_error_types = (OSError, ValueError, KeyError, RuntimeError, TypeError)
    if hasattr(docker, "errors") and hasattr(docker.errors, "DockerException"):
        docker_error_types = docker_error_types + (docker.errors.DockerException,)
    for attempt in range(retries):
        try:
            return docker.from_env()
        except docker_error_types as e:
            logger.warning("连接 Docker 失败 (尝试 %s/%s): %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def wait_for_containers_ready(
    client,
    project_label: str,
    timeout: int = MAX_WAIT,
    allow_degraded: set[str] | None = None,
    required_healthy: set[str] | None = None,
) -> bool:
    """
    等待项目内所有容器进入健康/运行状态。
    返回 True 表示所有容器就绪，False 表示超时或失败。
    """
    allowed = allow_degraded or set()
    must_be_healthy = required_healthy or set()
    start_time = time.time()
    while time.time() - start_time < timeout:
        containers = client.containers.list(all=True, filters={"label": project_label})
        if not containers:
            logger.error("未找到任何项目容器")
            return False

        all_ready = True
        for c in containers:
            c.reload()
            status = c.status
            health = c.attrs.get("State", {}).get("Health", {}).get("Status")
            if health:
                ready = health == "healthy"
                state_desc = f"health={health}"
            else:
                ready = status == "running"
                state_desc = f"status={status}"

            if not ready:
                if c.name in allowed and c.name not in must_be_healthy:
                    logger.warning("容器 %s 未就绪但已降级放行 (%s)", c.name, state_desc)
                else:
                    all_ready = False
                    logger.info("容器 %s 未就绪 (%s)", c.name, state_desc)

        for critical_name in must_be_healthy:
            critical_container = next((x for x in containers if x.name == critical_name), None)
            if critical_container is None:
                logger.error("关键容器缺失: %s", critical_name)
                return False

        if all_ready:
            return True
        time.sleep(CHECK_INTERVAL)

    logger.error("等待容器就绪超时 (%s秒)", timeout)
    return False


def scan_container_logs(container) -> list[str]:
    """扫描容器最近 50 行日志中的致命错误。"""
    try:
        logs = container.logs(tail=50).decode("utf-8", errors="ignore")
        return [line.strip() for line in logs.split("\n") if FATAL_PATTERN.search(line)]
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.warning("读取容器 %s 日志失败: %s", container.name, e)
        return []


def verify_infrastructure(
    exit_on_fail: bool = True,
    allow_degraded: set[str] | None = None,
    required_healthy: set[str] | None = None,
) -> bool:
    """
    执行基础设施验真。
    若 exit_on_fail=True，失败时直接退出进程；否则返回布尔值。
    """
    logger.info("开始执行自动化基座验真...")

    client = get_docker_client()
    if not client:
        logger.error("无法连接到 Docker Daemon")
        if exit_on_fail:
            sys.exit(1)
        return False

    if not wait_for_containers_ready(
        client,
        PROJECT_LABEL,
        allow_degraded=allow_degraded,
        required_healthy=required_healthy,
    ):
        if exit_on_fail:
            sys.exit(1)
        return False

    containers = client.containers.list(all=True, filters={"label": PROJECT_LABEL})
    all_passed = True

    for container in containers:
        name = container.name
        status = container.status
        health = container.attrs.get("State", {}).get("Health", {}).get("Status")
        health_info = ", health=%s" % health if health else ""
        logger.info("容器 %s: status=%s%s", name, status, health_info)

        bad_lines = scan_container_logs(container)
        if bad_lines and name not in (allow_degraded or set()):
            logger.warning("容器 %s 发现疑似致命日志:", name)
            for bl in bad_lines[:3]:
                logger.warning("    -> %s", bl)
            all_passed = False

    if all_passed:
        logger.info("ZEN70 基础设施全线绿灯")
    else:
        logger.error("基础设施验真未通过，请排查上述警告。")
        if exit_on_fail:
            sys.exit(1)

    return all_passed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    allow_degraded_arg = next((arg for arg in sys.argv if arg.startswith("--allow-degraded=")), "")
    allow_degraded = {item.strip() for item in allow_degraded_arg.replace("--allow-degraded=", "").split(",") if item.strip()}
    if "--ci-container" in sys.argv:
        verify_infrastructure(
            exit_on_fail=True,
            allow_degraded=allow_degraded,
            required_healthy=DEFAULT_CORE_CONTAINERS,
        )
        run_ci_pipeline(exit_on_fail=True, use_container_e2e=True)
    elif "--ci" in sys.argv:
        run_ci_pipeline(exit_on_fail=True)
    else:
        verify_infrastructure(
            exit_on_fail=True,
            allow_degraded=allow_degraded,
            required_healthy=DEFAULT_CORE_CONTAINERS,
        )
