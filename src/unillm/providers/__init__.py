"""プロバイダーの登録表。

モデル文字列 ``"<provider>:<model>"`` の ``<provider>`` 部分をここで引いて、
どのアダプター（リクエスト形式）・どの URL・どの環境変数を使うかを決める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ConfigurationError
from .anthropic import AnthropicProvider
from .base import ChatRequest, Provider
from .gemini import GeminiProvider
from .openai_chat import OpenAIChatProvider
from .openai_responses import OpenAIResponsesProvider

ADAPTERS: dict[str, type[Provider]] = {
    "openai": OpenAIResponsesProvider,
    "openai_chat": OpenAIChatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


@dataclass
class ProviderPreset:
    adapter: str
    base_url: str | None = None
    api_key_env: tuple[str, ...] | None = None  # None ならアダプターの既定値、() なら APIキー不要
    options: dict[str, Any] = field(default_factory=dict)


PRESETS: dict[str, ProviderPreset] = {
    # ネイティブ API
    "openai": ProviderPreset("openai"),
    "anthropic": ProviderPreset("anthropic"),
    "gemini": ProviderPreset("gemini"),
    "google": ProviderPreset("gemini"),
    # OpenAI Chat Completions 互換
    "openai-chat": ProviderPreset("openai_chat", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    "openrouter": ProviderPreset("openai_chat", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",)),
    "groq": ProviderPreset("openai_chat", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",)),
    "deepseek": ProviderPreset("openai_chat", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",)),
    "xai": ProviderPreset("openai_chat", "https://api.x.ai/v1", ("XAI_API_KEY",)),
    "mistral": ProviderPreset("openai_chat", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",)),
    "together": ProviderPreset("openai_chat", "https://api.together.ai/v1", ("TOGETHER_API_KEY",)),
    # ローカル実行（APIキー不要）
    "ollama": ProviderPreset("openai_chat", "http://localhost:11434/v1", ()),
    "lmstudio": ProviderPreset("openai_chat", "http://localhost:1234/v1", ()),
}


def register_provider(
    name: str,
    *,
    adapter: str = "openai_chat",
    base_url: str | None = None,
    api_key_env: tuple[str, ...] | str | None = None,
    **options: Any,
) -> None:
    """自前の vLLM サーバーや社内ゲートウェイなどを名前付きで登録する。

    >>> register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())
    >>> Client("myvllm:Qwen/Qwen3-8B")
    """
    if adapter not in ADAPTERS:
        raise ConfigurationError(f"unknown adapter {adapter!r}; available: {sorted(ADAPTERS)}")
    if isinstance(api_key_env, str):
        api_key_env = (api_key_env,)
    PRESETS[name] = ProviderPreset(adapter, base_url, api_key_env, options)


def register_adapter(name: str, cls: type[Provider]) -> None:
    """まったく新しいリクエスト形式を話すプロバイダー用のアダプタークラスを登録する。"""
    ADAPTERS[name] = cls
    PRESETS.setdefault(name, ProviderPreset(name))


def create_provider(
    key: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    options: dict[str, Any] | None = None,
) -> Provider:
    preset = PRESETS.get(key)
    if preset is None:
        raise ConfigurationError(
            f"unknown provider {key!r}. Known: {', '.join(sorted(PRESETS))}. Use register_provider() to add one."
        )
    cls = ADAPTERS[preset.adapter]
    return cls(
        key,
        model,
        api_key=api_key,
        base_url=base_url or preset.base_url,
        api_key_env=preset.api_key_env,
        options={**preset.options, **(options or {})},
    )


__all__ = [
    "ADAPTERS",
    "PRESETS",
    "ChatRequest",
    "Provider",
    "ProviderPreset",
    "create_provider",
    "register_adapter",
    "register_provider",
]
