"""推論の強さ（docs/e2e-tests.md の「推論の強さ」）。

段階の名前は変えずに送る。モデルが受け付けない段階は、API のエラーをそのまま返す（モデルごとの一覧は持たない）。
"""

from __future__ import annotations

from typing import Callable

import pytest
from e2e_support import MAX_TOKENS, TOOLS, answered, ask_tool_call, describe_reasoning, requires_key, tool_roundtrip

from switchllm import BadRequestError, UnsupportedFeatureError, run_tool_calls

PROMPT = "「ok」とだけ返して"


def _rejected(e: BadRequestError, note: Callable[[str], None]) -> None:
    assert e.status_code == 400 and e.message
    note(f"API が拒否した: {e.message[:160]}")


def _rejected_before_sending(level: str, call: Callable[[], object], note: Callable[[str], None]) -> None:
    # 各社の API に名前のない段階は、送る前に知らせる（近い段階を案内する）
    with pytest.raises(UnsupportedFeatureError, match=repr(level)):
        call()
    note("送る前に UnsupportedFeatureError（呼ばない）")


@pytest.mark.premise("U23", "U24", "U26", "U27")
@pytest.mark.calls(1)
def test_reasoning_effort_low_stream(client, model, note):
    # low はどの社にもある段階なので、クラウドの API が拒否したら送る形の誤り（Gemini の大文字・小文字など）。
    # ローカルのサーバーは、推論しないモデルや、reasoning_effort を受け付けないサーバーがありうる（U27）
    try:
        events = list(client.stream(PROMPT, reasoning_effort="low", max_tokens=MAX_TOKENS))
    except BadRequestError as e:
        if requires_key(model):
            raise
        _rejected(e, note)
        return
    assert [e.type for e in events].count("done") == 1 and events[-1].type == "done", [e.type for e in events]
    res = events[-1].response
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    note(f"受け付けた: {describe_reasoning(res)}、output_tokens={res.usage.output_tokens}")


@pytest.mark.premise("U23", "U24", "U27")
@pytest.mark.calls(1)
def test_reasoning_effort_none(client, adapter, note):
    # 推論を切る。Anthropic は thinking: disabled で送る。切れないモデル（Claude Opus 5.5・Sonnet 5.5 など）は
    # API のエラーを返す。Gemini には切る段階がない
    if adapter == "gemini":
        _rejected_before_sending("none", lambda: client.chat(PROMPT, reasoning_effort="none"), note)
        return
    try:
        res = client.chat(PROMPT, reasoning_effort="none", max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        _rejected(e, note)
        return
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    note(f"受け付けた: {describe_reasoning(res)}、output_tokens={res.usage.output_tokens}")


@pytest.mark.premise("U23", "U26", "U27")
@pytest.mark.calls(1)
def test_reasoning_effort_minimal(client, adapter, note):
    # minimal は、モデルによってない段階（gpt-5.5、Gemini 4・3.8 Flash）。受け付けないモデルが 400 を返すか、
    # 黙って受け付けるかを見る。DeepSeek と Ollama は low として受け付ける。Anthropic には名前がない
    if adapter == "anthropic":
        _rejected_before_sending("minimal", lambda: client.chat(PROMPT, reasoning_effort="minimal"), note)
        return
    try:
        res = client.chat(PROMPT, reasoning_effort="minimal", max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        _rejected(e, note)
        return
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    note(f"受け付けた: {describe_reasoning(res)}、output_tokens={res.usage.output_tokens}")


@pytest.mark.premise("U23", "U24", "U25", "U26")
@pytest.mark.calls(2)
def test_reasoning_effort_with_tools(client, model, note):
    # 推論の強さを指定したまま、ツールの往復で推論情報（thinking・reasoning・署名）を送り返す
    try:
        first = tool_roundtrip(client, reasoning_effort="low")
    except BadRequestError as e:
        if requires_key(model):
            raise
        _rejected(e, note)
        return
    note(describe_reasoning(first))


@pytest.mark.only("openai", "openai_chat", "anthropic")  # Gemini には推論を切る段階がない
@pytest.mark.premise("U24")
@pytest.mark.calls(2)
def test_turn_reasoning_off_after_tool_call(client, note):
    # 推論したターン（ツールの呼び出し）のあと、推論を切って結果を渡す。Anthropic は、thinking を切った
    # リクエストで前のターンの thinking ブロックを送り返してよいかを書いていない（U24）
    msgs, first = ask_tool_call(client)
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    try:
        res = client.chat(msgs, tools=TOOLS, reasoning_effort="none", max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        _rejected(e, note)
        return
    answered(res, " after tool result")
    note(f"受け付けた（最初の応答の{describe_reasoning(first)}）")
