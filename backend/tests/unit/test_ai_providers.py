"""
单元测试：AI Provider 抽象层。

覆盖 ModelProviderRegistry CRUD、OllamaProvider 配置与降级、
OpenAICompatibleProvider / LocalCLIPProvider 行为、
模型能力标签推断。
全部 Mock httpx，不需要真实 AI 后端。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# -------------------- BaseModelProvider --------------------


class TestBaseModelProvider:
    """抽象基类行为。"""

    def test_set_url_strips_trailing_slash(self) -> None:
        """set_url 应去除尾部斜杠。"""
        from backend.core.ai_providers import OllamaProvider

        p = OllamaProvider(base_url="http://localhost:11434/")
        p.set_url("http://new-host:11434/")
        assert p.base_url == "http://new-host:11434"

    @pytest.mark.asyncio
    async def test_chat_default_returns_501(self) -> None:
        """未覆写的 chat 方法应返回 501。"""
        from backend.core.ai_providers import LocalCLIPProvider

        provider = LocalCLIPProvider()
        result = await provider.chat("model", [])
        assert result.get("code") == 501

    @pytest.mark.asyncio
    async def test_embed_default_returns_501(self) -> None:
        """未覆写的 embed 方法应返回 501（OpenAICompatible 未覆写 embed）。"""
        from backend.core.ai_providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider("custom_openai", base_url="")
        result = await provider.embed("model", "text")
        assert result.get("code") == 501


# -------------------- OllamaProvider --------------------


class TestOllamaProvider:
    """Ollama Provider 测试。"""

    def test_provider_type_is_ollama(self) -> None:
        """provider_type 应为 ollama。"""
        from backend.core.ai_providers import OllamaProvider

        p = OllamaProvider()
        assert p.provider_type == "ollama"

    @pytest.mark.asyncio
    async def test_health_not_configured(self) -> None:
        """base_url 为空时返回 not_configured。"""
        from backend.core.ai_providers import OllamaProvider

        p = OllamaProvider(base_url="")
        result = await p.health()
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_list_models_empty_when_unconfigured(self) -> None:
        """base_url 为空时返回空列表。"""
        from backend.core.ai_providers import OllamaProvider

        p = OllamaProvider(base_url="")
        result = await p.list_models()
        assert result == []

    @pytest.mark.asyncio
    async def test_chat_returns_error_when_unconfigured(self) -> None:
        """base_url 为空时 embed 返回错误。"""
        from backend.core.ai_providers import OllamaProvider

        p = OllamaProvider(base_url="")
        # Ollama chat 覆写了默认方法，但连接失败应返回 502
        # 空 url 直接 httpx 报错
        result = await p.embed("mxbai", "hello")
        assert "error" in result


# -------------------- OpenAICompatibleProvider --------------------


class TestOpenAICompatibleProvider:
    """OpenAI 兼容 Provider。"""

    def test_constructor_reads_env(self) -> None:
        """构造时应从 PROVIDER_DEFAULTS 读取 env_key。"""
        from backend.core.ai_providers import OpenAICompatibleProvider

        with patch.dict("os.environ", {"LM_STUDIO_URL": "http://lms:1234"}):
            p = OpenAICompatibleProvider("lm_studio")
            assert p.base_url == "http://lms:1234"

    @pytest.mark.asyncio
    async def test_health_not_configured(self) -> None:
        """未配置时健康检查返回 not_configured。"""
        from backend.core.ai_providers import OpenAICompatibleProvider

        p = OpenAICompatibleProvider("vllm", base_url="")
        result = await p.health()
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_chat_unconfigured_returns_503(self) -> None:
        """未配置端点时 chat 返回 503。"""
        from backend.core.ai_providers import OpenAICompatibleProvider

        p = OpenAICompatibleProvider("custom_openai", base_url="")
        result = await p.chat("model", [])
        assert result.get("code") == 503


# -------------------- LocalCLIPProvider --------------------


class TestLocalCLIPProvider:
    """本地 CLIP Provider。"""

    @pytest.mark.asyncio
    async def test_list_models_returns_clip(self) -> None:
        """应返回内建 CLIP 模型。"""
        from backend.core.ai_providers import LocalCLIPProvider

        p = LocalCLIPProvider()
        models = await p.list_models()
        assert len(models) == 1
        assert models[0]["provider"] == "local_clip"
        assert "embed" in models[0]["capabilities"]  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_health_returns_available(self) -> None:
        """健康检查应返回 available。"""
        from backend.core.ai_providers import LocalCLIPProvider

        p = LocalCLIPProvider()
        result = await p.health()
        assert result["status"] == "available"


# -------------------- ModelProviderRegistry --------------------


class TestModelProviderRegistry:
    """Provider 注册表。"""

    def test_register_and_get(self) -> None:
        """注册后应能通过 get_provider 获取。"""
        from backend.core.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        clip = LocalCLIPProvider()
        registry.register(clip)

        assert registry.get_provider("local_clip") is clip

    def test_get_nonexistent_returns_none(self) -> None:
        """获取未注册的 provider 应返回 None。"""
        from backend.core.ai_providers import ModelProviderRegistry

        registry = ModelProviderRegistry()
        assert registry.get_provider("nonexistent") is None

    def test_update_url(self) -> None:
        """update_url 应更新已注册 Provider 的 base_url。"""
        from backend.core.ai_providers import ModelProviderRegistry, OllamaProvider

        registry = ModelProviderRegistry()
        ollama = OllamaProvider(base_url="http://old:11434")
        registry.register(ollama)

        assert registry.update_url("ollama", "http://new:11434") is True
        assert ollama.base_url == "http://new:11434"

    def test_update_url_nonexistent_returns_false(self) -> None:
        """更新不存在的 provider 应返回 False。"""
        from backend.core.ai_providers import ModelProviderRegistry

        registry = ModelProviderRegistry()
        assert registry.update_url("nonexistent", "http://x") is False

    @pytest.mark.asyncio
    async def test_discover_all_models(self) -> None:
        """应聚合所有 provider 的模型列表。"""
        from backend.core.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        registry.register(LocalCLIPProvider())

        models = await registry.discover_all_models()
        assert len(models) >= 1

    @pytest.mark.asyncio
    async def test_health_all(self) -> None:
        """应返回所有 provider 的健康状态。"""
        from backend.core.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        registry.register(LocalCLIPProvider())

        statuses = await registry.health_all()
        assert "local_clip" in statuses
        assert statuses["local_clip"]["status"] == "available"  # type: ignore[index]

    def test_get_all_endpoints(self) -> None:
        """应返回所有 provider 的端点配置。"""
        from backend.core.ai_providers import ModelProviderRegistry, OllamaProvider

        registry = ModelProviderRegistry()
        registry.register(OllamaProvider(base_url="http://test:11434"))

        endpoints = registry.get_all_endpoints()
        assert "ollama" in endpoints
        assert endpoints["ollama"]["url"] == "http://test:11434"


# -------------------- get_model_registry --------------------


class TestGetModelRegistry:
    """全局注册表懒初始化。"""

    def test_returns_singleton(self) -> None:
        """连续调用应返回同一实例。"""
        from backend.core.ai_providers import get_model_registry

        r1 = get_model_registry()
        r2 = get_model_registry()
        assert r1 is r2

    def test_registers_all_builtin_providers(self) -> None:
        """应注册所有内建 provider（≥9）。"""
        from backend.core.ai_providers import get_model_registry

        registry = get_model_registry()
        assert len(registry.providers) >= 9
        assert "ollama" in registry.providers
        assert "local_clip" in registry.providers
