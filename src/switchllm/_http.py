"""HTTP 送信・リトライ・SSE パースの共通処理。"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

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
    sleep: Callable[[float], None] = field(default=time.sleep)

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


def _error_message(body: Any, fallback: str) -> str:
    """OpenAI / Anthropic / Gemini いずれも ``{"error": {"message": ...}}`` 系の形。"""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
        if body.get("message"):
            return str(body["message"])
    if isinstance(body, str) and body:
        return body[:500]
    return fallback


def make_api_error(provider: str, response: httpx.Response) -> APIError:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    cls = error_for_status(response.status_code)
    return cls(
        _error_message(body, response.reason_phrase or "error"),
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


def request_json(
    client: httpx.Client, req: HTTPRequest, *, provider: str, retry: RetryPolicy
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            resp = client.request(req.method, req.url, headers=req.headers, json=req.body)
        except httpx.TransportError as e:
            if attempt < retry.max_retries:
                retry.sleep(retry.delay(attempt, None))
                attempt += 1
                continue
            raise APIConnectionError(f"[{provider}] {e!r}") from e

        if resp.status_code < 400:
            return _success_json(provider, resp)
        if resp.status_code in RETRYABLE_STATUS and attempt < retry.max_retries:
            retry.sleep(retry.delay(attempt, resp))
            attempt += 1
            continue
        raise make_api_error(provider, resp)


def iter_sse(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    """Server-Sent Events を ``(event名, data文字列)`` に分解する。

    仕様: 空行でイベント区切り、``data:`` が複数行なら改行で連結、``:`` で始まる行はコメント。
    """
    event = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


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
                    if resp.status_code in RETRYABLE_STATUS and attempt < retry.max_retries:
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
            if not started and attempt < retry.max_retries:
                retry.sleep(retry.delay(attempt, None))
                attempt += 1
                continue
            raise APIConnectionError(f"[{provider}] {e!r}") from e
