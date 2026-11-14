"""最小のエージェントループ。モデル文字列を変えるだけで、どのプロバイダーでも同じコードで動く。

    # PowerShell
    $env:SWITCHLLM_MODEL = "gemini:gemini-flash-latest"
    python examples/agent_loop.py
"""

from __future__ import annotations

import os
from typing import Annotated

from switchllm import Client, Message, run_tool_calls, tool


@tool
def get_weather(city: Annotated[str, "都市名"]) -> dict:
    """都市の現在の天気と気温を返す（デモ用の固定値）。"""
    table = {"東京": ("晴れ", 18), "大阪": ("曇り", 16)}
    weather, temp = table.get(city, ("不明", 0))
    return {"city": city, "weather": weather, "temp_c": temp}


@tool
def add(a: float, b: float) -> float:
    """2つの数を足す。"""
    return a + b


TOOLS = [get_weather, add]


def run(client: Client, question: str, max_steps: int = 8) -> str:
    messages = [Message.user(question)]
    for _ in range(max_steps):
        res = client.chat(messages, system="必要ならツールを使って正確に答える。", tools=TOOLS, max_tokens=1024)
        # res.message には thinking 署名・thoughtSignature・reasoning アイテムが生のまま入っている。
        # 加工せずに履歴へ積むのがポイント（同じモデルに返すときに必要になる）
        messages.append(res.message)
        if res.stop_reason != "tool_use":
            return res.text
        for call in res.tool_calls:
            print(f"  -> {call.name}({call.arguments})")
        messages.extend(run_tool_calls(TOOLS, res.tool_calls))
    raise RuntimeError("max_steps exceeded")


if __name__ == "__main__":
    model = os.environ.get("SWITCHLLM_MODEL", "anthropic:claude-sonnet-5-5")
    with Client(model) as client:
        print(f"[{client.model}]")
        print(run(client, "東京と大阪の天気を調べて、2都市の気温の合計も教えて"))
