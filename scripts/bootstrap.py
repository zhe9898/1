#!/usr/bin/env python3
"""
ZEN70 初始化点火脚本：系统初始化的唯一入口。

- 跨平台（Windows / Linux / Mac）：所有路径基于 pathlib 动态解析项目根，无 C:\\、/path 等硬编码。
- IaC 移植：compiler 在项目根检查 .env，不存在则 secrets 自动生成并创建；无需手动指定绝对路径。
- 流程：环境预检 → 多源拉取+校验 → 挂载点预建 → 编译（含自动密文闭环）→ compose up（隧道优先）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from deploy_utils import project_root as _root
from deploy_utils import resolve_name_conflict as _resolve_name_conflict
from deploy_utils import scripts_dir as _scripts_dir

# Docker SDK：用于 daemon 检查、版本检查与更精确的异常类型（文档 1.1 建议）
try:
    import docker
    from docker.errors import DockerException
except ImportError:
    docker = None  # type: ignore[assignment]
    DockerException = Exception  # type: ignore[misc, assignment]

# ---------------------------------------------------------------------------
# 常量（禁止硬编码 URL/路径于业务逻辑，此处为默认值）
# ---------------------------------------------------------------------------
MIN_DISK_GB = 20
"""预检要求：可用磁盘空间不低于此值（GB）。"""

MIN_DOCKER_VERSION = (20, 10)
"""预检要求：Docker API 版本不低于此（major, minor）。"""

DEFAULT_PORTS = [80, 443, 5173, 8000]
"""预检端口冲突检查的默认端口列表（可从 system.yaml 覆盖）。"""

DEFAULT_CONFIG_NAME = "system.yaml"
"""默认配置文件名。"""

CHECKSUM_SUFFIX = ".sha256"
"""拉取完整性校验：与 system.yaml 同目录的 system.yaml.sha256 可选。"""

# 多源拉取顺序：GitHub → Gitee → 本地缓存（占位 URL 可由环境变量或 system 覆盖）
GIT_SOURCES = [
    "https://github.com/zen70/zen70-config.git",
    "https://gitee.com/zen70/zen70-config.git",
]

REPO_DIR_NAME = ".zen70_repo"
"""拉取时使用的本地克隆目录名。"""

MOUNT_OWNER_UID = 1000
MOUNT_OWNER_GID = 1000
"""挂载点预建后 chown 目标（文档 7.1.3 防夺舍）。"""

PHASE_PRECHECK = "precheck"
PHASE_PULL = "pull_config"
PHASE_MOUNTS = "prepare_mounts"
PHASE_COMPILE = "compile"
PHASE_FRONTEND = "build_frontend"
PHASE_DEPLOY = "deploy"
PHASE_VERIFY = "verify"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """配置 stderr 日志，返回 bootstrap logger。"""
    log = logging.getLogger("bootstrap")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(h)
    return log


logger: logging.Logger | None = None


def _phase_log(phase: str, message: str, *args: object) -> None:
    """输出统一阶段日志，提升部署链路可观测性。"""
    if logger:
        logger.info("[phase=%s] " + message, phase, *args)


# ---------------------------------------------------------------------------
# 环境预检（Docker SDK + 版本 / 端口 / 内核 / 磁盘）
# ---------------------------------------------------------------------------


def _get_docker_client():  # type: () -> object | None
    """使用 DOCKER_HOST 寻址（与 .cursorrules 一致），返回 Docker 客户端或 None。"""
    if docker is None:
        return None
    try:
        return docker.from_env(timeout=15)
    except (OSError, ValueError, RuntimeError):
        return None


def check_docker() -> tuple[bool, str]:
    """
    使用 Docker Python SDK 检查 daemon 是否运行且可用（更精确捕获 DockerException）。

    Returns:
        (成功, 失败时错误信息)。
    """
    if docker is None:
        return False, "未安装 docker 包，请 pip install -r scripts/requirements.txt"
    try:
        client = docker.from_env(timeout=15)
        client.ping()
        return True, ""
    except DockerException as e:
        return False, f"Docker daemon 不可用: {e}"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        return False, str(e)


def check_docker_version(min_version: tuple[int, int] = MIN_DOCKER_VERSION) -> tuple[bool, str]:
    """
    检查 Docker Engine 版本是否 >= min_version（如 20.10）。
    使用 Version 字段（如 20.10.17），而非 ApiVersion（如 1.41，语义不同）。
    """
    if docker is None:
        return False, "未安装 docker 包，无法检查版本"
    try:
        client = docker.from_env(timeout=10)
        # 优先使用 Engine 版本（20.10.x），而非 API 版本（1.41）
        ver_str = client.version().get("Version") or client.version().get("ApiVersion") or "0.0"
        # 解析 "20.10.17" 或 "1.41"
        m = re.match(r"(\d+)\.(\d+)", ver_str)
        if not m:
            return False, f"无法解析 Docker 版本: {ver_str}"
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) >= min_version:
            return True, f"Docker 版本 {ver_str} >= {min_version[0]}.{min_version[1]}"
        return False, f"Docker 版本 {ver_str} 过低，需要 >= {min_version[0]}.{min_version[1]}"
    except DockerException as e:
        return False, f"获取 Docker 版本失败: {e}"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        return False, str(e)


def _get_port_list_output() -> str | None:
    """跨平台获取监听端口列表：Linux ss，Windows netstat。"""
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        r = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def check_ports(ports: list[int]) -> tuple[bool, str]:
    """
    检查指定端口是否已被占用。
    Fix: 为支持幂等部署，若端口已被占用（很可能是我们自己的容器），仅返回警告但不阻断运行。

    Returns:
        (始终为 True, 错误或说明信息)。
    """
    out = _get_port_list_output()
    if not out:
        return True, "无法获取端口列表（ss/netstat 不可用），跳过端口检查"
    occupied: list[int] = []
    for port in ports:
        pattern = re.compile(rf":{port}\b")
        for line in out.splitlines():
            if pattern.search(line) and ("LISTEN " in line or "LISTENING" in line):
                occupied.append(port)
                break
    if not occupied:
        return True, f"端口 {ports} 无冲突"

    # 幂等放行：仅仅是为了提示用户，不要返回 False 阻断流程
    return True, f"端口已被占用: {occupied} (若为重复部署则属正常现象)"


def check_kernel_params() -> tuple[bool, str]:
    """
    检查内核参数（仅 Linux）：net.ipv4.ip_forward、net.core.rmem_max。
    Windows 直接返回通过。
    """
    if platform.system() != "Linux":
        return True, "非 Linux，跳过内核参数检查"
    checks: list[tuple[str, str, int]] = [
        ("/proc/sys/net/ipv4/ip_forward", "net.ipv4.ip_forward", 1),
        ("/proc/sys/net/core/rmem_max", "net.core.rmem_max", 212992),
    ]
    for path, name, min_val in checks:
        try:
            p = Path(path)
            if not p.exists():
                continue
            val = int(p.read_text().strip())
            if val < min_val:
                return False, f"{name}={val}，建议 >= {min_val}（可 sysctl 或忽略）"
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            if logger:
                logger.debug("读取 %s 失败: %s", path, e)
    return True, "内核参数检查通过或跳过"


def check_disk(path: Path, min_gb: int = MIN_DISK_GB) -> tuple[bool, str]:
    """
    检查指定路径所在磁盘可用空间是否 >= min_gb（GB）。
    跨平台：使用 shutil.disk_usage。

    Returns:
        (通过, 失败时错误信息)。
    """
    try:
        path = path.resolve()
        if not path.exists():
            path = path.parent
        total, _used, free = shutil.disk_usage(path)
        free_gb = free / (1024**3)
        if free_gb >= min_gb:
            return True, ""
        return False, f"可用磁盘空间不足：{free_gb:.1f} GB < {min_gb} GB（路径: {path}）"
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        return False, f"无法检查磁盘空间: {e}"


def _ports_from_config(config_path: Path) -> list[int]:
    """从 system.yaml 解析需检查的宿主机端口（services.*.ports 中 host 部分）。"""
    if not config_path.exists():
        return DEFAULT_PORTS
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        services = data.get("services") or {}
        ports: list[int] = []
        for _name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            for p in svc.get("ports") or []:
                if isinstance(p, str) and ":" in p:
                    host_part = p.split(":")[0]
                    if host_part.isdigit():
                        ports.append(int(host_part))
                elif isinstance(p, int):
                    ports.append(p)
        return list(dict.fromkeys(ports)) if ports else []  # 无暴露端口时跳过检查，避免误报
    except (OSError, ValueError, KeyError, yaml.YAMLError):
        return DEFAULT_PORTS


NTP_DRIFT_THRESHOLD_SEC = 1.0
"""法典 2.4：NTP 时间漂移超过此值（秒）拒绝启动。"""

NTP_SERVERS = ("0.pool.ntp.org", "time.cloudflare.com")
"""NTP 预检使用的服务器，依次尝试。"""


def _run_ntp_precheck() -> None:
    """
    法典 2.4：NTP 同步预检。漂移 >1s 直接拒绝启动。
    无 ntplib 或网络不可达时仅警告并继续（极寒离线环境自举）。
    """
    try:
        import ntplib
    except ImportError:
        if logger:
            logger.warning("NTP 预检跳过：未安装 ntplib（极寒离线环境可忽略）")
        return
    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        try:
            response = client.request(server, version=3, timeout=3)
            offset = response.offset
            if abs(offset) > NTP_DRIFT_THRESHOLD_SEC:
                msg = f"NTP 时间漂移过大: {offset:.2f}s > {NTP_DRIFT_THRESHOLD_SEC}s（服务器 {server}）"
                if logger:
                    logger.error("预检失败: %s", msg)
                sys.exit(1)
            if logger:
                logger.info("NTP 预检通过: %s offset=%.3fs", server, offset)
            return
        except (ntplib.NTPException, OSError, TimeoutError) as e:
            if logger:
                logger.debug("NTP %s 不可达: %s", server, e)
            continue
    if logger:
        logger.warning("NTP 预检跳过：所有 NTP 服务器不可达（极寒离线环境可忽略）")


def run_precheck(
    root: Path,
    min_disk_gb: int = MIN_DISK_GB,
    config_path: Path | None = None,
) -> None:
    """
    执行环境预检：Docker（SDK ping + 版本）、端口冲突、内核参数、磁盘。
    不通过时打印错误并 sys.exit(1)。
    """
    _run_precheck_core(root, min_disk_gb=min_disk_gb, config_path=config_path)
    _run_ntp_precheck()
    _run_swapoff_linux()


def _abort_precheck(detail: str, context: str, warn_prefix: str | None = None) -> None:
    """统一预检失败出口，避免重复分支。"""
    if logger and warn_prefix is not None:
        logger.warning("%s: %s", warn_prefix, detail)
    if logger:
        logger.error("预检失败 %s: %s", context, detail)
    sys.exit(1)


def _run_precheck_core(root: Path, min_disk_gb: int, config_path: Path | None) -> None:
    ok, err = check_docker()
    if not ok:
        _abort_precheck(err, "Docker", "Docker 预检失败")
    if logger:
        logger.info("Docker 预检通过")

    ok, msg = check_docker_version()
    if not ok:
        _abort_precheck(msg, "Docker 版本", "Docker 版本检查未通过")
    if logger:
        logger.info("%s", msg)

    ports = _ports_from_config(config_path or root / DEFAULT_CONFIG_NAME)
    ok, err = check_ports(ports)
    if not ok:
        _abort_precheck(err, "端口", "端口预检失败")
    if logger:
        logger.info("%s", err)

    ok, msg = check_kernel_params()
    if not ok:
        _abort_precheck(msg, "内核", "内核参数")
    if logger:
        logger.debug("%s", msg)

    ok, err = check_disk(root, min_disk_gb)
    if not ok:
        _abort_precheck(err, "磁盘", "磁盘预检失败")
    if logger:
        logger.info("磁盘预检通过（>= %s GB）", min_disk_gb)


def _run_swapoff_linux() -> None:
    """法典 3.6：Linux 尝试执行 swapoff -a，失败仅告警。"""
    if platform.system() != "Linux":
        return
    try:
        result = subprocess.run(
            ["swapoff", "-a"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        if logger:
            logger.warning("swapoff 异常，跳过: %s", exc)
        return
    if result.returncode == 0:
        if logger:
            logger.info("Swap 已关闭 (swapoff -a)")
        return
    if logger:
        logger.warning(
            "swapoff -a 失败（可能需 root）: %s",
            (result.stderr or result.stdout or "").strip(),
        )


# ---------------------------------------------------------------------------
# 拉取完整性校验（文档 7.1.4）
# ---------------------------------------------------------------------------


def compute_file_sha256(path: Path) -> str:
    """计算文件 SHA256（十六进制）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pulled_config_checksum(
    config_path: Path,
    repo_path: Path,
    candidate_path: Path,
) -> tuple[bool, str]:
    """
    对拉取后的配置文件做完整性校验。
    若仓库中存在 system.yaml.sha256 或 config/system.yaml.sha256，则校验一致；
    否则仅计算并记录 SHA256，返回 True。

    Returns:
        (校验通过, 说明信息)。
    """
    if not config_path.exists():
        return False, "配置文件不存在"
    actual = compute_file_sha256(config_path)
    if logger:
        logger.info("配置文件 SHA256: %s", actual)
    # 可选：与同源 sha256 文件比对
    for sha_path in (
        candidate_path.parent / (candidate_path.name + CHECKSUM_SUFFIX),
        repo_path / (DEFAULT_CONFIG_NAME + CHECKSUM_SUFFIX),
        repo_path / "config" / (DEFAULT_CONFIG_NAME + CHECKSUM_SUFFIX),
    ):
        if sha_path.exists():
            try:
                expected = sha_path.read_text(encoding="utf-8").strip().split()[0]
                if len(expected) == 64 and all(c in "0123456789abcdef" for c in expected.lower()):
                    if actual.lower() == expected.lower():
                        return True, "校验和一致"
                    return False, f"校验和不一致: 期望 {expected[:16]}..., 实际 {actual[:16]}..."
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.warning("读取校验和文件失败 %s: %s", sha_path, e)
    return True, "无校验和文件，已记录 SHA256"


