"""プロバイダーの登録表（内部実装。公開 API は ``switchllm`` 直下の ``__all__`` だけ）。

モデル文字列 ``"<provider>:<model>"`` の ``<provider>`` 部分をここで引いて、
どのアダプター（リクエスト形式）・どの URL・どの環境変数を使うかを決める。

プリセットは「この名前で動く」という約束なので、base_url を公式ドキュメントで確認したものだけを置く
（docs/design-docs/verified-presets.md）。それ以外は利用者が ``register_provider()`` で足す。
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
    # OpenAI Chat Completions 形式（base_url= を渡せば任意の互換サーバーにも使える）
    "openai-chat": ProviderPreset("openai_chat", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    # クラウドの互換 API。base_url は docs/references/openai-chat-compatible.md の出典で確認済み。
    # DeepSeek の response_format は json_object（JSON モード）だけで、JSON Schema を受け付けない
    "deepseek": ProviderPreset("openai_chat", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",), {"json_schema": False}),
    # ローカル実行（APIキー不要）。base_url は docs/references/openai-chat-compatible.md の出典で確認済み
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
    """OpenAI 互換のクラウド API、自前の vLLM サーバー、社内ゲートウェイなどを名前付きで登録する。

    >>> register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
    >>> register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())
    >>> Client("myvllm:Qwen/Qwen3-8B")
    """
    # モデル文字列の provider 部分は parse_model() が小文字にしてから引くので、登録する名前もそろえる
    key = name.strip().lower()
    if not key or ":" in key:
        raise ConfigurationError(f"provider name must be non-empty and must not contain ':', got {name!r}")
    if adapter not in ADAPTERS:
        raise ConfigurationError(f"unknown adapter {adapter!r}; available: {sorted(ADAPTERS)}")
    ADAPTERS[adapter].check_options(key, options)
    if isinstance(api_key_env, str):
        api_key_env = (api_key_env,)
    PRESETS[key] = ProviderPreset(adapter, base_url, api_key_env, options)


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
    "register_provider",
]
