"""プロバイダーアダプターの基底クラス。

新しいプロバイダーを足すときは、このクラスを継承して3つのメソッドを実装するだけでよい:

- ``build_request``: 共通の ChatRequest → プロバイダー固有の HTTP リクエスト
- ``parse_response``: プロバイダー固有のレスポンス JSON → 共通の Response
- ``parse_stream``: SSE イベント列 → 共通の StreamEvent 列（最後に done）
"""

from __future__ import annotations

import copy
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

from .._http import HTTPRequest
from ..errors import ConfigurationError
from ..tools import Tool
from ..types import Message, Response, StreamEvent

ToolChoice = Any  # None | "auto" | "none" | "required" | {"name": str}


@dataclass
class ChatRequest:
    messages: list[Message]
    tools: list[Tool] = field(default_factory=list)
    tool_choice: ToolChoice = None
    max_tokens: int | None = None
    temperature: float | None = None
    stop: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """``extra`` を ``base`` に再帰的にマージする（プロバイダー固有パラメータの逃げ道）。"""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize_tool_choice(choice: ToolChoice) -> tuple[str, str | None]:
    """→ ("auto" | "none" | "required" | "tool", ツール名)"""
    if choice is None or choice == "auto":
        return "auto", None
    if choice in ("none", "required"):
        return choice, None
    if isinstance(choice, Tool):
        return "tool", choice.name
    if isinstance(choice, dict) and "name" in choice:
        return "tool", choice["name"]
    if isinstance(choice, str):
        return "tool", choice
    raise ConfigurationError(f"invalid tool_choice: {choice!r}")


class Provider(ABC):
    #: アダプター種別（"openai" / "openai_chat" / "anthropic" / "gemini"）
    adapter: str = ""
    default_base_url: str = ""
    api_key_env: tuple[str, ...] = ()
    requires_api_key: bool = True

    def __init__(
        self,
        key: str,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        api_key_env: tuple[str, ...] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.key = key  # モデル文字列の "provider:" 部分（例: "groq"）
        self.model = model
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.options = dict(options or {})
        envs = self.api_key_env if api_key_env is None else api_key_env
        self.api_key = api_key or next((os.environ[e] for e in envs if os.environ.get(e)), None)
        if self.requires_api_key and envs and not self.api_key:
            raise ConfigurationError(
                f"API key for {key!r} not found. Set {' or '.join(envs)} or pass api_key=..."
            )

    # ---- 共通ヘルパー ---- #
    def native_raw(self, msg: Message) -> Any | None:
        """同じプロバイダー・同じモデルが生成した assistant メッセージなら、生データを返す。"""
        st = msg.provider_state
        if st is not None and st.provider == self.key and st.model == self.model:
            return copy.deepcopy(st.raw)
        return None

    @staticmethod
    def system_text(messages: list[Message]) -> str:
        return "\n\n".join(m.text for m in messages if m.role == "system" and m.text)

    # ---- 実装必須 ---- #
    @abstractmethod
    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest: ...

    @abstractmethod
    def parse_response(self, data: dict[str, Any]) -> Response: ...

    @abstractmethod
    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]: ...
