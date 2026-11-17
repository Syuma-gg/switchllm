"""会話の途中での切り替え（docs/e2e-tests.md の「切り替え」）。

--models で隣どうしのモデル A・B について、A でツールを呼ぶ → B で結果を渡して答える → A に戻して次のターン。
"""

from __future__ import annotations

import pytest
from e2e_support import ASK_AGAIN, MAX_TOKENS, TOOLS, answered, ask_tool_call, describe_reasoning

from switchllm import Message, run_tool_calls


@pytest.mark.calls(3)
def test_switch_and_back(switch_clients, note):
    a, b = switch_clients
    msgs, first = ask_tool_call(a)
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    second = b.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS)
    answered(second, f" on {b.model}")
    msgs += [second.message, Message.user(ASK_AGAIN)]
    third = a.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS)
    answered(third, f" after switching back to {a.model}")
    note(f"{describe_reasoning(first)} → {describe_reasoning(second)} → 戻して {'ツール' if third.tool_calls else 'テキスト'}")
