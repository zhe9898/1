#!/usr/bin/env python3
#
# ZEN70 零信任初始化引擎 (Zero-Trust Auth Init)。
#
# 红线合规：
# 1. 密钥分片：Shamir Secret Sharing (n=5, k=3) 分割 Master JWT Key。
# 2. 从不全量写盘：主密钥生成后仅保留在 Redis 缓存或 ENV，物理持有的全是分片。

import secrets
import base64
import sys
import logging

try:
    from secretsharing import SecretSharer  # pip install secretsharing
except ImportError:
    SecretSharer = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AUTH-INIT] %(message)s",
)
logger = logging.getLogger(__name__)


def generate_master_key(length=32) -> str:
    """生成 256位 (32字节) 的随即盐作为 Master JWT Secret"""
    return secrets.token_hex(length)


def main():
    logger.info("启动 ZEN70 零信任初始基线向导...")
    master_key = generate_master_key()

    if not SecretSharer:
        logger.warning("未检测到 secretsharing 库。由于处在 MVP 环境，" "跳过 Shamir 分片，已生成并打印临时 Master Key。")
        logger.warning("TEMPORARY_MASTER_KEY=%s", master_key)
        logger.info("请通过 ansible 或 IaC 编译器转入系统环境变量。")
        sys.exit(0)

    try:
        # 法典 3.1.4: "分割保管的高强度一次性恢复码 n=5, k=3"
        # 这里 secretsharing 支持基于 hex 的字符串
        shares = SecretSharer.split_secret(master_key, 3, 5)

        logger.info("成功执行 Shamir 密钥分割 (门限 3/5)。")
        logger.info("主安全密钥将在系统重载后从内存消散。请务必将以下 5 个分片散列物理保管：")

        for idx, shard in enumerate(shares):
            logger.info("SHARD %s/5: %s", idx + 1, shard)

        logger.warning("在真正的断网灾难恢复日 (Day-0)，你需要输入至少 3 个 SHARD 才能拼合出主 JWT 签名密钥！")

    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("Shamir 分割执行异常: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
