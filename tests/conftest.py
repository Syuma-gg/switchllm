from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from switchllm import Client


class FakeAPI:
    """httpx.MockTransport で各社 API のふりをする。送られたリクエストを記録し、積んだ応答を順に返す。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._queue: list[httpx.Response] = []

    def reply_json(self, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._queue.append(httpx.Response(status, json=body, headers=headers or {}))

    def reply_sse(self, events: list[tuple[str | None, Any]]) -> None:
        chunks = []
        for event, data in events:
            payload = data if isinstance(data, str) else json.dumps(data)
            chunks.append((f"event: {event}\n" if event else "") + f"data: {payload}\n\n")
        self._queue.append(
            httpx.Response(200, content="".join(chunks).encode(), headers={"content-type": "text/event-stream"})
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return self._queue.pop(0)

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


WEATHER_TOOL = {
    "name": "get_weather",
    "description": "現在の天気を返す",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
