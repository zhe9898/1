from __future__ import annotations

from unittest.mock import patch

import pytest


class TestBaseModelProvider:
    def test_set_url_strips_trailing_slash(self) -> None:
        from backend.extensions.ai_providers import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434/")
        provider.set_url("http://new-host:11434/")
        assert provider.base_url == "http://new-host:11434"

    @pytest.mark.asyncio
    async def test_chat_default_returns_501(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider

        provider = LocalCLIPProvider()
        result = await provider.chat("model", [])
        assert result.get("code") == 501

    @pytest.mark.asyncio
    async def test_embed_default_returns_501(self) -> None:
        from backend.extensions.ai_providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider("custom_openai", base_url="")
        result = await provider.embed("model", "text")
        assert result.get("code") == 501


class TestOllamaProvider:
    def test_provider_type_is_ollama(self) -> None:
        from backend.extensions.ai_providers import OllamaProvider

        provider = OllamaProvider()
        assert provider.provider_type == "ollama"

    @pytest.mark.asyncio
    async def test_health_not_configured(self) -> None:
        from backend.extensions.ai_providers import OllamaProvider

        provider = OllamaProvider(base_url="")
        result = await provider.health()
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_list_models_empty_when_unconfigured(self) -> None:
        from backend.extensions.ai_providers import OllamaProvider

        provider = OllamaProvider(base_url="")
        result = await provider.list_models()
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_returns_error_when_unconfigured(self) -> None:
        from backend.extensions.ai_providers import OllamaProvider

        provider = OllamaProvider(base_url="")
        result = await provider.embed("mxbai", "hello")
        assert "error" in result


class TestOpenAICompatibleProvider:
    def test_constructor_reads_env(self) -> None:
        from backend.extensions.ai_providers import OpenAICompatibleProvider

        with patch.dict("os.environ", {"LM_STUDIO_URL": "http://lms:1234"}):
            provider = OpenAICompatibleProvider("lm_studio")
            assert provider.base_url == "http://lms:1234"

    @pytest.mark.asyncio
    async def test_health_not_configured(self) -> None:
        from backend.extensions.ai_providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider("vllm", base_url="")
        result = await provider.health()
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_chat_unconfigured_returns_503(self) -> None:
        from backend.extensions.ai_providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider("custom_openai", base_url="")
        result = await provider.chat("model", [])
        assert result.get("code") == 503


class TestLocalCLIPProvider:
    @pytest.mark.asyncio
    async def test_list_models_returns_clip(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider

        provider = LocalCLIPProvider()
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0]["provider"] == "local_clip"
        assert "embed" in models[0]["capabilities"]

    @pytest.mark.asyncio
    async def test_health_returns_available(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider

        provider = LocalCLIPProvider()
        result = await provider.health()
        assert result["status"] == "available"


class TestModelProviderRegistry:
    def test_register_and_get(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        clip = LocalCLIPProvider()
        registry.register(clip)
        assert registry.get_provider("local_clip") is clip

    def test_get_nonexistent_returns_none(self) -> None:
        from backend.extensions.ai_providers import ModelProviderRegistry

        registry = ModelProviderRegistry()
        assert registry.get_provider("missing") is None

    def test_update_url(self) -> None:
        from backend.extensions.ai_providers import ModelProviderRegistry, OllamaProvider

        registry = ModelProviderRegistry()
        ollama = OllamaProvider(base_url="http://old:11434")
        registry.register(ollama)

        assert registry.update_url("ollama", "http://new:11434") is True
        assert ollama.base_url == "http://new:11434"

    def test_update_url_nonexistent_returns_false(self) -> None:
        from backend.extensions.ai_providers import ModelProviderRegistry

        registry = ModelProviderRegistry()
        assert registry.update_url("missing", "http://x") is False

    @pytest.mark.asyncio
    async def test_discover_all_models(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        registry.register(LocalCLIPProvider())

        models = await registry.discover_all_models()
        assert len(models) >= 1

    @pytest.mark.asyncio
    async def test_health_all(self) -> None:
        from backend.extensions.ai_providers import LocalCLIPProvider, ModelProviderRegistry

        registry = ModelProviderRegistry()
        registry.register(LocalCLIPProvider())

        statuses = await registry.health_all()
        assert "local_clip" in statuses
        assert statuses["local_clip"]["status"] == "available"

    def test_get_all_endpoints(self) -> None:
        from backend.extensions.ai_providers import ModelProviderRegistry, OllamaProvider

        registry = ModelProviderRegistry()
        registry.register(OllamaProvider(base_url="http://test:11434"))

        endpoints = registry.get_all_endpoints()
        assert "ollama" in endpoints
        assert endpoints["ollama"]["url"] == "http://test:11434"


class TestGetModelRegistry:
    def test_returns_singleton(self) -> None:
        from backend.extensions.ai_providers import get_model_registry

        first = get_model_registry()
        second = get_model_registry()
        assert first is second

    def test_registers_all_builtin_providers(self) -> None:
        from backend.extensions.ai_providers import get_model_registry

        registry = get_model_registry()
        assert len(registry.providers) >= 9
        assert "ollama" in registry.providers
        assert "local_clip" in registry.providers
