"""利用者が触る唯一の入口。

    client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
    res = client.chat("こんにちは")

モデル文字列を省略すると環境変数 ``UNILLM_MODEL`` を読むので、
コードを一切変えずに設定だけで切り替えることもできる。
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Iterator, Sequence, Union

import httpx

from ._http import RetryPolicy, request_json, stream_sse
from .errors import ConfigurationError
from .providers import create_provider
from .providers.base import ChatRequest, Provider
from .tools import Tool, ToolLike, as_tool
from .types import Message, Response, StreamEvent

MODEL_ENV = "UNILLM_MODEL"

MessagesLike = Union[str, Message, dict, Sequence[Union[Message, dict]]]


def parse_model(model: str) -> tuple[str, str]:
    """``"provider:model"`` を分解する。最初のコロンで区切るので
    ``"ollama:llama3.2:3b"`` → ``("ollama", "llama3.2:3b")`` になる。"""
    provider, sep, name = model.partition(":")
    if not sep or not provider or not name:
        raise ConfigurationError(f"model must look like 'provider:model-name', got {model!r}")
    return provider.strip().lower(), name.strip()


def _to_messages(messages: MessagesLike) -> list[Message]:
    if isinstance(messages, str):
        return [Message.user(messages)]
    if isinstance(messages, Message):
        return [messages]
    if isinstance(messages, dict):
        return [Message.from_dict(messages)]
    return [m if isinstance(m, Message) else Message.from_dict(m) for m in messages]


class Client:
    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        http_client: httpx.Client | None = None,
        **provider_options: Any,
    ) -> None:
        model = model or os.environ.get(MODEL_ENV)
        if not model:
            raise ConfigurationError(f"pass a model like 'openai:gpt-5.5' or set ${MODEL_ENV}")
        key, name = parse_model(model)
        self.provider: Provider = create_provider(
            key, name, api_key=api_key, base_url=base_url, options=provider_options
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))
        self.retry = RetryPolicy(max_retries=max_retries, backoff=retry_backoff)
        self._init_kwargs = dict(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, **provider_options,
        )

    # ------------------------------------------------------------------ #
    @property
    def model(self) -> str:
        return f"{self.provider.key}:{self.provider.model}"

    def with_model(self, model: str, **overrides: Any) -> "Client":
        """HTTP 接続を共有したまま別モデルのクライアントを作る（フォールバックや比較用）。
        api_key / base_url は引き継がない（別プロバイダーのキーを誤送信しないため）。"""
        kw = {k: v for k, v in self._init_kwargs.items() if k not in ("api_key", "base_url")}
        kw.update(overrides)
        return Client(model, http_client=self._http, **kw)

    def _request(
        self,
        messages: MessagesLike,
        *,
        system: str | None,
        tools: Iterable[ToolLike] | None,
        tool_choice: Any,
        max_tokens: int | None,
        temperature: float | None,
        stop: list[str] | None,
        extra: dict[str, Any] | None,
    ) -> ChatRequest:
        msgs = _to_messages(messages)
        if system:
            msgs = [Message.system(system), *msgs]
        return ChatRequest(
            messages=msgs,
            tools=[as_tool(t) for t in (tools or [])],
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            extra=extra or {},
        )

    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: MessagesLike,
        *,
        system: str | None = None,
        tools: Iterable[ToolLike] | None = None,
        tool_choice: Any = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Response:
        """1回分の応答を返す。``extra`` はプロバイダー固有パラメータをリクエスト本体にそのままマージする。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop, extra=extra,
        )
        http_req = self.provider.build_request(req, stream=False)
        data = request_json(self._http, http_req, provider=self.provider.key, retry=self.retry)
        return self.provider.parse_response(data)

    def stream(
        self,
        messages: MessagesLike,
        *,
        system: str | None = None,
        tools: Iterable[ToolLike] | None = None,
        tool_choice: Any = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Iterator[StreamEvent]:
        """差分を逐次返す。最後の ``done`` イベントに完成した Response（ツール呼び出し込み）が入る。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop, extra=extra,
        )
        http_req = self.provider.build_request(req, stream=True)
        events = stream_sse(self._http, http_req, provider=self.provider.key, retry=self.retry)
        yield from self.provider.parse_stream(events)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Client({self.model!r})"


__all__ = ["Client", "MODEL_ENV", "parse_model", "Tool"]
