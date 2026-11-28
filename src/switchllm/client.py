"""利用者が触る入口。同期の ``Client`` と、非同期の ``AsyncClient``。

    client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
    res = client.chat("こんにちは")

モデル文字列を省略すると環境変数 ``SWITCHLLM_MODEL`` を読むので、
コードを一切変えずに設定だけで切り替えることもできる。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import aclosing, contextmanager
from typing import Any, AsyncIterator, Callable, Iterable, Iterator, Sequence, Union

import httpx

from ._http import RetryPolicy, arequest_json, astream_sse, request_json, stream_sse
from .errors import BadRequestError, ConfigurationError, UnsupportedFeatureError
from .providers import ADAPTERS, PRESETS, create_provider
from .providers.base import (
    REASONING_EFFORTS,
    ChatRequest,
    Provider,
    deep_merge,
    normalize_tool_choice,
    strict_object_schema,
)
from .tools import Tool, ToolLike, as_tool
from .types import FilePart, ImagePart, Message, Response, StreamEvent, TextPart

MODEL_ENV = "SWITCHLLM_MODEL"
#: 受信の待ちの既定（秒）。公式の OpenAI・Anthropic の SDK と同じ 10 分。接続の待ちは 10 秒（docs/RELIABILITY.md）
DEFAULT_TIMEOUT = 600.0
CONNECT_TIMEOUT = 10.0

MessagesLike = Union[str, Message, dict, Sequence[Union[Message, dict]]]

# with_model() で別のプロバイダーへ引き継ぐ設定。api_key / base_url / プロバイダー別のオプション
# （headers= など）は、キーや認証ヘッダーを別のサーバーへ送らないよう、同じプロバイダーのときだけ引き継ぐ。
# options_by_provider= は接続先ごとに分けて書いてあり、切り替え先の分しか使わないので引き継ぐ
_TRANSPORT_SETTINGS = ("timeout", "max_retries", "retry_backoff")
_CARRIED_TO_OTHER_PROVIDERS = (*_TRANSPORT_SETTINGS, "options_by_provider")

# 選んだ接続先が知らないオプションを渡されたときに、書き分ける方法を案内する
_OPTIONS_HINT = "To pass an option only to some providers, use options_by_provider={'<provider>': {...}}"


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
    """アダプターは TextPart / ImagePart / FilePart 以外のパーツを読まず、user 以外のメッセージからは
    文字しか読まない。黙って落として送らないよう、先に例外にする。"""
    if isinstance(m.content, str):
        return
    expected = f"{m.role} message content must be a str or a list of TextPart / ImagePart / FilePart"
    if not isinstance(m.content, list):
        raise TypeError(f"{expected}, got {m.content!r}")
    bad = [p for p in m.content if not isinstance(p, (TextPart, ImagePart, FilePart))]
    if bad:
        raise TypeError(f"{expected}, got {bad[0]!r}. Wrap text in TextPart('...')")
    media = [p for p in m.content if isinstance(p, (ImagePart, FilePart))]
    if media and m.role != "user":
        raise TypeError(
            f"{m.role} message content can't include {type(media[0]).__name__}; "
            "only user messages can carry images and files"
        )
    for p in media:
        if isinstance(p, FilePart):
            _check_file(p)
        else:
            _check_image(p)


def _check_image(p: ImagePart) -> None:
    """中身のない画像を、送る前に知らせる。アダプターは ``url`` があれば ``url`` だけを送り、
    なければ ``data`` と ``media_type`` を送る。"""
    if p.url:
        if not isinstance(p.url, str):
            raise TypeError(f"ImagePart.url must be a str, got {type(p.url).__name__}")
        return
    if not isinstance(p.data, str) or not p.data:
        raise TypeError("ImagePart needs a non-empty data or url; build images with ImagePart.from_file(path)")
    if not isinstance(p.media_type, str) or not p.media_type:
        raise TypeError("ImagePart.media_type must be a non-empty str when data is given")


def _check_file(p: FilePart) -> None:
    """どのプロバイダーでも送れない FilePart を、送る前に知らせる（docs/design-docs/file-input.md）。"""
    for name in ("data", "media_type"):
        if not isinstance(getattr(p, name), str) or not getattr(p, name):
            raise TypeError(f"FilePart.{name} must be a non-empty str; build files with FilePart.from_file(path)")
    kind = p.media_type.split("/", 1)[0].lower()
    if kind == "image":
        raise UnsupportedFeatureError(f"FilePart can't send images ({p.media_type}); use ImagePart instead")
    if kind in ("audio", "video"):
        raise UnsupportedFeatureError(f"audio and video input is not supported ({p.media_type})")


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


@contextmanager
def _hint_rejected(provider: Provider, req: ChatRequest) -> Iterator[None]:
    """API が 400 系で断ったら、原因とみられる引数と、どのモデルでも動く書き方を ``str(e)`` に足す。
    ``e.message`` は API の文言のまま変えない（docs/design-docs/error-hints.md）。"""
    try:
        yield
    except BadRequestError as e:
        hints = provider.error_hints(req, e.message, e.body)
        if hints:
            e.args = (str(e) + "".join(f"\nHint ({arg}): {text}" for arg, text in hints),)
        raise


def _pick_options(key: str, options: dict[str, Any], options_by_provider: Any) -> dict[str, Any]:
    """``Client(..., **options)`` に、選んだ接続先の ``options_by_provider`` の分を重ねる（接続先の分が優先）。

    選ばれなかった接続先の分も、オプション名と中身を検査する。打ち間違いに、その接続先へ載せ替えてから
    気づかないようにするため（docs/design-docs/provider-specific-params.md）。"""
    if options_by_provider is None:
        options_by_provider = {}
    if not isinstance(options_by_provider, dict):
        raise TypeError(f"options_by_provider must be a dict of provider name to options, got {type(options_by_provider).__name__}")
    chosen: dict[str, Any] = {}
    for name, value in options_by_provider.items():
        name_key = str(name).strip().lower()
        preset = PRESETS.get(name_key)
        if preset is None:
            raise ConfigurationError(
                f"options_by_provider has unknown provider {name!r}. Known: {', '.join(sorted(PRESETS))}"
            )
        if not isinstance(value, dict):
            raise TypeError(f"options_by_provider[{name!r}] must be a dict, got {value!r}")
        ADAPTERS[preset.adapter].check_options(name_key, value)
        if name_key == key:
            chosen = value
    preset = PRESETS.get(key)
    if preset is not None:  # 知らない接続先なら、create_provider() が知らせる
        ADAPTERS[preset.adapter].check_options(key, options, hint=_OPTIONS_HINT)
    return deep_merge(options, chosen)


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


def _check_reasoning_summary(summary: Any, effort: str | None) -> None:
    """bool だけを受け付ける。段階（OpenAI の concise・detailed）はほかの社で送れず、切り替えを妨げるため
    受け付けない。推論を切りながら本文を頼むのは矛盾するので、送る前に知らせる。"""
    if not isinstance(summary, bool):
        raise TypeError(
            f"reasoning_summary must be True or False, got {summary!r}. "
            "For a provider-specific summary level, pass it with extra="
        )
    if summary and effort == "none":
        raise ConfigurationError(
            "reasoning_summary=True asks for the model's reasoning, but reasoning_effort='none' turns reasoning off. "
            "Drop one of them"
        )


def _fill_parsed(res: Response) -> None:
    """構造化出力の本文を読んで ``res.parsed`` に入れる。最後まで答えたときだけ読む。
    拒否や上限で止まった本文は、JSON として読めてもスキーマに合う保証がないため。"""
    if res.stop_reason != "end":
        return
    try:
        res.parsed = json.loads(res.text)
    except ValueError:  # JSON でない本文（JSONDecodeError は ValueError の子）
        res.parsed = None


def _finish(req: ChatRequest, res: Response) -> Response:
    if req.response_schema is not None:
        _fill_parsed(res)
    return res


def _finish_event(req: ChatRequest, ev: StreamEvent) -> StreamEvent:
    if ev.type == "done" and ev.response is not None:
        _finish(req, ev.response)
    return ev


def _type_name(obj: Any) -> str:
    cls = type(obj)
    module = "httpx" if cls.__module__.startswith("httpx.") else cls.__module__
    return f"{module}.{cls.__qualname__}"


class _BaseClient:
    """``Client`` と ``AsyncClient`` が共有する部分。モデル文字列の解決、送る前の検査、
    ``ChatRequest`` の組み立て、``with_model()`` で引き継ぐ設定（docs/design-docs/async-client.md）。"""

    #: このクライアントが使う httpx のクライアントの型（``httpx.Client`` / ``httpx.AsyncClient``）
    _http_type: type = httpx.Client
    #: リトライの前に待つ関数
    _retry_sleep: Callable[[float], Any] = staticmethod(time.sleep)

    def _setup(
        self,
        model: str | None,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        max_retries: int,
        retry_backoff: float,
        http_client: Any,
        options_by_provider: Any,
        provider_options: dict[str, Any],
    ) -> None:
        if http_client is not None and not isinstance(http_client, self._http_type):
            # 取り違えると、最初の送信で分かりにくいエラーになる
            raise TypeError(
                f"{type(self).__name__} needs http_client=httpx.{self._http_type.__name__}(), "
                f"got {_type_name(http_client)}"
            )
        model = model or os.environ.get(MODEL_ENV)
        if not model:
            raise ConfigurationError(f"pass a model like 'openai:gpt-5.5' or set ${MODEL_ENV}")
        key, name = parse_model(model)
        options = _pick_options(key, provider_options, options_by_provider)
        self.provider: Provider = create_provider(key, name, api_key=api_key, base_url=base_url, options=options)
        self._owns_http = http_client is None
        self._http = http_client or self._http_type(timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT))
        self.retry = RetryPolicy(max_retries=max_retries, backoff=retry_backoff, sleep=self._retry_sleep)
        self._init_kwargs: dict[str, Any] = dict(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, options_by_provider=options_by_provider, **provider_options,
        )

    # ------------------------------------------------------------------ #
    @property
    def model(self) -> str:
        return f"{self.provider.key}:{self.provider.model}"

    def _with_model_kwargs(self, model: str, overrides: dict[str, Any]) -> dict[str, Any]:
        key, _ = parse_model(model)
        if key == self.provider.key:
            kw = dict(self._init_kwargs)
        else:
            kw = {k: self._init_kwargs[k] for k in _CARRIED_TO_OTHER_PROVIDERS}
        kw.update(overrides)
        return kw

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
        reasoning_summary: Any,
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
        _check_reasoning_summary(reasoning_summary, reasoning_effort)
        return ChatRequest(
            messages=msgs,
            tools=tool_list,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            response_schema=None if response_schema is None else strict_object_schema(response_schema),
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            extra=_pick_extra(self.provider.key, extra, extra_by_provider),
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.model!r})"


class Client(_BaseClient):
    """同期のクライアント。非同期で使うときは ``AsyncClient``（引数とメソッドの名前は同じ）。"""

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        http_client: httpx.Client | None = None,
        options_by_provider: dict[str, dict[str, Any]] | None = None,
        **provider_options: Any,
    ) -> None:
        self._setup(
            model, api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, http_client=http_client, options_by_provider=options_by_provider,
            provider_options=provider_options,
        )

    def with_model(self, model: str, **overrides: Any) -> Client:
        """HTTP 接続を共有したまま別モデルのクライアントを作る（フォールバックや比較用）。

        同じプロバイダー（``anthropic:a`` → ``anthropic:b``）なら、api_key / base_url /
        プロバイダー別のオプションも引き継ぐ。別のプロバイダーへは timeout / max_retries /
        retry_backoff と ``options_by_provider`` だけを引き継ぐ（キーや ``headers=`` の認証情報を別のサーバーへ
        送らないため。``options_by_provider`` は切り替え先の分しか使わない）。
        ``overrides`` はどちらの場合も最後に上書きする。"""
        return Client(model, http_client=self._http, **self._with_model_kwargs(model, overrides))

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
        reasoning_summary: bool = False,
        extra: dict[str, Any] | None = None,
        extra_by_provider: dict[str, dict[str, Any]] | None = None,
    ) -> Response:
        """1回分の応答を返す。

        ``response_schema`` に JSON Schema の dict を渡すと、答えをその形の JSON に縛り、
        読んだ値を ``Response.parsed`` に入れる（構造化出力。docs/design-docs/structured-output.md）。
        ``reasoning_effort`` は推論の強さで、``"none"``・``"minimal"``・``"low"``・``"medium"``・``"high"``・
        ``"xhigh"``・``"max"`` のどれか。各社の指定に名前を変えずに入れ、名前のない段階は送る前に
        ``UnsupportedFeatureError`` にする（docs/design-docs/reasoning-effort.md）。
        ``reasoning_summary=True`` は推論の要約を返させ、``Response.thinking`` と ``thinking`` イベントに入れる。
        各社の「要約を返す」指定を送り、Chat Completions 互換のサーバーには何も足さない（返したら入れる）。
        要約を返すかはモデルが決めるので、頼んでも ``None`` のことがある（docs/design-docs/reasoning-summary.md）。
        ``extra`` はプロバイダー固有パラメータをリクエスト本体にそのままマージする。
        ``extra_by_provider`` は ``{"anthropic": {...}, "openai": {...}}`` の形で各社の分を並べ、
        モデル文字列の ``provider:`` 部分と同じ名前の分だけをマージする（``extra`` より優先）。
        モデルを切り替えても、呼び出し側のコードを直さずに済む。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop,
            response_schema=response_schema, reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary, extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=False)
        with _hint_rejected(self.provider, req):
            data = request_json(self._http, http_req, provider=self.provider.key, retry=self.retry)
        return _finish(req, self.provider.parse_response(data))

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
        reasoning_summary: bool = False,
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
            reasoning_summary=reasoning_summary, extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=True)
        events = stream_sse(self._http, http_req, provider=self.provider.key, retry=self.retry)
        with _hint_rejected(self.provider, req):  # HTTP の 400 は、最初のイベントを読もうとしたときに分かる
            for ev in self.provider.parse_stream(events):
                yield _finish_event(req, ev)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncClient(_BaseClient):
    """非同期のクライアント。引数とメソッドの名前は ``Client`` と同じで、``chat()`` は await し、
    ``stream()`` は ``async for`` で回す（docs/design-docs/async-client.md）。

        async with AsyncClient("anthropic:claude-sonnet-5-5") as client:
            res = await client.chat("こんにちは")
            async for ev in client.stream("続けて"):
                ...
    """

    _http_type = httpx.AsyncClient
    _retry_sleep = staticmethod(asyncio.sleep)  # 待つ間もイベントループを止めない

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
        options_by_provider: dict[str, dict[str, Any]] | None = None,
        **provider_options: Any,
    ) -> None:
        self._setup(
            model, api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, http_client=http_client, options_by_provider=options_by_provider,
            provider_options=provider_options,
        )

    def with_model(self, model: str, **overrides: Any) -> AsyncClient:
        """HTTP 接続を共有したまま別モデルのクライアントを作る。引き継ぐ設定は ``Client.with_model()`` と同じ。"""
        return AsyncClient(model, http_client=self._http, **self._with_model_kwargs(model, overrides))

    # ------------------------------------------------------------------ #
    async def chat(
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
        reasoning_summary: bool = False,
        extra: dict[str, Any] | None = None,
        extra_by_provider: dict[str, dict[str, Any]] | None = None,
    ) -> Response:
        """1回分の応答を返す。引数の意味は ``Client.chat()`` と同じ。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop,
            response_schema=response_schema, reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary, extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=False)
        with _hint_rejected(self.provider, req):
            data = await arequest_json(self._http, http_req, provider=self.provider.key, retry=self.retry)
        return _finish(req, self.provider.parse_response(data))

    async def stream(
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
        reasoning_summary: bool = False,
        extra: dict[str, Any] | None = None,
        extra_by_provider: dict[str, dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """差分を逐次返す。``await`` は付けずに ``async for`` で回す。引数と返すイベントは ``Client.stream()`` と同じ。

        最後まで読めば接続は閉じる。途中で抜けたときにすぐ閉じたいなら ``contextlib.aclosing()`` で囲む。"""
        req = self._request(
            messages, system=system, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature, stop=stop,
            response_schema=response_schema, reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary, extra=extra, extra_by_provider=extra_by_provider,
        )
        http_req = self.provider.build_request(req, stream=True)
        # 途中で閉じられたとき、内側のジェネレーター（と HTTP の応答）もその場で閉じる
        async with aclosing(astream_sse(self._http, http_req, provider=self.provider.key, retry=self.retry)) as events:
            async with aclosing(self.provider.aparse_stream(events)) as parsed:
                with _hint_rejected(self.provider, req):  # HTTP の 400 は、最初のイベントを読もうとしたときに分かる
                    async for ev in parsed:
                        yield _finish_event(req, ev)

    # ------------------------------------------------------------------ #
    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


__all__ = ["AsyncClient", "Client", "MODEL_ENV", "parse_model", "Tool"]
