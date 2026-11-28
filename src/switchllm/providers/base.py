"""プロバイダーアダプターの基底クラス。

新しいプロバイダーを足すときは、このクラスを継承して3つのメソッドを実装するだけでよい:

- ``build_request``: 共通の ChatRequest → プロバイダー固有の HTTP リクエスト
- ``parse_response``: プロバイダー固有のレスポンス JSON → 共通の Response
- ``stream_parser``: SSE のイベントを1つずつ StreamEvent にする ``StreamParser``（最後に done）

どれも通信をしないので、同期の ``Client`` と非同期の ``AsyncClient`` が同じものを使う。
"""

from __future__ import annotations

import copy
import os
import re
import sys
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

from .._http import HTTPRequest
from ..errors import ConfigurationError, DroppedParameterWarning
from ..tools import Tool
from ..types import Message, Response, StreamEvent

ToolChoice = Any  # None | "auto" | "none" | "required" | {"name": str}

#: ``reasoning_effort=`` で受け付ける推論の強さ。OpenAI の7段階で、各社の段階の名前はどれもこの中にある
#: （docs/design-docs/reasoning-effort.md）。アダプターは名前を変えずに送り、名前のない段階は UnsupportedFeatureError
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: すべてのアダプターで受け付けるオプション（基底クラスで読む）。アダプターごとのものは ``Provider.option_names``
COMMON_OPTIONS = frozenset({"headers"})


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
    #: 推論の強さ。``REASONING_EFFORTS`` のどれか（``Client`` が確かめたもの）。None なら指定なし
    reasoning_effort: str | None = None
    #: 推論の要約を返させるか（docs/design-docs/reasoning-summary.md）。``Client`` が bool であることと、
    #: ``reasoning_effort="none"`` と一緒でないことを確かめたもの
    reasoning_summary: bool = False
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


# switchllm のパッケージのフォルダー（src/switchllm）。警告が指す場所を、この外の最初のフレームにする
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + os.sep


def warn_dropped(message: str) -> None:
    """指定を送らずに捨てたことを ``DroppedParameterWarning`` で知らせる。

    警告は、ライブラリの外で ``chat()`` / ``stream()`` を呼んだ行を指す。呼ばれる深さは同期・非同期・ストリーミングで
    違うので、``stacklevel`` を数えて決める（Python 3.12 の ``skip_file_prefixes`` と同じことを 3.10 でもする）。"""
    frame = sys._getframe(1)  # warn_dropped を呼んだフレーム（stacklevel=2 にあたる）
    level = 2
    while frame is not None and os.path.normcase(frame.f_code.co_filename).startswith(_PACKAGE_DIR):
        frame = frame.f_back
        level += 1
    warnings.warn(message, DroppedParameterWarning, stacklevel=level)


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


class StreamParser(ABC):
    """SSE のイベントを1つずつ受け取って ``StreamEvent`` にする。

    イベントを取り出すのが同期でも非同期でも、解析は同じコードで行うため、引き出す形（イテレーター）ではなく
    渡される形にしている（docs/design-docs/async-client.md）。``Provider.parse_stream`` /
    ``aparse_stream`` が、``feed()`` を繰り返してから ``finish()`` を1回呼ぶ。"""

    #: 終わりの合図（``[DONE]``、``message_stop`` など）を受け取ったら True にする。残りのイベントは読まない
    closed: bool = False

    @abstractmethod
    def feed(self, event: str, data: str) -> list[StreamEvent]:
        """イベント1つを読み、そこから作った StreamEvent を返す（なければ空）。"""

    @abstractmethod
    def finish(self) -> list[StreamEvent]:
        """ストリームが終わったら呼ぶ。まだ返していなければ最後の ``done`` を返す。
        終わりの合図が届かずに切れていたら ``StreamError``。``done`` は全体で1回だけ返す。"""


