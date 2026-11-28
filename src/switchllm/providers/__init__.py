"""プロバイダーの登録表（内部実装。公開 API は ``switchllm`` 直下の ``__all__`` だけ）。

モデル文字列 ``"<provider>:<model>"`` の ``<provider>`` 部分をここで引いて、
どのアダプター（リクエスト形式）・どの URL・どの環境変数を使うかを決める。

プリセットは「この名前で動く」という約束なので、base_url を公式ドキュメントで確認したものだけを置く
（docs/design-docs/verified-presets.md）。それ以外は利用者が ``register_provider()`` で足す。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

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
    #: base_url を環境変数から組み立てる関数（クラウドの経路。URL にリソース名やリージョンが入る）。
    #: 利用者が base_url= を渡せば呼ばない。内部のもので、register_provider() では渡せない
    resolve_base_url: Callable[[Mapping[str, str]], str] | None = None
    #: キーを入れるヘッダーと方式（None ならアダプターの既定。docs/design-docs/cloud-api-keys.md）
    auth: tuple[str, str] | None = None


# --------------------------------------------------------------------------- #
# クラウドの経路の URL（docs/references/cloud-endpoints.md）
# --------------------------------------------------------------------------- #
# キーは組み立てた URL のホストへ送るので、リソース名とリージョンはホスト名のラベル1つに限る
# （打ち間違いや "." を含む値で、別のホストへキーを送らないため。docs/design-docs/cloud-api-keys.md）
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _env(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def _host_label(value: str, env: str, provider: str) -> str:
    if not _HOST_LABEL.fullmatch(value):
        raise ConfigurationError(
            f"{env}={value!r} can't be used for {provider!r}: it must be a single host name label "
            "(letters, digits and hyphens)"
        )
    return value


def _https_base(value: str, env: str, provider: str, paths: tuple[str, ...]) -> str:
    """https の URL で、パスが ``paths`` のどれかなら、末尾の / を外して返す。キーを平文で送らないため。"""
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment or path not in paths:
        allowed = " or ".join(repr(p or "/") for p in paths)
        raise ConfigurationError(
            f"{env}={value!r} can't be used for {provider!r}: it must be an https URL whose path is {allowed}"
        )
    return f"https://{parts.netloc}{path}"


def _azure_openai_base_url(environ: Mapping[str, str]) -> str:
    """``AZURE_OPENAI_ENDPOINT``（例: ``https://<resource>.openai.azure.com``）に ``/openai/v1`` を足す。"""
    endpoint = _env(environ, "AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise ConfigurationError(
            "Azure OpenAI endpoint not found. Set AZURE_OPENAI_ENDPOINT (https://<resource>.openai.azure.com) "
            "or pass base_url=..."
        )
    base = _https_base(endpoint, "AZURE_OPENAI_ENDPOINT", "azure", ("", "/openai/v1"))
    return base if base.endswith("/openai/v1") else f"{base}/openai/v1"


def _foundry_base_url(environ: Mapping[str, str]) -> str:
    """Anthropic の SDK と同じく ``ANTHROPIC_FOUNDRY_RESOURCE`` か ``ANTHROPIC_FOUNDRY_BASE_URL`` から作る。"""
    resource = _env(environ, "ANTHROPIC_FOUNDRY_RESOURCE")
    base_url = _env(environ, "ANTHROPIC_FOUNDRY_BASE_URL")
    if resource and base_url:
        # SDK の文書が2つを mutually exclusive とし、どちらを優先するか書いていない
        raise ConfigurationError(
            "Both ANTHROPIC_FOUNDRY_RESOURCE and ANTHROPIC_FOUNDRY_BASE_URL are set; set only one of them"
        )
    if base_url:
        path = urlsplit(base_url).path.rstrip("/")
        return _https_base(base_url, "ANTHROPIC_FOUNDRY_BASE_URL", "foundry", (path,))
    if resource:
        resource = _host_label(resource, "ANTHROPIC_FOUNDRY_RESOURCE", "foundry")
        return f"https://{resource}.services.ai.azure.com/anthropic"
    raise ConfigurationError(
        "Microsoft Foundry resource not found. Set ANTHROPIC_FOUNDRY_RESOURCE (or ANTHROPIC_FOUNDRY_BASE_URL) "
        "or pass base_url=..."
    )


