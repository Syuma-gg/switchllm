from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from switchllm import Client


def _sse_bytes(events: list[tuple[str | None, Any]]) -> bytes:
    chunks = []
    for event, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        chunks.append((f"event: {event}\n" if event else "") + f"data: {payload}\n\n")
    return "".join(chunks).encode()


class _BrokenStream(httpx.SyncByteStream):
    """途中まで本文を返してから、接続が切れたように例外を起こす。"""

    def __init__(self, data: bytes, exc: Exception) -> None:
        self._data = data
        self._exc = exc

    def __iter__(self):
        yield self._data
        raise self._exc


class FakeAPI:
    """httpx.MockTransport で各社 API のふりをする。送られたリクエストを記録し、積んだ応答を順に返す。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queue: list[httpx.Response | Exception] = []

    def reply_json(self, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._queue.append(httpx.Response(status, json=body, headers=headers or {}))

    def reply_raw(self, content: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        """JSON でない本文（プロキシの HTML など）を返す。"""
        self._queue.append(httpx.Response(status, content=content, headers=headers or {}))

    def reply_sse(self, events: list[tuple[str | None, Any]]) -> None:
        self._queue.append(
            httpx.Response(200, content=_sse_bytes(events), headers={"content-type": "text/event-stream"})
        )

    def reply_sse_then_disconnect(self, events: list[tuple[str | None, Any]]) -> None:
        """イベントをいくつか送ったあとで接続が切れる。"""
        stream = _BrokenStream(_sse_bytes(events), httpx.ReadError("connection reset"))
        self._queue.append(httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))

    def fail(self, exc: Exception) -> None:
        """応答の代わりに例外を起こす（接続できない、タイムアウトなど）。"""
        self._queue.append(exc)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[i].content)

    def client(self, model: str, **kw: Any) -> Client:
        kw.setdefault("api_key", "test-key")
        return Client(
            model,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            retry_backoff=0,
            **kw,
        )


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()

