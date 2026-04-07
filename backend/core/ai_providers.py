"""
ZEN70 AI 模型提供者抽象层 (Model Provider Abstraction)
法典 §2.2.1: 严禁硬编码任何模型名称或单一后端
法典 §2.2.3: 能力标签标准化与版本感知

全域 LLM 接入：Ollama、LM Studio、LocalAI、text-generation-webui、
vLLM、Jan、GPT4All 以及任何 OpenAI-compatible 后端。

所有 Provider 端点地址均可在系统设置页配置，严禁写死端口！
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger("zen70.ai_providers")


# =========================================================================
# Provider 端点默认值与环境变量对照表
# 指挥官可通过系统设置页修改 —— 此处仅为开箱即用的合理默认值
# =========================================================================

PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "ollama": {
        "label": "🦙 Ollama",
        "default_url": "",
        "env_key": "OLLAMA_URL",
        "description": "Ollama — 最流行的本地 LLM 运行时，一行命令跑模型",
        "api_format": "ollama",
        "default_port": 11434,
    },
    "lm_studio": {
        "label": "🖥️ LM Studio",
        "default_url": "",
        "env_key": "LM_STUDIO_URL",
        "description": "LM Studio — 图形化本地 LLM 客户端，兼容 OpenAI API",
        "api_format": "openai",
        "default_port": 1234,
    },
    "localai": {
        "label": "🏠 LocalAI",
        "default_url": "",
        "env_key": "LOCALAI_URL",
        "description": "LocalAI — 开源的自托管 AI 推理平台，支持 LLM/TTS/图像",
        "api_format": "openai",
        "default_port": 8080,
    },
    "text_gen_webui": {
        "label": "📝 text-generation-webui",
        "default_url": "",
        "env_key": "TEXT_GEN_WEBUI_URL",
        "description": "oobabooga text-generation-webui — 功能最全的 LLM Web UI",
        "api_format": "openai",
        "default_port": 5000,
    },
    "vllm": {
        "label": "⚡ vLLM",
        "default_url": "",
        "env_key": "VLLM_URL",
        "description": "vLLM — 高性能 LLM 推理引擎，吞吐量极高",
        "api_format": "openai",
        "default_port": 8000,
    },
    "jan": {
        "label": "🤖 Jan",
        "default_url": "",
        "env_key": "JAN_URL",
        "description": "Jan — 开源桌面 AI 助手，自带模型管理",
        "api_format": "openai",
        "default_port": 1337,
    },
    "gpt4all": {
        "label": "🌍 GPT4All",
        "default_url": "",
        "env_key": "GPT4ALL_URL",
        "description": "GPT4All — 跨平台私有 AI 聊天工具",
        "api_format": "openai",
        "default_port": 4891,
    },
    "custom_openai": {
        "label": "🔌 自定义 OpenAI 兼容",
        "default_url": "",
        "env_key": "CUSTOM_OPENAI_URL",
        "description": "任何兼容 OpenAI API 的推理后端（填入地址即可）",
        "api_format": "openai",
        "default_port": 0,
    },
    "local_clip": {
        "label": "🖼️ 本地 CLIP",
        "default_url": "local://clip-worker",
        "env_key": "",
        "description": "本地 CLIP 视觉向量模型（内建 Worker，仅 embed 能力）",
        "api_format": "internal",
        "default_port": 0,
    },
}


# =========================================================================
# 抽象基类
# =========================================================================


class BaseModelProvider(ABC):
    """
    AI 模型提供者抽象基类。
    所有后端必须实现此接口，网关层通过此接口统一调度。
    """

    provider_type: str = "unknown"
    base_url: str = ""

    def set_url(self, url: str) -> None:
        """动态设置端点地址（指挥官在设置页修改后调用）"""
        self.base_url = url.rstrip("/")

    @abstractmethod
    async def list_models(self) -> list[dict[str, object]]: ...

    @abstractmethod
    async def health(self) -> dict[str, object]: ...

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        return {"error": f"{self.provider_type} 不支持对话能力", "code": 501}

    async def embed(self, model: str, text: str, **kwargs: object) -> dict[str, object]:
        return {"error": f"{self.provider_type} 不支持向量化能力", "code": 501}


# =========================================================================
# OpenAI-Compatible Provider (通用基类)
# LM Studio / LocalAI / text-gen-webui / vLLM / Jan / GPT4All 共用
# =========================================================================


class OpenAICompatibleProvider(BaseModelProvider):
    """
    通用 OpenAI-compatible 后端。
    支持 /v1/models 发现 + /v1/chat/completions 对话。
    """

    def __init__(self, provider_type: str, base_url: str = "") -> None:
        self.provider_type = provider_type
        defaults = PROVIDER_DEFAULTS.get(provider_type, {})
        self.base_url = base_url or os.getenv(defaults.get("env_key", ""), defaults.get("default_url", ""))  # type: ignore[call-overload]

    async def list_models(self) -> list[dict[str, object]]:
        if not self.base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                if resp.status_code != 200:
                    return []
                data = resp.json()
                return [
                    {
                        "id": m.get("id", ""),
                        "name": m.get("id", ""),
                        "provider": self.provider_type,
                        "capabilities": ["chat"],
                        "auto_discovered": True,
                    }
                    for m in data.get("data", [])
                ]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.debug("[%s] 模型发现失败: %s", self.provider_type, e)
            return []

    async def health(self) -> dict[str, object]:
        if not self.base_url:
            return {"status": "not_configured"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # 尝试 /v1/models (标准) → /health → /
                for path in ["/v1/models", "/health", "/"]:
                    try:
                        resp = await client.get(f"{self.base_url}{path}")
                        if resp.status_code == 200:
                            return {"status": "online", "url": self.base_url}
                    except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
                        logger.debug(
                            "[%s] health probe failed on %s: %s",
                            self.provider_type,
                            path,
                            e,
                        )
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.debug("[%s] health probe error: %s", self.provider_type, e)
        return {"status": "offline", "url": self.base_url}

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        if not self.base_url:
            return {"error": "Provider 未配置", "code": 503}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={"model": model, "messages": messages, **kwargs},
                )
                return resp.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            return {"error": str(e), "code": 502}


# =========================================================================
# Ollama Provider (专有 API 格式)
# =========================================================================


class OllamaProvider(BaseModelProvider):
    """Ollama — 专有 /api/ 端点 + 自动发现已拉取模型。"""

    provider_type = "ollama"

    def __init__(self, base_url: str = "") -> None:
        defaults = PROVIDER_DEFAULTS["ollama"]
        self.base_url = base_url or os.getenv(defaults["env_key"], defaults["default_url"])  # type: ignore[call-overload]

    async def list_models(self) -> list[dict[str, object]]:
        if not self.base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return []
                data = resp.json()
                models = []
                for m in data.get("models", []):
                    name = m.get("name", "")
                    size_bytes = m.get("size", 0)
                    size_gb = round(size_bytes / (1024**3), 1) if size_bytes else 0

                    capabilities = ["chat"]
                    name_lower = name.lower()
                    if any(k in name_lower for k in ("embed", "nomic", "bge", "mxbai")):
                        capabilities = ["embed"]
                    if any(k in name_lower for k in ("llava", "bakllava", "moondream", "vision")):
                        capabilities.append("vision")
                    if any(
                        k in name_lower
                        for k in (
                            "code",
                            "codellama",
                            "deepseek-coder",
                            "starcoder",
                            "qwen2.5-coder",
                        )
                    ):
                        capabilities.append("code")

                    models.append(
                        {
                            "id": name,
                            "name": name,
                            "provider": "ollama",
                            "size_gb": size_gb,
                            "capabilities": capabilities,
                            "auto_discovered": True,
                            "details": m.get("details", {}),
                        }
                    )
                return models
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.debug("Ollama 模型发现失败: %s", e)
            return []

    async def health(self) -> dict[str, object]:
        if not self.base_url:
            return {"status": "not_configured"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/version")
                if resp.status_code == 200:
                    return {
                        "status": "online",
                        "version": resp.json().get("version", "?"),
                        "url": self.base_url,
                    }
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            logger.debug("Ollama health check failed: %s", e)
        return {"status": "offline", "url": self.base_url}

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        **kwargs,
                    },
                )
                return resp.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            return {"error": str(e), "code": 502}

    async def embed(self, model: str, text: str, **kwargs: object) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                return resp.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
            return {"error": str(e), "code": 502}


# =========================================================================
# Local CLIP Provider (内建 Worker)
# =========================================================================


class LocalCLIPProvider(BaseModelProvider):
    """本地 CLIP 视觉模型 Provider（仅 embed 能力）。"""

    provider_type = "local_clip"

    async def list_models(self) -> list[dict[str, object]]:
        return [
            {
                "id": "clip-vit-base-patch32",
                "name": "clip-vit-base-patch32",
                "provider": "local_clip",
                "capabilities": ["embed"],
                "auto_discovered": True,
            }
        ]

    async def health(self) -> dict[str, object]:
        return {"status": "available", "note": "本地 CLIP Worker 按需启动"}


# =========================================================================
# Provider 注册表 — 全域单例
# =========================================================================


class ModelProviderRegistry:
    """
    模型提供者注册表。
    网关启动时自动注册所有内建 Provider，指挥官可在设置页修改端点、启停 Provider。
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseModelProvider] = {}

    def register(self, provider: BaseModelProvider) -> None:
        self._providers[provider.provider_type] = provider
        logger.info("注册 AI Provider: %s", provider.provider_type)

    def update_url(self, provider_type: str, url: str) -> bool:
        """指挥官修改端点地址后，热更新 Provider（无需重启）。"""
        provider = self._providers.get(provider_type)
        if provider:
            provider.set_url(url)
            logger.info("Provider [%s] 端点更新为: %s", provider_type, url)
            return True
        return False

    async def discover_all_models(self) -> list[dict[str, object]]:
        all_models: list[dict[str, object]] = []
        for ptype, provider in self._providers.items():
            try:
                models = await provider.list_models()
                all_models.extend(models)
            except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as e:
                logger.warning("Provider [%s] 扫描异常: %s", ptype, e)
        return all_models

    async def health_all(self) -> dict[str, object]:
        statuses: dict[str, object] = {}
        for ptype, provider in self._providers.items():
            try:
                statuses[ptype] = await provider.health()
            except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                logging.getLogger(__name__).debug("Provider health check failed for %s: %s", ptype, exc)
                statuses[ptype] = {"status": "error"}
        return statuses

    def get_provider(self, provider_type: str) -> BaseModelProvider | None:
        return self._providers.get(provider_type)

    def get_all_endpoints(self) -> dict[str, dict[str, object]]:
        """获取所有 Provider 的端点配置（用于前端展示）。"""
        endpoints: dict[str, dict[str, object]] = {}
        for ptype, provider in self._providers.items():
            defaults = PROVIDER_DEFAULTS.get(ptype, {})
            endpoints[ptype] = {
                "label": defaults.get("label", ptype),
                "url": getattr(provider, "base_url", ""),
                "default_url": defaults.get("default_url", ""),
                "default_port": defaults.get("default_port", 0),
                "description": defaults.get("description", ""),
                "api_format": defaults.get("api_format", "unknown"),
            }
        return endpoints

    @property
    def providers(self) -> dict[str, BaseModelProvider]:
        return self._providers


# =========================================================================
# 全局单例 — 懒初始化
# =========================================================================

_registry: ModelProviderRegistry | None = None


def get_model_registry() -> ModelProviderRegistry:
    """获取全局 Provider 注册表（懒初始化）。"""
    global _registry
    if _registry is None:
        _registry = ModelProviderRegistry()
        # Ollama (专有 API)
        _registry.register(OllamaProvider())
        # OpenAI-compatible 后端们
        _registry.register(OpenAICompatibleProvider("lm_studio"))
        _registry.register(OpenAICompatibleProvider("localai"))
        _registry.register(OpenAICompatibleProvider("text_gen_webui"))
        _registry.register(OpenAICompatibleProvider("vllm"))
        _registry.register(OpenAICompatibleProvider("jan"))
        _registry.register(OpenAICompatibleProvider("gpt4all"))
        _registry.register(OpenAICompatibleProvider("custom_openai"))
        # 本地 CLIP
        _registry.register(LocalCLIPProvider())
        logger.info("AI Provider Registry 初始化: %s", list(_registry.providers.keys()))
    return _registry
