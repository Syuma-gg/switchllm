"""ストリーミング（docs/e2e-tests.md の「ストリーミング」）。"""

from __future__ import annotations

import pytest
from e2e_support import ASK_TOOL, MAX_TOKENS, TOOLS, answered, describe_reasoning

from switchllm import Message, run_tool_calls


def _collect(events):
    """イベントを集め、done が最後に1回だけ来ることを確かめて (text の差分, done の Response) を返す。"""
    events = list(events)
    dones = [i for i, ev in enumerate(events) if ev.type == "done"]
    assert dones == [len(events) - 1], f"done must come exactly once, at the end: {[e.type for e in events]}"
    return [ev.text for ev in events if ev.type == "text"], events[-1].response


@pytest.mark.premise("U10", "U16")
@pytest.mark.calls(1)
def test_stream_text(client, note):
    deltas, res = _collect(client.stream("1から5まで数えて", max_tokens=MAX_TOKENS))
    assert deltas, "no text delta"
    assert "".join(deltas) == res.text  # 非ストリーミングと同じ Response に組み立てる
    assert res.stop_reason == "end"
    assert res.usage.output_tokens > 0, "stream returned no usage"
    note(f"{len(deltas)} chunks, output_tokens={res.usage.output_tokens}")


@pytest.mark.premise("U11", "U16")
@pytest.mark.calls(2)
def test_stream_tool_call_then_resend(client, note):
    # ストリーミングで組み立てた生データ（DeepSeek の reasoning_content など）を、次のリクエストで送り返す
    msgs = [Message.user(ASK_TOOL)]
    _, res = _collect(client.stream(msgs, tools=TOOLS, max_tokens=MAX_TOKENS))
    assert res.tool_calls, f"no tool call (stop_reason={res.stop_reason})"
    assert res.stop_reason == "tool_use"
    msgs += [res.message, *run_tool_calls(TOOLS, res.tool_calls)]
    answered(client.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS), " after tool result")
    note(describe_reasoning(res))


@pytest.mark.premise("U16")
@pytest.mark.calls(1)
def test_stream_max_tokens(client):
    # 出力の上限で止まったストリームも、done で終わる（途中で切れたと誤解して StreamError にしない）
    _, res = _collect(client.stream("1から200まで、数字をスペース区切りで書いて。", max_tokens=16))
    assert res.stop_reason == "max_tokens", f"stop_reason={res.stop_reason}"
