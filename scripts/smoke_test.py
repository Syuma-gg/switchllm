"""実 API に対する疎通テスト。手元の APIキーで、各プロバイダーが本当に動くかを確かめる。

    python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest

各モデルで (1) テキスト応答 (2) ツール呼び出しの往復 (3) ストリーミング を試す。
少額ながら API 料金が発生する。
"""

from __future__ import annotations

import sys
import traceback
from typing import Annotated

from unillm import Client, Message, run_tool_calls, tool


@tool
def get_weather(city: Annotated[str, "都市名"]) -> dict:
    """都市の天気を返す。"""
    return {"city": city, "weather": "晴れ"}


def check(model: str) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    client = Client(model)

    def run(name: str, fn) -> None:
        try:
            results.append((name, True, fn()))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: {e}"))
            traceback.print_exc()

    run("text", lambda: client.chat("「ok」とだけ返して", max_tokens=200).text.strip()[:40])

    def tool_roundtrip() -> str:
        msgs = [Message.user("get_weather で東京の天気を調べて答えて")]
        res = client.chat(msgs, tools=[get_weather], tool_choice="required", max_tokens=1000)
        assert res.tool_calls, f"no tool call (stop_reason={res.stop_reason})"
        msgs += [res.message, *run_tool_calls([get_weather], res.tool_calls)]
        final = client.chat(msgs, tools=[get_weather], max_tokens=1000)
        return final.text.strip()[:40]

    run("tools", tool_roundtrip)

    def stream() -> str:
        chunks = 0
        done = None
        for ev in client.stream("1から5まで数えて", max_tokens=200):
            chunks += ev.type == "text"
            done = ev.response if ev.type == "done" else done
        assert done is not None
        return f"{chunks} chunks, {done.usage.output_tokens} output tokens"

    run("stream", stream)
    client.close()
    return results


def main(models: list[str]) -> int:
    failed = 0
    for model in models:
        print(f"\n=== {model} ===")
        for name, ok, info in check(model):
            failed += not ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:6} {info}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
