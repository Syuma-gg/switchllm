"""HTTP 送信・リトライ・SSE パースの共通処理。

同期（``httpx.Client``）と非同期（``httpx.AsyncClient``）の両方を持つ。リトライの判断と SSE の分解は共有し、
違うのは ``await`` するところだけにする（docs/design-docs/async-client.md）。
"""

from __future__ import annotations

import inspect
import math
import random
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Union

import httpx

from .errors import APIConnectionError, APIError, error_for_status

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
MAX_RETRY_AFTER = 60.0  # 秒。Retry-After がこれより長くても、ここで打ち切って再送する


@dataclass
class HTTPRequest:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    method: str = "POST"


@dataclass
class RetryPolicy:
    max_retries: int = 2
    backoff: float = 0.5  # 秒。0 にするとテスト時に待たない
    #: 待つ関数。``AsyncClient`` は ``asyncio.sleep`` を使い、非同期の送信は戻り値が awaitable なら await する
    sleep: Callable[[float], Union[None, Awaitable[None]]] = field(default=time.sleep)

    def delay(self, attempt: int, response: httpx.Response | None) -> float:
        retry_after = _retry_after(response) if response is not None else None
        if retry_after is not None:
            return retry_after
        if self.backoff <= 0:
            return 0.0
        return min(self.backoff * (2**attempt), 8.0) * (0.75 + random.random() * 0.5)


def _retry_after(response: httpx.Response) -> float | None:
    """``Retry-After`` の秒数を 0〜``MAX_RETRY_AFTER`` に収めて返す。

    秒数として読めないもの（HTTP-date 形式、``nan`` など）は None にして、通常のバックオフに任せる。
    負の値をそのまま ``time.sleep`` に渡すと ValueError になるので 0 にする。
    """
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _message_in(body: Any) -> str | None:
    """OpenAI / Anthropic / Gemini いずれも ``{"error": {"message": ...}}`` 系の形。配列で包まれていれば先頭を見る。"""
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if body.get("message"):
            return str(body["message"])
    return None


def _error_message(body: Any, text: str, reason: str) -> str:
    """API が返した文言を、手を加えずに取り出す。知らない形なら本文の先頭をそのまま使う
    （HTTP の理由句だけにすると、API が何と言ったのかが分からなくなる）。"""
    message = _message_in(body)
    if message:
        return message
    if text.strip():
        return text[:500]
    return reason or "error"


def make_api_error(provider: str, response: httpx.Response) -> APIError:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    cls = error_for_status(response.status_code)
    return cls(
        _error_message(body, response.text, response.reason_phrase),
        provider=provider,
        status_code=response.status_code,
        body=body,
    )


def _success_json(provider: str, response: httpx.Response) -> dict[str, Any]:
    """2xx の本文を dict として返す。JSON のオブジェクトでなければ APIError にする
    （プロキシが HTML を返したときなど。JSONDecodeError のまま利用者に届けない）。"""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        return body
    raise APIError(
        f"expected a JSON object in the response body, got {response.text[:200]!r}",
        provider=provider,
        status_code=response.status_code,
        body=response.text,
    )


def _can_retry(resp: httpx.Response, attempt: int, retry: RetryPolicy) -> bool:
    return resp.status_code in RETRYABLE_STATUS and attempt < retry.max_retries


async def _asleep(retry: RetryPolicy, seconds: float) -> None:
    waited = retry.sleep(seconds)
    if inspect.isawaitable(waited):
        await waited


def _retry_transport(e: httpx.TransportError, attempt: int, retry: RetryPolicy) -> bool:
    """接続の失敗のあとで送り直すか。受信の待ちのタイムアウト（``httpx.ReadTimeout``）は送り直さない。
    API はまだ生成している最中のことが多く、送り直すと同じ生成を重ねて頼み、待ちも料金も重なるため
    （docs/design-docs/read-timeout.md）。接続できなかったときなどは、リクエストが届いていないので送り直す。"""
    return attempt < retry.max_retries and not isinstance(e, httpx.ReadTimeout)


def _connection_error(provider: str, e: httpx.TransportError, client: httpx.Client | httpx.AsyncClient,
                      *, streaming: bool) -> APIConnectionError:
    if not isinstance(e, httpx.ReadTimeout):
        return APIConnectionError(f"[{provider}] {e!r}")
    read = client.timeout.read
    waited = f" ({read:g}s)" if read is not None else ""
    advice = "pass a longer timeout=" if streaming else "use stream() for long generations, or pass a longer timeout="
    return APIConnectionError(
        f"[{provider}] no response within the read timeout{waited}: {e!r}. Not retried, because the request "
        f"may still be running (and billed); {advice}"
    )


