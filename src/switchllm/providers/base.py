"""プロバイダーアダプターの基底クラス。

新しいプロバイダーを足すときは、このクラスを継承して3つのメソッドを実装するだけでよい:

- ``build_request``: 共通の ChatRequest → プロバイダー固有の HTTP リクエスト
- ``parse_response``: プロバイダー固有のレスポンス JSON → 共通の Response
- ``parse_stream``: SSE イベント列 → 共通の StreamEvent 列（最後に done）
"""

from __future__ import annotations

import copy
import os
import re
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
    #: 構造化出力の JSON Schema。``Client`` が ``strict_object_schema()`` を通したもの。None なら指定なし
    response_schema: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# 構造化出力のスキーマで、中にスキーマを持つキーワード（docs/design-docs/structured-output.md）
_SCHEMA_MAPS = ("properties", "$defs", "definitions")
_SCHEMA_LISTS = ("anyOf", "allOf", "oneOf", "prefixItems")
# OpenAI の json_schema の name の条件（docs/references/openai-chat-compatible.md）
_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def strict_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """プロパティを並べたオブジェクトに ``additionalProperties: false`` を足した写しを返す。

    OpenAI（strict）と Anthropic が必須とし、Gemini も受け付ける。プロパティのないオブジェクト
    （キーを決めない dict）と、``additionalProperties`` を書いてあるものは変えない。足すと意味が変わるため。"""
    out = copy.deepcopy(schema)
    _close_objects(out)
    return out


def _close_objects(node: Any) -> None:
    if not isinstance(node, dict):
        return
    if isinstance(node.get("properties"), dict) and "additionalProperties" not in node:
        node["additionalProperties"] = False
    for key in _SCHEMA_MAPS:
        sub = node.get(key)
        if isinstance(sub, dict):
            for child in sub.values():
                _close_objects(child)
    for key in _SCHEMA_LISTS:
        sub = node.get(key)
        if isinstance(sub, list):
            for child in sub:
                _close_objects(child)
    items = node.get("items")
    for child in items if isinstance(items, list) else [items]:  # list は古い書き方のタプル
        _close_objects(child)


def schema_name(schema: dict[str, Any]) -> str:
    """OpenAI が必須とする json_schema の name。``title`` が条件に合えばそれ、合わなければ ``"response"``。"""
    title = schema.get("title")
    return title if isinstance(title, str) and _SCHEMA_NAME.fullmatch(title) else "response"


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
    #: ``Client(..., **options)`` で受け付けるオプション名。読むオプションを足したらここにも足す
    #: （``tests/test_client.py`` がソースと照合する）
    option_names: frozenset[str] = frozenset()
    #: True なら、同じプロバイダーキーの別のモデルが生成した生データも再送する。
    #: プロバイダーが「モデルを替えても推論情報を捨てるな」と文書で求めているときだけ True にする
    #: （docs/design-docs/provider-state-resend.md）
    resend_across_models: bool = False

    @classmethod
    def check_options(cls, key: str, options: dict[str, Any]) -> None:
        """知らないオプション名を ConfigurationError にする。打ち間違いや、
        別のアダプター用のオプションを黙って無視しないため。"""
        unknown = sorted(set(options) - cls.option_names)
        if unknown:
            allowed = ", ".join(sorted(cls.option_names)) or "(none)"
            raise ConfigurationError(
                f"unknown option(s) for {key!r}: {', '.join(unknown)}. Options for this provider: {allowed}"
            )

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
        self.check_options(key, options or {})
        self.key = key  # モデル文字列の "provider:" 部分（例: "ollama"）
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
        """同じプロバイダー・同じモデルが生成した assistant メッセージなら、生データを返す。
        ``resend_across_models`` が True のアダプターでは、モデルが違っても返す。"""
        st = msg.provider_state
        if st is None or st.provider != self.key:
            return None
        if st.model != self.model and not self.resend_across_models:
            return None
        return copy.deepcopy(st.raw)

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