class Provider(ABC):
    #: アダプター種別（"openai" / "openai_chat" / "anthropic" / "gemini"）
    adapter: str = ""
    default_base_url: str = ""
    api_key_env: tuple[str, ...] = ()
    requires_api_key: bool = True
    #: ``Client(..., **options)`` で受け付けるオプション名（``COMMON_OPTIONS`` のほかに）。読むオプションを
    #: 足したらここにも足す（``tests/test_client.py`` がソースと照合する）
    option_names: frozenset[str] = frozenset()
    #: APIキーを入れるヘッダー。``headers=`` にこの名前があれば（値が None でも）、APIキーがなくてもよい
    #: （docs/design-docs/custom-headers.md）
    auth_header: str = ""
    #: ``headers=`` で渡させないヘッダー（小文字の名前, 代わりに使うオプション）。専用のオプションがあり、
    #: ライブラリがその値を見て検査するもの
    reserved_headers: tuple[tuple[str, str], ...] = ()
    #: True なら、同じプロバイダーキーの別のモデルが生成した生データも再送する。
    #: プロバイダーが「モデルを替えても推論情報を捨てるな」と文書で求めているときだけ True にする
    #: （docs/design-docs/provider-state-resend.md）
    resend_across_models: bool = False

    @classmethod
    def check_options(cls, key: str, options: dict[str, Any]) -> None:
        """知らないオプション名を ConfigurationError にする。打ち間違いや、
        別のアダプター用のオプションを黙って無視しないため。"""
        accepted = cls.option_names | COMMON_OPTIONS
        unknown = sorted(set(options) - accepted)
        if unknown:
            raise ConfigurationError(
                f"unknown option(s) for {key!r}: {', '.join(unknown)}. Options for this provider: {', '.join(sorted(accepted))}"
            )
        if "headers" in options:
            cls._check_headers(options["headers"])

    @classmethod
    def _check_headers(cls, headers: Any) -> None:
        """送る前に分かる ``headers=`` の誤りを知らせる。名前は大文字と小文字を区別しない（RFC 9110 の 5.1）。

        認証のヘッダーにキーが入っていることがあるので、メッセージにはヘッダーの値を出さない（docs/SECURITY.md）。"""
        expected = "headers must be a dict of str to str or None"
        if not isinstance(headers, dict):
            raise TypeError(f"{expected}, got {type(headers).__name__}")
        for name, value in headers.items():
            if not isinstance(name, str):
                raise TypeError(f"{expected}; got the name {name!r}")
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{expected}; {name!r} has a {type(value).__name__} value")
        seen: dict[str, str] = {}
        reserved = dict(cls.reserved_headers)
        for name in headers:
            lowered = name.lower()
            if lowered in reserved:
                raise ConfigurationError(
                    f"{lowered!r} is set by the {reserved[lowered]}= option; pass it there instead of headers="
                )
            first = seen.setdefault(lowered, name)
            if first != name:
                raise ConfigurationError(
                    f"{first!r} and {name!r} are the same header (header names are case-insensitive); "
                    "pass only one in headers="
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
        if self.requires_api_key and envs and not self.api_key and not self._user_sets(self.auth_header):
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

    def _user_sets(self, header: str) -> bool:
        """``headers=`` にこの名前のヘッダーがあるか（大文字と小文字を区別しない。値が None で止めるときも）。"""
        return bool(header) and header.lower() in {name.lower() for name in self.options.get("headers") or {}}

    def request_headers(self, own: dict[str, str]) -> dict[str, str]:
        """アダプターが付けるヘッダーに、利用者の ``headers=`` を重ねる。

        同じ名前（大文字と小文字を区別しない。RFC 9110 の 5.1）は利用者の値で置き換え、
        同じヘッダーを2本送らない（5.3）。認証のヘッダーも置き換えられる。値が None なら、
        ライブラリのそのヘッダーを送らない（docs/design-docs/custom-headers.md）。"""
        extra: dict[str, str | None] = self.options.get("headers") or {}
        replaced = {name.lower() for name in extra}
        kept = {k: v for k, v in own.items() if k.lower() not in replaced}
        return {**kept, **{k: v for k, v in extra.items() if v is not None}}

    @staticmethod
    def system_text(messages: list[Message]) -> str:
        return "\n\n".join(m.text for m in messages if m.role == "system" and m.text)

    # ---- 実装必須 ---- #
    @abstractmethod
    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest: ...

    @abstractmethod
    def parse_response(self, data: dict[str, Any]) -> Response: ...

    @abstractmethod
    def stream_parser(self) -> StreamParser:
        """ストリーム1本ぶんの解析器を作る。"""

    # ---- ストリームの解析を回す（同期・非同期） ---- #
    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]:
        """SSE イベント列 → StreamEvent 列（最後に done）。"""
        parser = self.stream_parser()
        for event, data in events:
            yield from parser.feed(event, data)
            if parser.closed:
                break
        yield from parser.finish()

    async def aparse_stream(self, events: AsyncIterator[tuple[str, str]]) -> AsyncIterator[StreamEvent]:
        """``parse_stream`` の非同期版。解析は同じ ``StreamParser`` が行う。"""
        parser = self.stream_parser()
        async for event, data in events:
            for ev in parser.feed(event, data):
                yield ev
            if parser.closed:
                break
        for ev in parser.finish():
            yield ev