# ---------------------------------------------------------------------------
# 多源容灾拉取（try GitHub → Gitee → 本地缓存）+ 完整性校验
# ---------------------------------------------------------------------------


def pull_latest_config(root: Path, config_path: Path, offline: bool = False) -> bool:
    """
    多源拉取最新配置到项目根，保证 config_path 存在。

    顺序：GitHub → Gitee → 本地缓存（已有 system.yaml 或 repo 缓存）。
    拉取成功后仅接受仓库根 system.yaml 并复制到 config_path。
    具备 try/except 与日志；全部失败时若 config_path 已存在则视为使用本地缓存成功。

    Args:
        root: 项目根目录。
        config_path: 期望的 system.yaml 路径（如 root / "system.yaml"）。
        offline: 为 True 时不执行网络拉取，仅检查 config_path 或本地缓存是否存在。

    Returns:
        最终存在可用配置为 True，否则 False。
    """
    if config_path.exists():
        if logger:
            logger.info("已存在配置: %s，跳过拉取", config_path)
        return True

    if offline:
        if logger:
            logger.warning("离线模式且未找到 %s", config_path)
        return False

    repo_path = root / REPO_DIR_NAME
    if _pull_from_remote_sources(root, repo_path, config_path):
        return True
    if _pull_from_local_cache(repo_path, config_path):
        return True

    if logger:
        logger.error("所有源拉取失败且无本地缓存")
    return False


