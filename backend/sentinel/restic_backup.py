"""
ZEN70 云端防勒索容灾备份 (Restic S3 Backup)
法典准则 §3.1.3:
采用 Restic，结合 S3 Object Lock (Anti-Ransomware)。
法典准则 §3.4.1:
负载感知避让，高并发推理时备份自动挂起。
"""

from __future__ import annotations

import logging
import os
import subprocess

import psutil

logger = logging.getLogger("zen70.sentinel.restic_backup")

# 环境变量应当在系统启动时，通过 IaC 或 bootstrap.py 注入
# 此处使用 os.environ 获取 S3 凭证和 Restic 密码


def check_system_load_for_backup() -> bool:
    """法典 3.4.1: 在执行增量推流前，核对 CPU 负载。"""
    cpu_usage = psutil.cpu_percent(interval=1)
    if cpu_usage > 75.0:
        logger.warning("⚠️ 系统当前 CPU 负载高达 %s%%，暂停高耗能推流，SLA 保护生效。", cpu_usage)
        return False

    # 法典 3.4.1：接入 Categraf/Prometheus 获取真实 GPU 负载
    gpu_metrics_url = os.getenv("CATEGRAF_GPU_METRICS_URL", "").strip()
    if gpu_metrics_url:
        try:
            import httpx

            resp = httpx.get(gpu_metrics_url, timeout=3.0)
            if resp.status_code == 200:
                # 解析 Prometheus 格式指标，查找 GPU 利用率
                for line in resp.text.splitlines():
                    if line.startswith("nvidia_gpu_utilization"):
                        parts = line.split()
                        if len(parts) >= 2:
                            gpu_usage = float(parts[-1])
                            if gpu_usage > 80.0:
                                logger.warning(
                                    "⚠️ GPU 负载 %s%%，暂停推流保护推理 SLA。",
                                    gpu_usage,
                                )
                                return False
                        break
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as gpu_err:
            logger.debug("Categraf GPU 指标查询失败（降级放行）: %s", gpu_err)
    return True


def run_restic_backup(
    repo_url: str,
    repository_password: str,
    target_paths: list[str],
    aws_access_key_id: str,
    aws_secret_access_key: str,
) -> bool:
    """执行 Restic 备份指令"""
    if not check_system_load_for_backup():
        return False

    env = os.environ.copy()
    env["RESTIC_PASSWORD"] = repository_password
    env["AWS_ACCESS_KEY_ID"] = aws_access_key_id
    env["AWS_SECRET_ACCESS_KEY"] = aws_secret_access_key

    # Restic 初始化仓库命令 (带 --repository-version 2 支持 S3 Object Lock)
    # 此处假设仓库已初始化，或者有专用 bootstrap 脚本处理

    cmd = ["restic", "-r", repo_url, "backup"] + target_paths

    try:
        logger.info("📤 开始向云端不可变存储桶推送加密快照: %s", target_paths)
        result = subprocess.run(
            cmd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        logger.info("✅ Restic 推流成功，快照已写入不可变存储（依赖 S3 Object Lock 策略）。")
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        logger.error("❌ 快照推送失败: %s", stderr)
        # 法典：集成 AlertManager 发送报错 Push
        alert_webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
        if alert_webhook:
            try:
                import httpx

                httpx.post(
                    alert_webhook,
                    json={
                        "level": "critical",
                        "title": "❌ Restic 备份失败",
                        "message": f"快照推送失败: {stderr[:500]}",
                        "source": "restic_backup",
                    },
                    timeout=5.0,
                )
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as alert_err:
                logger.error("告警下行通道故障: %s", alert_err)
        return False
    except subprocess.TimeoutExpired:
        logger.error("❌ Restic 推流超时（3600s），已中止以保护系统稳定性。")
        return False


def _get_required_env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v if v else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("启动 ZEN70 S3 灾备组件...")
    # 仅从环境变量读取，禁止硬编码机密（全文档合规）
    repo = _get_required_env("RESTIC_REPOSITORY")
    pw = _get_required_env("RESTIC_PASSWORD")
    ak = _get_required_env("AWS_ACCESS_KEY_ID")
    sk = _get_required_env("AWS_SECRET_ACCESS_KEY")
    targets_raw = os.getenv("RESTIC_TARGET_PATHS", "")
    targets = [p.strip() for p in targets_raw.split(",") if p.strip()]

    missing = [
        k
        for k, v in (
            ("RESTIC_REPOSITORY", repo),
            ("RESTIC_PASSWORD", pw),
            ("AWS_ACCESS_KEY_ID", ak),
            ("AWS_SECRET_ACCESS_KEY", sk),
        )
        if not v
    ]
    if missing or not targets:
        logger.error("缺少必需环境变量或目标路径。missing=%s targets=%s", missing, targets)
        raise SystemExit(2)

    run_restic_backup(
        repo_url=repo,  # type: ignore[arg-type]
        repository_password=pw,  # type: ignore[arg-type]
        target_paths=targets,
        aws_access_key_id=ak,  # type: ignore[arg-type]
        aws_secret_access_key=sk,  # type: ignore[arg-type]
    )
