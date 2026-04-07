"""
ZEN70 功能开关 & 系统配置模型
法典 §2.3.1: 协议驱动 UI，动态开关
法典 §2.2.3: 设备探针与硬件解耦 — 严禁硬编码模型

现代软件工程：DB 持久化 + Redis 热缓存双层架构
"""

from __future__ import annotations

import datetime
import os

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class FeatureFlag(Base):
    """
    功能开关表。
    指挥官可通过后台管理界面切换开关状态。
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SystemConfig(Base):
    """
    系统配置表 (Key-Value)。
    指挥官可在设置页自行修改 AI 模型、推理超时等参数。
    严禁在代码中硬编码任何模型名称！一切从此表读取。
    """

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )


# Phase 5.2 修复：工厂函数替代模块级 ORM 实例
# 原 DEFAULT_FLAGS / DEFAULT_CONFIGS 是模块级 ORM 实例 —
# 首次 session.add() 后绑定到 Session，跨请求复用触发 DetachedInstanceError。
# 现改为每次调用返回全新实例。


def get_default_flags() -> list[FeatureFlag]:
    """返回预置开关清单（每次返回全新实例，防止跨请求 Session 污染）。"""
    return [
        FeatureFlag(
            key="ai_semantic_search",
            enabled=False,
            description="AI 语义搜索：输入自然语言检索照片与安防快照",
            category="ai",
        ),
        FeatureFlag(
            key="ai_auto_tagging",
            enabled=False,
            description="AI 智能标签：自动为上传的照片打标签（风景/美食/人物等）",
            category="ai",
        ),
        FeatureFlag(
            key="ai_security_recall",
            enabled=False,
            description="AI 安防回溯：在向量空间中融合 Frigate 抓拍帧，支持自然语言搜索监控事件",
            category="ai",
        ),
        FeatureFlag(
            key="jellyfin_streaming",
            enabled=False,
            description="Jellyfin 流媒体引擎：启用家庭影院、音乐播放与硬件转码",
            category="media",
        ),
        FeatureFlag(
            key="mqtt_iot_bus",
            enabled=False,
            description="MQTT 物联总线：启用智能家居设备联动与安防事件监听",
            category="iot",
        ),
        FeatureFlag(
            key="emotion_capture",
            enabled=False,
            description="情感资产沉淀：自动截取微笑、拥抱等温馨瞬间归档（需授权）",
            category="ai",
        ),
        FeatureFlag(
            key="local_llm_agent",
            enabled=False,
            description="本地大模型 Agent：强制 JSON-only 输出 + switch:events 闭环（可开可关）",
            category="agent",
        ),
        FeatureFlag(
            key="memory_rumination",
            enabled=False,
            description="对话记忆反刍：Redis 入队 + 夜间/低负载提取 facts + pgvector 落盘",
            category="memory",
        ),
        FeatureFlag(
            key="memory_daily_summary",
            enabled=False,
            description="对话日总结：夜间聚合生成 summary，供快速上下文回填",
            category="memory",
        ),
    ]


def get_default_configs() -> list[SystemConfig]:
    """返回预置系统配置（每次返回全新实例，防止跨请求 Session 污染）。"""
    return [
        # AI 配置
        SystemConfig(
            key="ai_model_id",
            value="openai/clip-vit-base-patch32",
            description="当前使用的 AI 视觉模型（可在设置页切换）",
        ),
        SystemConfig(
            key="ai_inference_timeout",
            value="5",
            description="单次 AI 推理超时秒数（极刑熔断）",
        ),
        SystemConfig(key="ai_worker_interval", value="10", description="AI Worker 扫描间隔（秒）"),
        SystemConfig(key="ai_max_batch_size", value="10", description="AI Worker 每轮最大处理资产数"),
        # 网络与端口
        SystemConfig(key="backend_port", value="8000", description="后端 API 服务端口"),
        SystemConfig(key="frontend_port", value="5173", description="前端开发服务端口"),
        SystemConfig(key="caddy_http_port", value="80", description="Caddy 反向代理 HTTP 端口"),
        SystemConfig(key="caddy_https_port", value="443", description="Caddy 反向代理 HTTPS 端口"),
        # 域名配置
        SystemConfig(
            key="caddy_domain",
            value="",
            description="Caddy 反代挂载域名（如 home.example.com）",
        ),
        SystemConfig(
            key="cf_tunnel_domain",
            value="",
            description="Cloudflare Tunnel 公网域名（如 zen70.example.com）",
        ),
        SystemConfig(
            key="headscale_domain",
            value="",
            description="Headscale/WireGuard 内网域名（如 hc.internal）",
        ),
        # 存储路径
        SystemConfig(
            key="media_path",
            value=os.getenv("MEDIA_PATH", ""),
            description="媒体文件存储根路径（来自 system.yaml）",
        ),
        SystemConfig(
            key="jellyfin_data_path",
            value=(f"{os.getenv('MEDIA_PATH', '').strip()}/jellyfin".strip("/") if os.getenv("MEDIA_PATH", "").strip() else ""),
            description="Jellyfin 媒体库路径",
        ),
        # 告警通道（严禁硬编码外部端点）
        SystemConfig(
            key="bark_url",
            value=os.getenv("BARK_URL", ""),
            description="Bark 推送 URL（如 https://api.day.app/<key>）",
        ),
        SystemConfig(key="bark_icon_url", value="", description="Bark 通知 icon URL（可选）"),
        SystemConfig(
            key="server_chan_key",
            value=os.getenv("SERVER_CHAN_KEY", ""),
            description="Server酱 SendKey（可选）",
        ),
        SystemConfig(
            key="server_chan_api_base",
            value="https://sctapi.ftqq.com",
            description="Server酱 API Base（可选）",
        ),
        # 路由控制（严禁写死 Caddy Admin 地址）
        SystemConfig(
            key="caddy_admin_url",
            value=os.getenv("CADDY_ADMIN_URL", ""),
            description="Caddy Admin API URL（如 http://caddy:2019/load）",
        ),
        # 外部服务端点（由 IaC/设置注入）
        SystemConfig(
            key="jellyfin_url",
            value=os.getenv("JELLYFIN_URL", ""),
            description="Jellyfin URL（如 http://jellyfin:8096）",
        ),
    ]


# 向后兼容别名（直接引用 DEFAULT_FLAGS/DEFAULT_CONFIGS 的调用方迁移期间使用）
DEFAULT_FLAGS = get_default_flags()
DEFAULT_CONFIGS = get_default_configs()

# =========================================================================
# 可选模型注册表 — 指挥官从此列表中挑选，严禁让代码决定！
# =========================================================================
AVAILABLE_MODELS = [
    {
        "id": "openai/clip-vit-base-patch32",
        "name": "CLIP ViT-B/32 (OpenAI)",
        "size": "~600MB",
        "dim": 512,
        "speed": "快",
        "quality": "标准",
        "description": "经典基础模型，速度与精度平衡。适合入门和低显存设备。",
    },
    {
        "id": "openai/clip-vit-large-patch14",
        "name": "CLIP ViT-L/14 (OpenAI)",
        "size": "~1.7GB",
        "dim": 768,
        "speed": "中",
        "quality": "高",
        "description": "大尺度模型，更强的语义理解能力。需要 4GB+ 显存。",
    },
    {
        "id": "laion/CLIP-ViT-H-14-laion2B-s32B-b79K",
        "name": "CLIP ViT-H/14 (LAION-2B)",
        "size": "~3.5GB",
        "dim": 1024,
        "speed": "慢",
        "quality": "极高",
        "description": "开源社区训练的超大模型，语义能力顶级。需 8GB+ 显存。",
    },
    {
        "id": "M-CLIP/XLM-Roberta-Large-Vit-B-32",
        "name": "Multilingual CLIP (XLM-R)",
        "size": "~2.0GB",
        "dim": 512,
        "speed": "中",
        "quality": "高",
        "description": "支持中文、日文等多语言查询。推荐中文家庭用户使用！",
    },
    {
        "id": "CN-CLIP/ViT-B-16",
        "name": "CN-CLIP ViT-B/16 (中文优化)",
        "size": "~800MB",
        "dim": 512,
        "speed": "快",
        "quality": "高（中文）",
        "description": "针对中文语义专门优化的 CLIP 模型。中文搜索效果最佳！",
    },
]