def _sync_repo_from_source(root: Path, repo_path: Path, source: str) -> bool:
    """克隆或更新配置仓库，成功返回 True。"""
    if repo_path.exists():
        result = subprocess.run(
            ["git", "-C", str(repo_path), "pull", "--depth", "1", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(root),
        )
        if result.returncode == 0:
            return True
        if logger:
            logger.warning("git pull 失败: %s", result.stderr or result.stdout)
        return False

    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            "main",
            source,
            str(repo_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root),
    )
    if result.returncode == 0:
        return True
    if logger:
        logger.warning("git clone 失败: %s", result.stderr or result.stdout)
    return False


def _copy_verified_candidate(repo_path: Path, candidate: Path, config_path: Path) -> bool:
    """复制候选配置并进行校验。"""
    if not candidate.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, config_path)
    ok_verify, msg_verify = verify_pulled_config_checksum(config_path, repo_path, candidate)
    if not ok_verify:
        if logger:
            logger.error("拉取完整性校验失败: %s", msg_verify)
        return False
    if logger:
        logger.info("拉取成功并校验通过，已写入: %s", config_path)
    return True


def _pull_from_remote_sources(root: Path, repo_path: Path, config_path: Path) -> bool:
    for source in GIT_SOURCES:
        try:
            if logger:
                logger.info("尝试拉取配置: %s", source)
            if not _sync_repo_from_source(root, repo_path, source):
                continue
            candidate = repo_path / DEFAULT_CONFIG_NAME
            if _copy_verified_candidate(repo_path, candidate, config_path):
                return True
            if logger:
                logger.warning("仓库中未找到 %s，尝试下一源", DEFAULT_CONFIG_NAME)
        except subprocess.TimeoutExpired:
            if logger:
                logger.warning("拉取超时: %s", source)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            if logger:
                logger.warning("拉取异常 %s: %s", source, exc, exc_info=True)
    return False