def _bedrock_base_url(environ: Mapping[str, str]) -> str:
    """bedrock-mantle の Messages API。リージョンは SDK と同じく ``AWS_REGION``、なければ ``AWS_DEFAULT_REGION``。"""
    for env in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        region = _env(environ, env)
        if region:
            return f"https://bedrock-mantle.{_host_label(region, env, 'bedrock')}.api.aws/anthropic"
    raise ConfigurationError(
        "AWS region not found for 'bedrock'. Set AWS_REGION (or AWS_DEFAULT_REGION) or pass base_url=..."
    )


PRESETS: dict[str, ProviderPreset] = {
    # ネイティブ API
    "openai": ProviderPreset("openai"),
    "anthropic": ProviderPreset("anthropic"),
    "gemini": ProviderPreset("gemini"),
    "google": ProviderPreset("gemini"),
    # OpenAI Chat Completions 形式（base_url= を渡せば任意の互換サーバーにも使える）
    "openai-chat": ProviderPreset("openai_chat", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    # クラウドの互換 API。base_url は docs/references/openai-chat-compatible.md の出典で確認済み。
    # DeepSeek の response_format は json_object（JSON モード）だけで、JSON Schema を受け付けない。
    # 文書（PDF など）は Files API でアップロードした file_id でしか受け付けない（docs/design-docs/file-input.md）
    "deepseek": ProviderPreset(
        "openai_chat", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",), {"json_schema": False, "file_input": False}
    ),
    # ローカル実行（APIキー不要）。base_url は docs/references/openai-chat-compatible.md の出典で確認済み。
    # どちらも文書にファイルの入力の記述がない。Ollama の対応表は tool_choice と画像の URL を未対応とする
    "ollama": ProviderPreset(
        "openai_chat", "http://localhost:11434/v1", (), {"file_input": False, "tool_choice": False, "image_url": False}
    ),
    "lmstudio": ProviderPreset("openai_chat", "http://localhost:1234/v1", (), {"file_input": False}),
    # クラウドの経路（API キーで呼ぶもの）。URL・キーのヘッダー・環境変数の名前は、
    # docs/references/cloud-endpoints.md の出典で確認済み（キーの環境変数は各社の SDK と同じ名前。
    # VERTEX_API_KEY だけは Google の文書に名前がないので作者が決めた。docs/design-docs/cloud-api-keys.md）
    "azure": ProviderPreset(
        "openai", None, ("AZURE_OPENAI_API_KEY",), resolve_base_url=_azure_openai_base_url, auth=("api-key", "")
    ),
    # 推論モデルは max_tokens を受け付けない。Chat Completions は推論の本文を返さない
    "azure-chat": ProviderPreset(
        "openai_chat",
        None,
        ("AZURE_OPENAI_API_KEY",),
        {"max_tokens_field": "max_completion_tokens", "reasoning_summary": False},
        resolve_base_url=_azure_openai_base_url,
        auth=("api-key", ""),
    ),
    "foundry": ProviderPreset("anthropic", None, ("ANTHROPIC_FOUNDRY_API_KEY",), resolve_base_url=_foundry_base_url),
    # bedrock-mantle は URL の画像と文書を受け付けない
    "bedrock": ProviderPreset(
        "anthropic", None, ("AWS_BEARER_TOKEN_BEDROCK",), {"image_url": False}, resolve_base_url=_bedrock_base_url
    ),
    "vertex": ProviderPreset("gemini", "https://aiplatform.googleapis.com/v1/publishers/google", ("VERTEX_API_KEY",)),
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
    if not base_url and preset.resolve_base_url is not None:
        base_url = preset.resolve_base_url(os.environ)
    return cls(
        key,
        model,
        api_key=api_key,
        base_url=base_url or preset.base_url,
        api_key_env=preset.api_key_env,
        options={**preset.options, **(options or {})},
        auth=preset.auth,
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
