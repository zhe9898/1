"""
ZEN70 IoT Bridge Worker - Redis Streams 双端泵送与异常消耗。

消费 REDIS_STREAM_KEY 中的 IoT 指令，执行后 ACK；超过 MAX_RETRIES 次失败的消息
打入 DLQ_STREAM_KEY 死信队列，防止毒药报文阻塞主链路。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("zen70.iot_bridge")

MAX_RETRIES: int = 3
REDIS_STREAM_KEY: str = "zen70:iot:commands"
DLQ_STREAM_KEY: str = "zen70:iot:dlq"
CONSUMER_GROUP: str = "zen70-iot-consumers"


class IoTBridgeWorker:
    """Redis Streams 消费者：处理 IoT 控制指令并管理死信队列。"""

    def __init__(self) -> None:
        self.redis: Any = None
        self.mqtt: Any = None

    async def _handle_command(self, message_id: str, data: dict[str, str]) -> None:
        """处理单条 IoT 控制指令。子类或测试可覆写此方法注入故障。"""
        action = data.get("action", "")
        logger.info("IoT command %s: action=%s", message_id, action)

    async def spin_loop(self) -> None:
        """主消费循环：从 Redis Stream 读取消息并处理。"""
        while True:
            streams = await self.redis.xreadgroup(
                CONSUMER_GROUP,
                "worker-1",
                {REDIS_STREAM_KEY: ">"},
                count=10,
                block=5000,
            )
            if not streams:
                continue
            for _stream_name, messages in streams:
                for message_id, data in messages:
                    retries = int(data.get("retry_count", 0))
                    try:
                        await self._handle_command(message_id, data)
                        await self.redis.xack(REDIS_STREAM_KEY, CONSUMER_GROUP, message_id)
                    except Exception as exc:
                        retries += 1
                        logger.warning("Command %s failed (attempt %s): %s", message_id, retries, exc)
                        if retries >= MAX_RETRIES:
                            await self.redis.xadd(DLQ_STREAM_KEY, data, maxlen=10000, approximate=True)
                            await self.redis.xack(REDIS_STREAM_KEY, CONSUMER_GROUP, message_id)
                        else:
                            # Re-add the message with incremented retry_count so subsequent
                            # deliveries track the cumulative failure count correctly.
                            updated = dict(data)
                            updated["retry_count"] = str(retries)
                            await self.redis.xadd(REDIS_STREAM_KEY, updated)
                            await self.redis.xack(REDIS_STREAM_KEY, CONSUMER_GROUP, message_id)