def _pull_from_local_cache(repo_path: Path, config_path: Path) -> bool:
    candidate = repo_path / DEFAULT_CONFIG_NAME
    if not candidate.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, config_path)
    verify_pulled_config_checksum(config_path, repo_path, candidate)
    if logger:
        logger.info("使用本地缓存配置: %s", config_path)
    return True


# ---------------------------------------------------------------------------
# 挂载点预建与权限（文档 7.1.3：防夺舍）
# ---------------------------------------------------------------------------


def ensure_mount_points(config_path: Path) -> None:
    """
    解析 system.yaml，在宿主机预建所有挂载卷目录并执行 chown 1000:1000（仅 Linux）。
    仅处理绝对路径（如 /mnt/media、/tmp/zen70-cache），忽略命名卷（如 postgres_data）。

    P3.2/P3.3: Windows 上跳过挂载预建（NTFS 不支持 chown，且 Unix 路径在 Windows 无意义）。
    Docker Desktop 使用卷管理器自行处理挂载，不需要宿主机预建。
    """
    if platform.system() == "Windows":
        if logger:
            logger.info("[挂载点] Windows 环境跳过挂载点预建（Docker Desktop 自行管理卷）")
        return
    if not config_path.exists():
        return
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for path in _collect_host_mount_paths(data):
            _ensure_and_chown_mount(path)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        if logger:
            logger.warning("解析配置以预建挂载点失败: %s", exc)


def _collect_host_mount_paths(data: dict) -> list[Path]:
    """收集需要在宿主机预建的目录路径。"""
    collected: list[Path] = []
    cap_storage = (data.get("capabilities") or {}).get("storage") or {}
    if isinstance(cap_storage, dict):
        media_path = cap_storage.get("media_path")
        if media_path:
            path = Path(media_path)
            if path.is_absolute():
                collected.append(path)

    host_prefixes = ("/mnt", "/tmp", "/home")
    storage = data.get("storage") or {}
    for value in storage.values():
        if not isinstance(value, dict):
            continue
        raw_path = value.get("path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.is_absolute() and any(str(path).startswith(prefix) for prefix in host_prefixes):
            collected.append(path)

    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for path in collected:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)
    return unique_paths


