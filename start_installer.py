#!/usr/bin/env python3
"""
ZEN70 图形化部署引火器启动入口。

自动检测并安装轻量依赖（FastAPI/Uvicorn/PyYAML/Pydantic/ruamel.yaml），
扫描可用端口后启动 Web 安装向导。
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("installer-launcher")


def ensure_dependencies() -> None:
    """
    确保图形化向导所需的轻量依赖已安装。

    若检测到缺失，自动通过 pip 安装。
    """
    packages = ["fastapi", "uvicorn", "pyyaml", "pydantic", "ruamel.yaml"]
    try:
        import fastapi  # noqa: F401
        import pydantic  # noqa: F401
        import uvicorn  # noqa: F401
        import yaml  # noqa: F401
        from ruamel.yaml import YAML  # noqa: F401
    except ImportError:
        logger.info(
            "未检测到图形化部署引火器所需的轻量依赖，正在自动获取 "
            "(FastAPI/Uvicorn/PyYAML/Pydantic/ruamel.yaml)..."
        )
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *packages],
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error("依赖安装失败: %s", e)
            sys.exit(1)
        logger.info("依赖拉取完毕！")


def find_free_port(start_port: int = 8080, max_port: int = 8099) -> int:
    """
    扫描 [start_port, max_port] 范围，返回第一个可用端口。

    Args:
        start_port: 起始端口（含）。
        max_port: 结束端口（含）。

    Returns:
        可用端口号；若全部占用则回退 start_port（uvicorn 会报端口冲突）。
    """
    for port in range(start_port, max_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    project_root = Path(__file__).resolve().parent
    sys.path.insert(0, str(project_root))

    print("\n" + "=" * 50)
    print("[*] 正在拉起 ZEN70 V2.0 图形化部署引擎...")
    print("=" * 50 + "\n")

    # S2 Fix: 确保依赖已安装（之前定义了但从未调用）
    ensure_dependencies()

    import uvicorn  # noqa: E402

    try:
        free_port = find_free_port()
        print(f"[>] 服务已预检启动，请在您的浏览器中访问: http://127.0.0.1:{free_port}\n")
        uvicorn.run(
            "installer.main:app",
            host="127.0.0.1",
            port=free_port,
            log_level="info",
        )
    except OSError as e:
        logger.error("图形向导启动失败: %s", e)
        print("💡 可能是系统网络栈异常或端口均被占用。")
        input("\n按回车键退出，防止窗口闪退...")