def request_json(
    client: httpx.Client, req: HTTPRequest, *, provider: str, retry: RetryPolicy
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            resp = client.request(req.method, req.url, headers=req.headers, json=req.body)
        except httpx.TransportError as e:
            if _retry_transport(e, attempt, retry):
                retry.sleep(retry.delay(attempt, None))
                attempt += 1
                continue
            raise _connection_error(provider, e, client, streaming=False) from e

        if resp.status_code < 400:
            return _success_json(provider, resp)
        if _can_retry(resp, attempt, retry):
            retry.sleep(retry.delay(attempt, resp))
            attempt += 1
            continue
        raise make_api_error(provider, resp)


async def arequest_json(
    client: httpx.AsyncClient, req: HTTPRequest, *, provider: str, retry: RetryPolicy
) -> dict[str, Any]:
    """``request_json`` の非同期版。リトライの条件と待ち時間は同じ。"""
    attempt = 0
    while True:
        try:
            resp = await client.request(req.method, req.url, headers=req.headers, json=req.body)
        except httpx.TransportError as e:
            if _retry_transport(e, attempt, retry):
                await _asleep(retry, retry.delay(attempt, None))
                attempt += 1
                continue
            raise _connection_error(provider, e, client, streaming=False) from e

        if resp.status_code < 400:
            return _success_json(provider, resp)
        if _can_retry(resp, attempt, retry):
            await _asleep(retry, retry.delay(attempt, resp))
            attempt += 1
            continue
        raise make_api_error(provider, resp)


class _SSEDecoder:
    """Server-Sent Events を1行ずつ受け取り、``(event名, data文字列)`` にまとめる。

    仕様: 空行でイベント区切り、``data:`` が複数行なら改行で連結、``:`` で始まる行はコメント。"""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def line(self, raw: str) -> tuple[str, str] | None:
        """1行を読む。イベントが1つ揃ったら返す。"""
        line = raw.rstrip("\r")
        if line == "":
            return self.end()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        return None

    def end(self) -> tuple[str, str] | None:
        """区切りの空行、または本文の終わり。``data:`` がなかったイベントは返さない。"""
        item = (self._event, "\n".join(self._data)) if self._data else None
        self._event, self._data = "", []
        return item


def iter_sse(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    """Server-Sent Events を ``(event名, data文字列)`` に分解する。"""
    decoder = _SSEDecoder()
    for raw in lines:
        item = decoder.line(raw)
        if item is not None:
            yield item
    item = decoder.end()
    if item is not None:
        yield item


async def aiter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """``iter_sse`` の非同期版。"""
    decoder = _SSEDecoder()
    async for raw in lines:
        item = decoder.line(raw)
        if item is not None:
            yield item
    item = decoder.end()
    if item is not None:
        yield item


def stream_sse(
    client: httpx.Client, req: HTTPRequest, *, provider: str, retry: RetryPolicy
) -> Iterator[tuple[str, str]]:
    """SSE ストリームを開いてイベントを返す。リトライは「最初のバイトを受け取る前」だけ行う。"""
    attempt = 0
    started = False
    while True:
        try:
            with client.stream(req.method, req.url, headers=req.headers, json=req.body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    if _can_retry(resp, attempt, retry):
                        retry.sleep(retry.delay(attempt, resp))
                        attempt += 1
                        continue
                    raise make_api_error(provider, resp)
                for item in iter_sse(resp.iter_lines()):
                    started = True
                    yield item
                return
        except httpx.TransportError as e:
            # 途中まで受信済みなら再送すると重複するので、そのまま失敗させる
            if not started and _retry_transport(e, attempt, retry):
                retry.sleep(retry.delay(attempt, None))
                attempt += 1
                continue
            raise _connection_error(provider, e, client, streaming=True) from e


async def astream_sse(
    client: httpx.AsyncClient, req: HTTPRequest, *, provider: str, retry: RetryPolicy
) -> AsyncIterator[tuple[str, str]]:
    """``stream_sse`` の非同期版。リトライは同じく「最初のバイトを受け取る前」だけ行う。"""
    attempt = 0
    started = False
    while True:
        try:
            async with client.stream(req.method, req.url, headers=req.headers, json=req.body) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    if _can_retry(resp, attempt, retry):
                        await _asleep(retry, retry.delay(attempt, resp))
                        attempt += 1
                        continue
                    raise make_api_error(provider, resp)
                # 途中で閉じられたとき、内側のジェネレーターもその場で閉じる（GC での後片づけに任せない）
                async with aclosing(aiter_sse(resp.aiter_lines())) as items:
                    async for item in items:
                        started = True
                        yield item
                return
        except httpx.TransportError as e:
            if not started and _retry_transport(e, attempt, retry):
                await _asleep(retry, retry.delay(attempt, None))
                attempt += 1
                continue
            raise _connection_error(provider, e, client, streaming=True) from e