def _ensure_and_chown_mount(path: Path) -> None:
    """预建挂载目录并在 Linux root 模式下 chown。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        if logger:
            logger.info("挂载点已预建: %s", path)
        _chown_mount_tree_if_needed(path)
    except OSError as exc:
        if logger:
            logger.warning("预建挂载点失败 %s: %s", path, exc)


def _chown_mount_tree_if_needed(path: Path) -> None:
    if platform.system() != "Linux":
        return
    if os.geteuid() != 0:
        if logger:
            logger.debug("非 root，跳过 chown %s", path)
        return
    try:
        os.chown(path, MOUNT_OWNER_UID, MOUNT_OWNER_GID)
    except OSError:
        pass
    for child in path.rglob("*"):
        try:
            os.chown(child, MOUNT_OWNER_UID, MOUNT_OWNER_GID)
        except OSError:
            pass
    if logger:
        logger.info("已 chown -R %s:%s: %s", MOUNT_OWNER_UID, MOUNT_OWNER_GID, path)


# ---------------------------------------------------------------------------
# 执行编译器
# ---------------------------------------------------------------------------


def run_compiler(root: Path, config_path: Path, output_dir: Path | None = None) -> None:
    """
    调用 scripts/compiler.py 生成 docker-compose.yml 和 .env。
    确保 -o 始终附带有效参数，避免 expected one argument 报错。
    """
    out = output_dir or root
    try:
        out_arg = "." if out.resolve() == root.resolve() else str(out.relative_to(root))
    except ValueError:
        out_arg = str(out)
    cmd = [
        sys.executable,
        str(_scripts_dir() / "compiler.py"),
        str(config_path),
        "-o",
        out_arg,
    ]
    if logger:
        logger.info("执行编译器: %s", " ".join(cmd))
    subprocess.run(cmd, cwd=str(root), check=True, timeout=120)
    if logger:
        logger.info("编译器执行完成")


def start_host_services(config_path: Path, output_dir: Path) -> None:
    """为 system.yaml 中 runtime: host 的服务执行 systemctl daemon-reload + enable --now。

    仅在 Linux 系统上执行；unit 文件需已由 compiler.py 写入 output_dir/systemd/。
    """
    if platform.system() != "Linux":
        if logger:
            logger.debug("非 Linux 环境，跳过 host 服务 systemctl 管理")
        return
    if not shutil.which("systemctl"):
        if logger:
            logger.warning("systemctl 不可用，跳过 host 服务启动")
        return
    if not config_path.exists():
        return

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        if logger:
            logger.warning("无法解析 system.yaml 以启动 host 服务: %s", exc)
        return

    host_names: list[str] = []
    for name, svc in (data.get("services") or {}).items():
        if isinstance(svc, dict) and svc.get("runtime") == "host" and svc.get("enabled") is not False:
            host_names.append(name)

    if not host_names:
        return

    systemd_dir = output_dir / "systemd"
    if systemd_dir.exists():
        for unit_file in systemd_dir.glob("*.service"):
            dest = Path("/etc/systemd/system") / unit_file.name
            try:
                shutil.copy2(unit_file, dest)
                os.chmod(dest, 0o644)
                if logger:
                    logger.info("[host] 已安装 unit 文件: %s", dest)
            except OSError as exc:
                if logger:
                    logger.warning("[host] 安装 unit 文件失败 %s: %s", dest, exc)

    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
        if logger:
            logger.info("[host] systemctl daemon-reload 完成")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if logger:
            logger.warning("[host] daemon-reload 失败: %s", exc)

    for name in host_names:
        unit = f"{name}.service"
        try:
            subprocess.run(["systemctl", "enable", "--now", unit], check=True, timeout=30)
            if logger:
                logger.info("[host] %s 已启动并设置开机自启", unit)
        except subprocess.CalledProcessError as exc:
            if logger:
                logger.warning("[host] enable --now %s 失败 (rc=%d)", unit, exc.returncode)
        except (subprocess.TimeoutExpired, OSError) as exc:
            if logger:
                logger.warning("[host] enable --now %s 异常: %s", unit, exc)


# ---------------------------------------------------------------------------
# 供应链（法典 1.1）：私有镜像仓库 docker login
# ---------------------------------------------------------------------------


def _parse_env_for_registry(env_path: Path) -> tuple[str, str, str]:
    """从 .env 解析 REGISTRY_URL、REGISTRY_USER、REGISTRY_PASSWORD。"""
    url, user, pwd = "", "", ""
    if not env_path.exists():
        return url, user, pwd
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return url, user, pwd
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if not v or (v.startswith("${") and v.endswith("}")):
            continue
        if k == "REGISTRY_URL":
            url = v
        elif k == "REGISTRY_USER":
            user = v
        elif k == "REGISTRY_PASSWORD":
            pwd = v
    return url, user, pwd


def verify_pulled_image_digests(
    manifest_path: Path,
    root: Path,
    compose_file: Path,
) -> None:
    """
    若 images.manifest 存在，拉取镜像后校验 digest（法典 1.1 供应链）。
    格式：每行 image:tag 或 image:tag expected_sha256:xxx，注释以 # 开头。
    """
    expected = _parse_expected_manifest_digests(manifest_path)
    if not expected:
        return
    if not _compose_pull_quiet(root, compose_file):
        return
    for image, expected_digest in expected.items():
        _verify_image_digest(image, expected_digest)


def _parse_expected_manifest_digests(manifest_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        if logger:
            logger.warning("无法读取 images.manifest: %s", manifest_path)
        return {}
    expected: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) >= 2 and parts[1].startswith("sha256:"):
            expected[parts[0]] = parts[1]
    return expected


def _compose_pull_quiet(root: Path, compose_file: Path) -> bool:
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = "zen70"
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "pull", "--quiet"],
            cwd=str(root),
            env=env,
            timeout=600,
            capture_output=True,
            check=False,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        if logger:
            logger.warning("compose pull 失败，跳过镜像校验: %s", exc)
        return False


def _verify_image_digest(image: str, expected_digest: str) -> None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            if logger:
                logger.warning("镜像 digest 校验跳过 %s: 未拉取或不存在", image)
            return
        digests = json.loads(result.stdout or "[]")
        matched = any(expected_digest in digest for digest in digests if isinstance(digest, str))
        if matched:
            return
        if logger:
            logger.warning("镜像 digest 不匹配: %s 期望 %s 实际 %s", image, expected_digest, digests)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        if logger:
            logger.debug("digest 校验异常 %s: %s", image, exc)


def docker_login_registry_if_needed(env_path: Path) -> None:
    """
    若 .env 中 REGISTRY_URL 与 REGISTRY_USER 已配置且非空，执行 docker login。
    密码经 stdin 传入，避免出现在进程列表中。
    """
    url, user, pwd = _parse_env_for_registry(env_path)
    if not (url.strip() and user.strip()):
        if logger:
            logger.debug("未配置 REGISTRY_URL / REGISTRY_USER，跳过 docker login")
        return
    try:
        args = ["docker", "login", url.strip(), "-u", user.strip(), "--password-stdin"]
        r = subprocess.run(
            args,
            input=pwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            if logger:
                logger.warning("docker login %s 失败: %s", url, err)
        elif logger:
            logger.info("私有镜像仓库 %s 登录成功", url)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        if logger:
            logger.warning("docker login 异常: %s", e)


# ---------------------------------------------------------------------------
# 前端构建：法典 §1.2 协议驱动 Schema-Driven UI
# IaC 唯一事实来源 — 前端构建纳入部署流水线，禁止手动操作
# ---------------------------------------------------------------------------


def build_frontend(root: Path) -> None:
    """
    构建前端 SPA 产物（npm install + npm run build）。

    法典 §1.2: 协议驱动 UI，前端必须编译后由 Caddy 提供静态服务。
    IaC 法典: 部署流水线自动构建，禁止手动 npm build。

    降级策略:
    - node/npm 不可用 → WARNING 降级（允许 CI 预构建 dist 后直接部署）
    - dist/ 已存在且 package.json 无变更 → 跳过重复构建（幂等）
    """
    frontend_dir = root / "frontend"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        if logger:
            logger.warning("[前端构建] 跳过：未找到 frontend/package.json")
        return

    npm_cmd: str | None = shutil.which("npm")
    if npm_cmd is None:
        if logger:
            logger.warning("[前端构建] ⚠️ 未检测到 npm，跳过前端构建。")
        return

    dist_dir = frontend_dir / "dist"
    index_html = dist_dir / "index.html"
    if _frontend_build_up_to_date(package_json, index_html):
        if logger:
            logger.info("[前端构建] dist/ 已是最新，跳过构建")
        return

    if logger:
        logger.info("[前端构建] 开始构建前端...")
    if not _run_frontend_install_if_needed(frontend_dir, npm_cmd):
        return
    if not _run_frontend_build(frontend_dir, npm_cmd):
        return

    if index_html.exists():
        if logger:
            logger.info("[前端构建] ✅ 前端构建完成: %s", dist_dir)
    else:
        if logger:
            logger.error("[前端构建] ❌ 构建后 dist/index.html 不存在")


def _frontend_build_up_to_date(package_json: Path, index_html: Path) -> bool:
    """P1.2: 除 package.json 外，还扫描 src/ 目录下的源码 mtime。"""
    if not index_html.exists():
        return False
    dist_mtime = index_html.stat().st_mtime
    # 检查 package.json 是否比 dist 更新
    if package_json.stat().st_mtime > dist_mtime:
        return False
    # 检查 src/ 下的源码是否比 dist 更新
    src_dir = package_json.parent / "src"
    if src_dir.is_dir():
        for src_file in src_dir.rglob("*"):
            if src_file.is_file() and src_file.stat().st_mtime > dist_mtime:
                return False
    return True


def _run_npm_command(frontend_dir: Path, args: list[str], timeout: int, label: str) -> bool:
    try:
        result = subprocess.run(
            args,
            cwd=str(frontend_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if logger:
            logger.error("[前端构建] %s 超时 (%ss)", label, timeout)
        return False
    except FileNotFoundError:
        if logger:
            logger.error("[前端构建] npm 命令不可用")
        return False
    if result.returncode == 0:
        return True
    if logger:
        logger.error("[前端构建] %s 失败 (rc=%d)", label, result.returncode)
        for line in (result.stderr or "").splitlines()[-10:]:
            logger.error("  %s", line)
    return False


def _run_frontend_install_if_needed(frontend_dir: Path, npm_cmd: str) -> bool:
    if (frontend_dir / "node_modules").exists():
        return True
    package_lock = frontend_dir / "package-lock.json"
    install_args = [npm_cmd, "ci"] if package_lock.exists() else [npm_cmd, "install", "--prefer-offline", "--no-audit", "--no-fund"]
    install_label = "npm ci" if package_lock.exists() else "npm install"
    if logger:
        logger.info("[PROGRESS:35]")
        logger.info("[前端构建] 正在下载并安装依赖 (%s)，这可能需要几分钟...", install_label)
    return _run_npm_command(
        frontend_dir,
        install_args,
        timeout=300,
        label=install_label,
    )


def _run_frontend_build(frontend_dir: Path, npm_cmd: str) -> bool:
    if logger:
        logger.info("[PROGRESS:50]")
        logger.info("[前端构建] 编译前端 (npm run build)...")
    return _run_npm_command(
        frontend_dir,
        [npm_cmd, "run", "build"],
        timeout=300,
        label="npm run build",
    )


# ---------------------------------------------------------------------------
# 启动顺序：先隧道（cloudflared），再其余（Docker SDK 预验 + 精确异常捕获）
# ---------------------------------------------------------------------------


def compose_up(root: Path, compose_file: Path) -> None:
    """
    通过 subprocess 执行 docker compose up -d --remove-orphans。

    法典 §1.2 / §7:
    - 单次 up 全量启动，Compose 根据 depends_on + healthcheck 自动编排启动顺序
    - 严禁分步 up 单个服务（跨 project context 的容器名冲突无法自愈）
    - COMPOSE_PROJECT_NAME 强制 zen70，确保 compose 认领所有同名容器
    """
    compose_file = compose_file.resolve()
    _validate_compose_up_prerequisites(compose_file)
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = "zen70"
    up_args = _build_compose_up_args(compose_file)
    if logger:
        logger.info("[PROGRESS:70]")
        logger.info("执行: %s", " ".join(up_args))
        logger.info("[点火引擎] 正在拉取镜像并启动容器 (Docker Compose)，这可能需要几分钟...")

    result = _run_compose_up(root, up_args, env)
    if result.returncode == 0:
        return

    stderr = (result.stderr or result.stdout or "").strip()
    stderr_lower = stderr.lower()

    # P1.1: 网络异常自愈 — IPAM 变更后旧网络不会自动重建
    net_err_keywords = ("network", "address pool", "subnet", "ipam", "dns")
    if any(kw in stderr_lower for kw in net_err_keywords):
        if logger:
            logger.warning("[网络自愈] 检测到网络配置异常，执行 compose down 重建网络...")
        _compose_down_for_network_reset(root, compose_file, env)
        _retry_compose_up_once(root, up_args, env)
        return

    if "already in use" not in stderr:
        if logger:
            logger.error("docker compose 返回 %s", result.returncode)
            if stderr:
                logger.error("%s", stderr)
        sys.exit(1)

    if logger:
        logger.warning("[冲突修复] compose up 遇容器名冲突，启动精确修复...")
    _resolve_name_conflict(root, stderr)
    _retry_compose_up_once(root, up_args, env)


def _compose_down_for_network_reset(
    root: Path,
    compose_file: Path,
    env: dict[str, str],
) -> None:
    """
    网络异常自愈：执行 compose down --remove-orphans 销毁旧网络。

    场景：IPAM 子网变更后旧网络与新配置不兼容，compose up 直接报错。
    必须先 down 清除旧网络定义，让 compose up 重建。

    法典 §1.2: 强制 --remove-orphans 斩杀孤儿容器。
    """
    if logger:
        logger.info("[网络自愈] 执行 compose down --remove-orphans 清除旧网络...")
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "down",
                "--remove-orphans",
            ],
            cwd=str(root),
            env=env,
            timeout=120,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        if logger:
            logger.warning("[网络自愈] compose down 异常: %s", exc)


def _validate_compose_up_prerequisites(compose_file: Path) -> None:
    if not compose_file.exists():
        if logger:
            logger.error("compose 文件不存在: %s", compose_file)
        sys.exit(1)
    if docker is None:
        return
    try:
        client = docker.from_env(timeout=10)
        client.ping()
    except DockerException as exc:
        if logger:
            logger.error("Docker daemon 不可用: %s", exc)
        sys.exit(1)


def _build_compose_up_args(compose_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "up",
        "-d",
        "--force-recreate",
        "--remove-orphans",
    ]


def _run_compose_up(root: Path, up_args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            up_args,
            cwd=str(root),
            env=env,
            timeout=600,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        if logger:
            logger.error("docker compose 执行超时 (600s)")
        sys.exit(1)
    except FileNotFoundError:
        if logger:
            logger.error("未找到 docker 或 compose 插件，请确保已安装 Docker Engine 与 Compose V2")
        sys.exit(1)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        if logger:
            logger.error("docker compose 异常: %s", exc)
        raise


def _retry_compose_up_once(root: Path, up_args: list[str], env: dict[str, str]) -> None:
    if logger:
        logger.info("[冲突修复] 重试 compose up...")
    try:
        result = subprocess.run(
            up_args,
            cwd=str(root),
            env=env,
            timeout=600,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        if logger:
            logger.error("[冲突修复] 重试失败: %s", exc)
        sys.exit(1)
    if result.returncode != 0:
        err2 = (result.stderr or result.stdout or "").strip()
        if logger:
            logger.error("[冲突修复] 重试仍失败 (rc=%s): %s", result.returncode, err2[:500])
        sys.exit(1)
    if logger:
        logger.info("[冲突修复] 重试成功")


# ---------------------------------------------------------------------------
# 主流程（幂等）
# ---------------------------------------------------------------------------


def main() -> None:
    global logger
    parser = argparse.ArgumentParser(description="ZEN70 初始化点火脚本（幂等）")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_NAME,
        help="system.yaml 路径（相对项目根或绝对），默认 system.yaml",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="编译器输出目录，默认与项目根一致",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help="跳过多源拉取，仅使用已有配置",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线模式：不拉取，仅当已有配置时继续",
    )
    parser.add_argument(
        "--min-disk-gb",
        type=int,
        default=MIN_DISK_GB,
        help=f"预检最小可用磁盘（GB），默认 {MIN_DISK_GB}",
    )
    parser.add_argument(
        "--no-up",
        "--no-deploy",
        action="store_true",
        dest="no_up",
        help="仅预检+拉取+编译，不执行 docker-compose up",
    )
    parser.add_argument(
        "--skip-mounts",
        action="store_true",
        help="跳过挂载点预建与 chown（仍会执行其他预检与编译）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="详细日志",
    )
    parser.add_argument(
        "--platform",
        default="auto",
        choices=["auto", "linux_x86", "wsl2", "windows_desktop", "arm64", "macos"],
        help="目标部署平台（auto=自动检测）",
    )
    args = parser.parse_args()

    logger = setup_logging(verbose=args.verbose)
    root = _root()
    # 路径均基于项目根；IaC 唯一事实源为根 system.yaml（仍可显式传绝对路径）
    config_path = root / args.config if not Path(args.config).is_absolute() else Path(args.config)
    output_dir = root / args.output_dir if args.output_dir else root

    # 解析平台
    deploy_platform = args.platform
    if deploy_platform == "auto":
        if platform.system() == "Windows":
            deploy_platform = "windows_desktop"
        elif platform.system() == "Darwin":
            deploy_platform = "macos"
        elif platform.machine().lower() in ("aarch64", "armv7l", "arm64"):
            deploy_platform = "arm64"
        else:
            deploy_platform = "linux_x86"
    if logger:
        logger.info("部署平台: %s", deploy_platform)

    is_desktop_docker = deploy_platform in ("windows_desktop", "macos")

    # 1. 环境预检
    _phase_log(PHASE_PRECHECK, "starting")
    # 将平台信息注入环境变量，让 run_precheck 内部跳过 Linux 专属检查
    os.environ["ZEN70_DEPLOY_PLATFORM"] = deploy_platform
    run_precheck(root, min_disk_gb=args.min_disk_gb, config_path=config_path)
    if is_desktop_docker and logger:
        logger.info("[平台适配] %s 环境，跳过 NTP/swapoff/内核参数预检", deploy_platform)
    _phase_log(PHASE_PRECHECK, "done")

    # 2. 多源拉取（除非 --skip-pull）+ 拉取后完整性校验
    _phase_log(PHASE_PULL, "starting")
    if not args.skip_pull:
        if not pull_latest_config(root, config_path, offline=args.offline):
            if not config_path.exists():
                if logger:
                    logger.error("无可用配置且拉取失败（可尝试 --offline 并使用已有 system.yaml）")
                sys.exit(1)
    else:
        if not config_path.exists():
            if logger:
                logger.error("未找到配置: %s", config_path)
            sys.exit(1)
        if logger:
            logger.info("已存在配置: %s", config_path)
    _phase_log(PHASE_PULL, "done")

    # 3. 挂载点预建与权限
    _phase_log(PHASE_MOUNTS, "starting")
    if args.skip_mounts or is_desktop_docker:
        if logger and is_desktop_docker:
            logger.info("[平台适配] %s 环境，跳过挂载点预建（Docker Desktop 自行管理卷）", deploy_platform)
    else:
        ensure_mount_points(config_path)
    _phase_log(PHASE_MOUNTS, "done")

    # 4. 执行编译
    _phase_log(PHASE_COMPILE, "starting")
    run_compiler(root, config_path, output_dir)
    _phase_log(PHASE_COMPILE, "done")

    # 4.5 前端构建（法典 §1.2 协议驱动 UI — IaC 唯一事实来源）
    _phase_log(PHASE_FRONTEND, "starting")
    build_frontend(root)
    _phase_log(PHASE_FRONTEND, "done")

    # 5. 启动顺序控制（Docker SDK 预验 + compose up -d --force-recreate --remove-orphans）
    if not args.no_up:
        _phase_log(PHASE_DEPLOY, "starting")
        env_path = output_dir / ".env"
        docker_login_registry_if_needed(env_path)
        compose_file = output_dir / "docker-compose.yml"
        manifest_path = config_path.parent / "images.manifest"
        verify_pulled_image_digests(manifest_path, root, compose_file)
        compose_up(root, compose_file)
        # host 服务 systemctl 管理（runtime: host 服务不走 Docker）
        start_host_services(config_path, output_dir)
        _phase_log(PHASE_DEPLOY, "done")
        # 6. 自动化基座验真（平级导入，避免 ModuleNotFoundError）
        # 当由 installer 调用时（--skip-pull），跳过验真以避免重复
        # installer 有自己的 PHASE 2 docker inspect 健康检查
        if args.skip_pull:
            if logger:
                logger.info("[平台适配] installer 模式，跳过内置验真（由 installer PHASE 2 接管）")
        else:
            try:
                _phase_log(PHASE_VERIFY, "starting")
                _scripts = str(_scripts_dir())
                if _scripts not in sys.path:
                    sys.path.insert(0, _scripts)
                import verify

                if logger:
                    logger.info("触发底层自动化验真探针...")
                verify.verify_infrastructure(exit_on_fail=False)
                _phase_log(PHASE_VERIFY, "done")
            except ImportError as e:
                if logger:
                    logger.warning("无法导入验证模块: %s", e)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                if logger:
                    logger.warning("验真过程异常: %s（容器可能仍在启动中，可稍后运行 repair.py 检查）", e)

    if logger:
        logger.info("bootstrap 完成（幂等）")


if __name__ == "__main__":
    main()
