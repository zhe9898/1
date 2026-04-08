"""AI provider abstractions for local and OpenAI-compatible model backends."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger("zen70.ai_providers")

PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "ollama": {
        "label": "Ollama",
        "default_url": "",
        "env_key": "OLLAMA_URL",
        "description": "Local Ollama runtime",
        "api_format": "ollama",
        "default_port": 11434,
    },
    "lm_studio": {
        "label": "LM Studio",
        "default_url": "",
        "env_key": "LM_STUDIO_URL",
        "description": "OpenAI-compatible LM Studio server",
        "api_format": "openai",
        "default_port": 1234,
    },
    "localai": {
        "label": "LocalAI",
        "default_url": "",
        "env_key": "LOCALAI_URL",
        "description": "OpenAI-compatible LocalAI server",
        "api_format": "openai",
        "default_port": 8080,
    },
    "text_gen_webui": {
        "label": "text-generation-webui",
        "default_url": "",
        "env_key": "TEXT_GEN_WEBUI_URL",
        "description": "OpenAI-compatible text-generation-webui server",
        "api_format": "openai",
        "default_port": 5000,
    },
    "vllm": {
        "label": "vLLM",
        "default_url": "",
        "env_key": "VLLM_URL",
        "description": "OpenAI-compatible vLLM server",
        "api_format": "openai",
        "default_port": 8000,
    },
    "jan": {
        "label": "Jan",
        "default_url": "",
        "env_key": "JAN_URL",
        "description": "OpenAI-compatible Jan server",
        "api_format": "openai",
        "default_port": 1337,
    },
    "gpt4all": {
        "label": "GPT4All",
        "default_url": "",
        "env_key": "GPT4ALL_URL",
        "description": "OpenAI-compatible GPT4All server",
        "api_format": "openai",
        "default_port": 4891,
    },
    "custom_openai": {
        "label": "Custom OpenAI Compatible",
        "default_url": "",
        "env_key": "CUSTOM_OPENAI_URL",
        "description": "Any OpenAI-compatible endpoint",
        "api_format": "openai",
        "default_port": 0,
    },
    "local_clip": {
        "label": "Local CLIP",
        "default_url": "local://clip-worker",
        "env_key": "",
        "description": "Built-in local CLIP embedding worker",
        "api_format": "internal",
        "default_port": 0,
    },
}


class BaseModelProvider(ABC):
    provider_type: str = "unknown"
    base_url: str = ""

    def set_url(self, url: str) -> None:
        self.base_url = url.rstrip("/")

    @abstractmethod
    async def list_models(self) -> list[dict[str, object]]:
        ...

    @abstractmethod
    async def health(self) -> dict[str, object]:
        ...

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        del model, messages, kwargs
        return {"error": f"{self.provider_type} does not support chat", "code": 501}

    async def embed(self, model: str, text: str, **kwargs: object) -> dict[str, object]:
        del model, text, kwargs
        return {"error": f"{self.provider_type} does not support embeddings", "code": 501}


class OpenAICompatibleProvider(BaseModelProvider):
    def __init__(self, provider_type: str, base_url: str = "") -> None:
        self.provider_type = provider_type
        defaults = PROVIDER_DEFAULTS.get(provider_type, {})
        env_key = str(defaults.get("env_key", ""))
        default_url = str(defaults.get("default_url", ""))
        self.base_url = base_url or os.getenv(env_key, default_url)

    async def list_models(self) -> list[dict[str, object]]:
        if not self.base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/v1/models")
                if response.status_code != 200:
                    return []
                payload = response.json()
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("[%s] model discovery failed: %s", self.provider_type, exc)
            return []

        return [
            {
                "id": model.get("id", ""),
                "name": model.get("id", ""),
                "provider": self.provider_type,
                "capabilities": ["chat"],
                "auto_discovered": True,
            }
            for model in payload.get("data", [])
        ]

    async def health(self) -> dict[str, object]:
        if not self.base_url:
            return {"status": "not_configured"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                for path in ("/v1/models", "/health", "/"):
                    response = await client.get(f"{self.base_url}{path}")
                    if response.status_code == 200:
                        return {"status": "online", "url": self.base_url}
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("[%s] health probe failed: %s", self.provider_type, exc)
        return {"status": "offline", "url": self.base_url}

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        if not self.base_url:
            return {"error": "Provider not configured", "code": 503}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={"model": model, "messages": messages, **kwargs},
                )
                return response.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            return {"error": str(exc), "code": 502}


class OllamaProvider(BaseModelProvider):
    provider_type = "ollama"

    def __init__(self, base_url: str = "") -> None:
        defaults = PROVIDER_DEFAULTS["ollama"]
        self.base_url = base_url or os.getenv(str(defaults["env_key"]), str(defaults["default_url"]))

    async def list_models(self) -> list[dict[str, object]]:
        if not self.base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code != 200:
                    return []
                payload = response.json()
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("ollama model discovery failed: %s", exc)
            return []

        models: list[dict[str, object]] = []
        for model in payload.get("models", []):
            name = str(model.get("name", ""))
            capabilities = ["chat"]
            lower_name = name.lower()
            if any(token in lower_name for token in ("embed", "nomic", "bge", "mxbai")):
                capabilities = ["embed"]
            if any(token in lower_name for token in ("llava", "bakllava", "moondream", "vision")):
                capabilities.append("vision")
            if any(token in lower_name for token in ("code", "codellama", "deepseek-coder", "starcoder", "qwen2.5-coder")):
                capabilities.append("code")
            models.append(
                {
                    "id": name,
                    "name": name,
                    "provider": "ollama",
                    "capabilities": capabilities,
                    "auto_discovered": True,
                    "details": model.get("details", {}),
                }
            )
        return models

    async def health(self) -> dict[str, object]:
        if not self.base_url:
            return {"status": "not_configured"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/api/version")
                if response.status_code == 200:
                    return {
                        "status": "online",
                        "version": response.json().get("version", "?"),
                        "url": self.base_url,
                    }
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("ollama health probe failed: %s", exc)
        return {"status": "offline", "url": self.base_url}

    async def chat(self, model: str, messages: list, **kwargs: object) -> dict[str, object]:
        if not self.base_url:
            return {"error": "Provider not configured", "code": 503}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={"model": model, "messages": messages, "stream": False, **kwargs},
                )
                return response.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            return {"error": str(exc), "code": 502}

    async def embed(self, model: str, text: str, **kwargs: object) -> dict[str, object]:
        del kwargs
        if not self.base_url:
            return {"error": "Provider not configured", "code": 503}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                return response.json()  # type: ignore[no-any-return]
        except (httpx.RequestError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            return {"error": str(exc), "code": 502}


class LocalCLIPProvider(BaseModelProvider):
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
        return {"status": "available", "note": "local worker"}


class ModelProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, BaseModelProvider] = {}

    def register(self, provider: BaseModelProvider) -> None:
        self._providers[provider.provider_type] = provider

    def update_url(self, provider_type: str, url: str) -> bool:
        provider = self._providers.get(provider_type)
        if provider is None:
            return False
        provider.set_url(url)
        return True

    async def discover_all_models(self) -> list[dict[str, object]]:
        all_models: list[dict[str, object]] = []
        for provider in self._providers.values():
            all_models.extend(await provider.list_models())
        return all_models

    async def health_all(self) -> dict[str, object]:
        statuses: dict[str, object] = {}
        for provider_type, provider in self._providers.items():
            statuses[provider_type] = await provider.health()
        return statuses

    def get_provider(self, provider_type: str) -> BaseModelProvider | None:
        return self._providers.get(provider_type)

    def get_all_endpoints(self) -> dict[str, dict[str, object]]:
        endpoints: dict[str, dict[str, object]] = {}
        for provider_type, provider in self._providers.items():
            defaults = PROVIDER_DEFAULTS.get(provider_type, {})
            endpoints[provider_type] = {
                "label": defaults.get("label", provider_type),
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


_registry: ModelProviderRegistry | None = None


def get_model_registry() -> ModelProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ModelProviderRegistry()
        _registry.register(OllamaProvider())
        _registry.register(OpenAICompatibleProvider("lm_studio"))
        _registry.register(OpenAICompatibleProvider("localai"))
        _registry.register(OpenAICompatibleProvider("text_gen_webui"))
        _registry.register(OpenAICompatibleProvider("vllm"))
        _registry.register(OpenAICompatibleProvider("jan"))
        _registry.register(OpenAICompatibleProvider("gpt4all"))
        _registry.register(OpenAICompatibleProvider("custom_openai"))
        _registry.register(LocalCLIPProvider())
    return _registry


__all__ = [
    "BaseModelProvider",
    "LocalCLIPProvider",
    "ModelProviderRegistry",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "PROVIDER_DEFAULTS",
    "get_model_registry",
]
