"""非同期のクライアント（docs/design-docs/async-client.md）。

``chat()``・``stream()``・リトライ・エラーなどの振る舞いは、``tests/conftest.py`` の ``api`` が
同期版と同じテストを非同期でも回して確かめる。ここでは非同期にしかないことを確かめる。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect

import httpx
import pytest

from switchllm import AsyncClient, Client
from switchllm._http import aiter_sse, iter_sse

OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}
MODEL = "anthropic:claude-sonnet-5-5"


def _mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("name", ["__init__", "chat", "stream", "with_model"])
def test_takes_the_same_arguments_as_client(name):
    # 同期から非同期へは、クラス名を替えて await を足すだけで移れる
    def params(cls):
        return [(p.name, p.kind, p.default) for p in inspect.signature(getattr(cls, name)).parameters.values()]

    assert params(AsyncClient) == params(Client)


def test_chat_is_awaited_and_stream_is_iterated_with_async_for():
    assert inspect.iscoroutinefunction(AsyncClient.chat)
    assert inspect.isasyncgenfunction(AsyncClient.stream)  # await を付けずに async for で回す
    assert inspect.iscoroutinefunction(AsyncClient.aclose)


def test_requests_run_concurrently():
    # 2つ目のリクエストが届くまで1つ目の応答を返さない。順に送っていたら待ちきれずに失敗する
    async def main():
        arrived: list[str] = []
        both = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            arrived.append(request.url.path)
            if len(arrived) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=5)
            return httpx.Response(200, json=OK)

        client = AsyncClient(MODEL, api_key="k", http_client=_mock(handler))
        results = await asyncio.gather(client.chat("1"), client.chat("2"))
        return [r.text for r in results], arrived

    texts, arrived = asyncio.run(main())
    assert texts == ["ok", "ok"] and len(arrived) == 2


def test_retry_wait_does_not_block_the_event_loop():
    # 429 のあとの待ち時間に、ほかのタスクが動く（time.sleep で待つと、再送が先に済んでしまう）
    async def main():
        log: list[str] = []
        first_failed = asyncio.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            log.append("request")
            if not first_failed.is_set():
                first_failed.set()
                return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "0.05"})
            return httpx.Response(200, json=OK)

        async def other_task():
            await first_failed.wait()
            log.append("other")

        client = AsyncClient(MODEL, api_key="k", http_client=_mock(handler))
        res, _ = await asyncio.gather(client.chat("hi"), other_task())
        return res.text, log

    text, log = asyncio.run(main())
    assert text == "ok" and log == ["request", "other", "request"]


# Anthropic のストリームの、本文の差分（「途中」）までを送ったところ
_PARTIAL_SSE = (
    'data: {"type": "message_start", "message": {"content": [], "usage": {}}}\n\n'
    'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n'
    'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "途中"}}\n\n'
).encode()


class _TrackedStream(httpx.AsyncByteStream):
    """SSE を返し、閉じられたかを記録する。``stall=True`` なら、送ったあとに次が届かない（止まった接続）。"""

    def __init__(self, data: bytes, *, stall: bool = False) -> None:
        self.data = data
        self.stall = stall
        self.closed = False

    async def __aiter__(self):
        yield self.data
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


def _serving(stream: _TrackedStream) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    return _mock(handler)


def test_leaving_a_stream_early_with_aclosing_closes_the_response():
    # 途中で抜けても、contextlib.aclosing() で囲めばその場で接続を閉じる（文書に書いた使い方）
    stream = _TrackedStream(_PARTIAL_SSE)

    async def main():
        client = AsyncClient(MODEL, api_key="k", http_client=_serving(stream))
        async with contextlib.aclosing(client.stream("hi")) as events:
            async for ev in events:
                assert ev.type == "text" and ev.text == "途中"
                assert not stream.closed
                break
        return stream.closed

    assert asyncio.run(main()) is True


def test_leaving_a_stream_early_without_aclosing_closes_it_by_the_end_of_the_loop():
    # aclosing() を使わずに break しても、接続を残したままにしない（文書に書いた約束。参照を持ったままの、いちばん遅い場合）
    stream = _TrackedStream(_PARTIAL_SSE, stall=True)
    kept = []

    async def main():
        client = AsyncClient(MODEL, api_key="k", http_client=_serving(stream))
        events = client.stream("hi")
        kept.append(events)
        async for _ in events:
            break
        assert not stream.closed

    asyncio.run(main())
    assert stream.closed  # asyncio.run がループを閉じる前に、残ったジェネレーターを閉じる


def test_cancelled_chat_is_not_retried():
    # asyncio.wait_for のタイムアウトや、FastAPI でクライアントが切断したときの取り消し。
    # 取り消しを接続エラーと取り違えて再送したり、握りつぶして続けたりしない
    async def main():
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                await asyncio.Event().wait()  # 1つ目の応答は届かない
            return httpx.Response(200, json=OK)

        client = AsyncClient(MODEL, api_key="k", http_client=_mock(handler))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.chat("hi"), timeout=0.05)
        await asyncio.sleep(0.05)  # 取り消したあとで、裏で送り直していないこと
        return len(requests)

    assert asyncio.run(main()) == 1


def test_cancelled_stream_closes_the_response():
    # ストリームを読んでいるタスクを取り消したら、HTTP の応答を閉じる（接続を残さない）
    stream = _TrackedStream(_PARTIAL_SSE, stall=True)

    async def main():
        client = AsyncClient(MODEL, api_key="k", http_client=_serving(stream))
        first = asyncio.Event()

        async def read():
            async for _ in client.stream("hi"):
                first.set()

        task = asyncio.create_task(read())
        await asyncio.wait_for(first.wait(), timeout=5)
        assert not stream.closed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return stream.closed

    assert asyncio.run(main()) is True


def test_aclose_only_closes_own_http_client():
    async def main():
        owned = AsyncClient("ollama:llama3.2")
        async with owned:
            assert repr(owned) == "AsyncClient('ollama:llama3.2')"
        assert owned._http.is_closed  # async with を抜けると閉じる

        shared = httpx.AsyncClient()
        await AsyncClient("ollama:llama3.2", http_client=shared).aclose()
        assert not shared.is_closed  # 渡された接続は呼び出し側のもの
        await shared.aclose()

    asyncio.run(main())


def test_with_model_returns_an_async_client_sharing_the_connection():
    async def main():
        async with AsyncClient(MODEL, api_key="k") as client:
            other = client.with_model("openai:gpt-5.5", api_key="k2")
            assert isinstance(other, AsyncClient) and other.model == "openai:gpt-5.5"
            assert other._http is client._http

    asyncio.run(main())


def test_http_client_of_the_wrong_kind_is_rejected():
    # 取り違えると最初の送信で分かりにくいエラーになるので、作った時点で知らせる
    sync_http = httpx.Client()
    with pytest.raises(TypeError, match=r"AsyncClient needs http_client=httpx\.AsyncClient\(\), got httpx\.Client"):
        AsyncClient("ollama:llama3.2", http_client=sync_http)
    sync_http.close()

    async def main():
        async_http = httpx.AsyncClient()
        with pytest.raises(TypeError, match=r"Client needs http_client=httpx\.Client\(\), got httpx\.AsyncClient"):
            Client("ollama:llama3.2", http_client=async_http)
        await async_http.aclose()

    asyncio.run(main())


def test_async_sse_parser_splits_events_like_the_sync_one():
    # 空行で終わらない最後のイベント、知らないフィールド（id・retry）、空白のない "data:" も同じに読む
    lines = [": comment", "event: a", "data: line1", "data: line2", "", "id: 7", "retry: 100", "data:x\r", "",
             "data: tail"]

    async def lines_async():
        for line in lines:
            yield line

    async def collect():
        return [item async for item in aiter_sse(lines_async())]

    expected = [("a", "line1\nline2"), ("", "x"), ("", "tail")]
    assert list(iter_sse(iter(lines))) == expected
    assert asyncio.run(collect()) == expected
