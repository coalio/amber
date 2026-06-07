from __future__ import annotations

from src.providers.base import ModelProvider, ProviderSelectionConfig
from src.providers.openai.provider import OpenAIProvider


class ModelProviderGateway:
    def __init__(self, config: ProviderSelectionConfig) -> None:
        self._provider = self._build_provider(config)

    def _build_provider(self, config: ProviderSelectionConfig) -> ModelProvider:
        provider_name = config.provider_name.lower()
        if provider_name == "openai":
            return OpenAIProvider(config.api_key)
        raise RuntimeError(f"Unsupported model provider: {config.provider_name}")

    @property
    def provider(self) -> ModelProvider:
        return self._provider
