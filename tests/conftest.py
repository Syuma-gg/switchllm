from __future__ import annotations

import asyncio
import json
from typing import Any, Iterator

import httpx
import pytest

from switchllm import AsyncClient, Client


def _sse_bytes(events: list[tuple[str | None, Any]]) -> bytes:
    chunks = []
    for event, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        chunks.append((f"event: {event}\n" if event else "") + f"data: {payload}\n\n")
    return "".join(chunks).encode()


class _BrokenStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    """途中まで本文を返してから、接続が切れたように例外を起こす（同期と非同期の両方）。"""

    def __init__(self, data: bytes, exc: Exception) -> None:
        self._data = data
        self._exc = exc

    def __iter__(self):
        yield self._data
        raise self._exc

    async def __aiter__(self):
        yield self._data
        raise self._exc


class AsyncDriver:
    """``AsyncClient`` を同期のテストから呼ぶ。同期版と同じテストで、非同期版の振る舞いを確かめるため。

    ``chat()`` はイベントループで完了まで回す。``stream()`` は ``async for`` を1つずつ進めるので、
    イベントの順序と、途中で起きた例外の位置は ``AsyncClient`` のままになる。"""

    def __init__(self, inner: AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self.inner = inner
        self._loop = loop

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return self._loop.run_until_complete(self.inner.chat(*args, **kwargs))

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        events = self.inner.stream(*args, **kwargs)
        try:
            while True:
                try:
                    ev = self._loop.run_until_complete(events.__anext__())
                except StopAsyncIteration:
                    return
                yield ev
        finally:
            self._loop.run_until_complete(events.aclose())

    def with_model(self, *args: Any, **kwargs: Any) -> AsyncDriver:
        return AsyncDriver(self.inner.with_model(*args, **kwargs), self._loop)

    def close(self) -> None:
        self._loop.run_until_complete(self.inner.aclose())

    def __getattr__(self, name: str) -> Any:  # model / provider / retry / _http は AsyncClient のものを見せる
        return getattr(self.inner, name)


class FakeAPI:
    """httpx.MockTransport で各社 API のふりをする。送られたリクエストを記録し、積んだ応答を順に返す。

    ``mode`` が ``"async"`` なら、``client()`` は ``AsyncClient`` を包んだ ``AsyncDriver`` を返す。"""

    def __init__(self, mode: str = "sync") -> None:
        self.mode = mode
        self.requests: list[httpx.Request] = []
        self._queue: list[httpx.Response | Exception] = []
        self._loop = asyncio.new_event_loop() if mode == "async" else None

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

    def raw_client(self, model: str | None = None, **kw: Any) -> Client | AsyncDriver:
        """既定の引数を足さずに作る（APIキーやモデルを環境変数から読むテスト用）。"""
        transport = httpx.MockTransport(self.handler)
        if self._loop is None:
            return Client(model, http_client=httpx.Client(transport=transport), **kw)
        return AsyncDriver(AsyncClient(model, http_client=httpx.AsyncClient(transport=transport), **kw), self._loop)

    def client(self, model: str, **kw: Any) -> Client | AsyncDriver:
        kw.setdefault("api_key", "test-key")
        return self.raw_client(model, retry_backoff=0, **kw)

    def close(self) -> None:
        if self._loop is not None:
            self._loop.close()


#: クラウドのプリセットが URL を組み立てる環境変数の、テストで使う値（docs/design-docs/cloud-api-keys.md）
CLOUD_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
    "ANTHROPIC_FOUNDRY_RESOURCE": "my-foundry",
    "AWS_REGION": "us-east-1",
}
#: 開発者の環境にあると、組み立てる URL が変わる環境変数。テストでは消す
CLOUD_ENV_CLEARED = ("ANTHROPIC_FOUNDRY_BASE_URL", "AWS_DEFAULT_REGION")


@pytest.fixture
def cloud_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """クラウドのプリセットの URL を、決まった値で組み立てられるようにする。"""
    for name in CLOUD_ENV_CLEARED:
        monkeypatch.delenv(name, raising=False)
    for name, value in CLOUD_ENV.items():
        monkeypatch.setenv(name, value)
    return dict(CLOUD_ENV)


@pytest.fixture(params=["sync", "async"])
def api(request: pytest.FixtureRequest) -> Iterator[FakeAPI]:
    """同じテストを ``Client`` と ``AsyncClient`` の両方で回す（docs/design-docs/async-client.md）。"""
    fake = FakeAPI(request.param)
    yield fake
    fake.close()

