"""非同期のクライアント（docs/e2e-tests.md の「非同期」）。

送るリクエストと応答の解析は同期の ``Client`` と同じものを使う（docs/design-docs/async-client.md）。
ここでは、本物の HTTP の上で ``await`` と ``async for`` が最後まで動くこと（非同期の送信と SSE の読み取り）を確かめる。
"""

from __future__ import annotations

import asyncio

import pytest
from e2e_support import MAX_TOKENS


@pytest.mark.calls(2)
def test_async_chat_and_stream(open_async_client, model, note):
    async def main():
        async with open_async_client(model) as client:
            res = await client.chat("「ok」とだけ返して", max_tokens=MAX_TOKENS)
            events = [ev async for ev in client.stream("1から5まで数えて", max_tokens=MAX_TOKENS)]
        return res, events

    res, events = asyncio.run(main())
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    assert res.stop_reason == "end"
    dones = [i for i, ev in enumerate(events) if ev.type == "done"]
    assert dones == [len(events) - 1], f"done must come exactly once, at the end: {[e.type for e in events]}"
    deltas = [ev.text for ev in events if ev.type == "text"]
    assert deltas, "no text delta"
    assert "".join(deltas) == events[-1].response.text
    assert events[-1].response.stop_reason == "end"
    note(f"{len(deltas)} chunks")
