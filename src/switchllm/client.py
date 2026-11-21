"""利用者が触る唯一の入口。

    client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
    res = client.chat("こんにちは")

モデル文字列を省略すると環境変数 ``SWITCHLLM_MODEL`` を読むので、
コードを一切変えずに設定だけで切り替えることもできる。
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Iterator, Sequence, Union

import httpx

from ._http import RetryPolicy, request_json, stream_sse
from .errors import ConfigurationError
from .providers import PRESETS, create_provider
from .providers.base import (
    REASONING_EFFORTS,
    ChatRequest,
    Provider,
    deep_merge,
    normalize_tool_choice,
    strict_object_schema,
)
from .tools import Tool, ToolLike, as_tool
from .types import ImagePart, Message, Response, StreamEvent, TextPart

MODEL_ENV = "SWITCHLLM_MODEL"

MessagesLike = Union[str, Message, dict, Sequence[Union[Message, dict]]]

# with_model() で別のプロバイダーへ引き継ぐ設定。api_key / base_url / プロバイダー別のオプション
# （headers= など）は、キーや認証ヘッダーを別のサーバーへ送らないよう、同じプロバイダーのときだけ引き継ぐ
_TRANSPORT_SETTINGS = ("timeout", "max_retries", "retry_backoff")


def parse_model(model: str) -> tuple[str, str]:
    """``"provider:model"`` を分解する。最初のコロンで区切るので
    ``"ollama:llama3.2:3b"`` → ``("ollama", "llama3.2:3b")`` になる。"""
    provider, sep, name = model.partition(":")
    if not sep or not provider or not name:
        raise ConfigurationError(f"model must look like 'provider:model-name', got {model!r}")
    return provider.strip().lower(), name.strip()


def _to_messages(messages: MessagesLike) -> list[Message]:
    if isinstance(messages, str):
        msgs = [Message.user(messages)]
    elif isinstance(messages, Message):
        msgs = [messages]
    elif isinstance(messages, dict):
        msgs = [Message.from_dict(messages)]
    else:
        msgs = [m if isinstance(m, Message) else Message.from_dict(m) for m in messages]
    for m in msgs:
        _check_content(m)
    return msgs


def _check_content(m: Message) -> None:
    """アダプターは TextPart / ImagePart 以外のパーツを読まない。黙って落として送らないよう、先に TypeError にする。"""
    if isinstance(m.content, str):
        return
    if isinstance(m.content, list):
        bad = [p for p in m.content if not isinstance(p, (TextPart, ImagePart))]
        if not bad:
            return
        raise TypeError(
            f"{m.role} message content must be a str or a list of TextPart / ImagePart, got {bad[0]!r}. "
            "Wrap text in TextPart('...')"
        )
    raise TypeError(f"{m.role} message content must be a str or a list of TextPart / ImagePart, got {m.content!r}")


def _pick_extra(
    key: str, extra: dict[str, Any] | None, extra_by_provider: dict[str, dict[str, Any]] | None
) -> dict[str, Any]:
    """``extra`` に、選んだプロバイダーの ``extra_by_provider`` の分を重ねる（プロバイダーの分が優先）。"""
    chosen: dict[str, Any] = {}
    for name, value in (extra_by_provider or {}).items():
        name_key = name.strip().lower()
        if name_key not in PRESETS:
            raise ConfigurationError(
                f"extra_by_provider has unknown provider {name!r}. Known: {', '.join(sorted(PRESETS))}"
            )
        if not isinstance(value, dict):
            raise TypeError(f"extra_by_provider[{name!r}] must be a dict, got {value!r}")
        if name_key == key:
            chosen = value
    return deep_merge(extra or {}, chosen)


def _check_tool_choice(tool_choice: Any, tools: list[Tool]) -> None:
    """送る前に分かる tool_choice の誤りを ConfigurationError にする（API に送って料金を払う前に）。"""
    if tool_choice is None:
        return
    mode, name = normalize_tool_choice(tool_choice)
    if mode in ("required", "tool") and not tools:
        raise ConfigurationError(f"tool_choice={tool_choice!r} needs tools=[...]")
    if mode == "tool" and name not in {t.name for t in tools}:
        raise ConfigurationError(
            f"tool_choice names {name!r}, which is not in tools: {', '.join(t.name for t in tools)}"
        )


def _check_reasoning_effort(effort: Any) -> None:
    """段階の名前の誤りを送る前に知らせる。大文字や空白も直さない（各社へは名前を変えずに送るため）。"""
    if effort is None:
        return
    allowed = ", ".join(REASONING_EFFORTS)
    if not isinstance(effort, str):
        raise TypeError(f"reasoning_effort must be one of {allowed} (a str), got {effort!r}")
    if effort not in REASONING_EFFORTS:
        raise ConfigurationError(f"unknown reasoning_effort {effort!r}. Use one of: {allowed}")


def _fill_parsed(res: Response) -> None:
    """構造化出力の本文を読んで ``res.parsed`` に入れる。最後まで答えたときだけ読む。
    拒否や上限で止まった本文は、JSON として読めてもスキーマに合う保証がないため。"""
    if res.stop_reason != "end":
        return
    try:
        res.parsed = json.loads(res.text)
    except ValueError:  # JSON でない本文（JSONDecodeError は ValueError の子）
        res.parsed = None


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
        self._init_kwargs: dict[str, Any] = dict(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, **provider_options,
        )

    # ------------------------------------------------------------------ #
    @property
    def model(self) -> str:
        return f"{self.provider.key}:{self.provider.model}"

    def with_model(self, model: str, **overrides: Any) -> "Client":
        """HTTP 接続を共有したまま別モデルのクライアントを作る（フォールバックや比較用）。

        同じプロバイダー（``anthropic:a`` → ``anthropic:b``）なら、api_key / base_url /
        プロバイダー別のオプションも引き継ぐ。別のプロバイダーへは timeout / max_retries /
        retry_backoff だけを引き継ぐ（キーや ``headers=`` の認証情報を別のサーバーへ送らないため）。
        ``overrides`` はどちらの場合も最後に上書きする。"""
        key, _ = parse_model(model)
        if key == self.provider.key:
            kw = dict(self._init_kwargs)
        else:
            kw = {k: self._init_kwargs[k] for k in _TRANSPORT_SETTINGS}
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
        response_schema: dict[str, Any] | None,
        reasoning_effort: str | None,
        extra: dict[str, Any] | None,
        extra_by_provider: dict[str, dict[str, Any]] | None,
    ) -> ChatRequest:
        msgs = _to_messages(messages)
        if system:
            msgs = [Message.system(system), *msgs]
        tool_list = [as_tool(t) for t in (tools or [])]
        _check_tool_choice(tool_choice, tool_list)
        if response_schema is not None and not isinstance(response_schema, dict):
            raise TypeError(
                f"response_schema must be a JSON Schema dict, got {response_schema!r}. "
                "For a Pydantic model, pass Model.model_json_schema()"
            )
        _check_reasoning_effort(reasoning_effort)
        return ChatRequest(
            messages=msgs,
            tools=tool_list,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            response_schema=None if response_schema is None else strict_object_schema(response_schema),
            reasoning_effort=reasoning_effort,
            extra=_pick_extra(self.provider.key, extra, extra_by_provider),
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
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
        extra_by_provider: dict[str, dict[str, Any]] | None = None,
    ) -> Response:
        """1回分の応答を返す。

        ``response_schema`` に JSON Schema の dict を渡すと、答えをその形の JSON に縛り、
        読んだ値を ``Response.parsed`` に入れる（構造化出力。docs/design-docs/structured-output.md）。
        ``reasoning_effort`` は推論の強さで、``"none"``・``"minimal"``・``"low"``・``"medium"``・``"high"``・
        ``"xhigh"``・``"max"`` のどれか。各社の指定に名前を変えずに入れ、名前のない段階は送る前に
        ``UnsupportedFeatureError`` にする（docs/design-docs/reasoning-effort.md）。
        ``extra`` はプロバイダー固有パラメータをリクエスト本体にそのままマージする。
        ``extra_by_provider`` は ``{"anthropic": {...}, "openai": {...}}`` の形で各社の分を並べ、
        モデル文字列の ``provider:`` 部分と同じ名前の分だけをマージする（``extra`` より優先）。
        モデルを切り替えても、呼び出し側のコードを直さずに済む。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop,
            response_schema=response_schema, reasoning_effort=reasoning_effort,
            extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=False)
        data = request_json(self._http, http_req, provider=self.provider.key, retry=self.retry)
        res = self.provider.parse_response(data)
        if req.response_schema is not None:
            _fill_parsed(res)
        return res

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
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
        extra_by_provider: dict[str, dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        """差分を逐次返す。最後の ``done`` イベントに完成した Response（ツール呼び出し込み）が入る。
        引数の意味は ``chat()`` と同じ。``response_schema`` を渡したときの ``text`` イベントは JSON の断片で、
        ``done`` の ``response.parsed`` は ``chat()`` と同じになる。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop,
            response_schema=response_schema, reasoning_effort=reasoning_effort,
            extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=True)
        events = stream_sse(self._http, http_req, provider=self.provider.key, retry=self.retry)
        for ev in self.provider.parse_stream(events):
            if ev.type == "done" and ev.response is not None and req.response_schema is not None:
                _fill_parsed(ev.response)
            yield ev

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
