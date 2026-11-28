"""構造化出力（docs/e2e-tests.md の「構造化出力」）。"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from e2e_support import ASK_TOOL, MAX_TOKENS, TOOLS, json_schema_supported, rejection

from switchllm import BadRequestError, Message, UnsupportedFeatureError, run_tool_calls

HOBBIES = ["読書", "料理", "旅行", "その他"]
# 入れ子のオブジェクトと配列を含める。additionalProperties: false はライブラリが足す（入れ子にも）
PERSON = {
    "title": "Person",
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "名前"},
        "age": {"type": "integer"},
        "address": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        "hobbies": {"type": "array", "items": {"type": "string", "enum": HOBBIES}},
    },
    "required": ["name", "age", "address", "hobbies"],
}
ASK = "次の文から人物の情報を取り出して:「大阪に住むタロウは20歳で、料理と旅行が好きだ。」"
WEATHER = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "weather": {"type": "string"}},
    "required": ["city", "weather"],
}


def _check_person(parsed: Any) -> None:
    assert isinstance(parsed, dict), parsed
    assert set(parsed) == {"name", "age", "address", "hobbies"}, parsed  # スキーマにないキーがない
    assert isinstance(parsed["name"], str) and isinstance(parsed["age"], int), parsed
    assert set(parsed["address"]) == {"city"}, parsed
    assert all(h in HOBBIES for h in parsed["hobbies"]), parsed


def _rejected_before_sending(call: Callable[[], Any], note: Callable[[str], None]) -> None:
    # JSON Schema を受け付けない接続先は、送る前に知らせる（JSON モードを案内する）
    with pytest.raises(UnsupportedFeatureError, match="json_object"):
        call()
    note("送る前に UnsupportedFeatureError（呼ばない）")


@pytest.mark.premise("U19", "U20", "U21")
@pytest.mark.calls(1)
def test_structured_output(client, model, note):
    if not json_schema_supported(model):
        _rejected_before_sending(lambda: client.chat(ASK, response_schema=PERSON, max_tokens=MAX_TOKENS), note)
        return
    res = client.chat(ASK, response_schema=PERSON, max_tokens=MAX_TOKENS)
    assert res.stop_reason == "end", f"stop_reason={res.stop_reason} text={res.text[:80]!r}"
    assert res.parsed == json.loads(res.text)
    _check_person(res.parsed)
    note(f"parsed={res.parsed}")


@pytest.mark.premise("U19", "U20", "U21")
@pytest.mark.calls(1)
def test_structured_output_stream(client, model, note):
    if not json_schema_supported(model):
        _rejected_before_sending(lambda: next(client.stream(ASK, response_schema=PERSON, max_tokens=MAX_TOKENS)), note)
        return
    events = list(client.stream(ASK, response_schema=PERSON, max_tokens=MAX_TOKENS))
    assert [e.type for e in events].count("done") == 1 and events[-1].type == "done", [e.type for e in events]
    res = events[-1].response
    assert "".join(e.text for e in events if e.type == "text") == res.text  # text の差分は JSON の断片
    assert res.stop_reason == "end", f"stop_reason={res.stop_reason}"
    _check_person(res.parsed)
    note(f"{sum(e.type == 'text' for e in events)} chunks")


@pytest.mark.premise("U22")
@pytest.mark.calls(2)
def test_structured_output_with_tools(client, model, note):
    # ツールを呼んでから、構造化した答えを返す。併用を受け付けないモデル（Gemini 3 より前など）は
    # API のエラーをそのまま返す（モデルごとの一覧は持たない）
    msgs = [Message.user(ASK_TOOL)]
    if not json_schema_supported(model):
        _rejected_before_sending(lambda: client.chat(msgs, tools=TOOLS, response_schema=WEATHER), note)
        return
    try:
        first = client.chat(msgs, tools=TOOLS, response_schema=WEATHER, max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        assert e.status_code == 400 and e.message
        note(rejection(e))
        return
    assert first.tool_calls, f"no tool call (stop_reason={first.stop_reason}, text={first.text[:80]!r})"
    assert first.parsed is None  # ツールを呼んだ応答（tool_use）は読まない
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    final = client.chat(msgs, tools=TOOLS, response_schema=WEATHER, max_tokens=MAX_TOKENS)
    if final.tool_calls:
        note("結果を渡したあとも、ツールをもう一度呼んだ（構造化した答えは確かめられなかった）")
        return
    assert final.stop_reason == "end", f"stop_reason={final.stop_reason}"
    assert isinstance(final.parsed, dict) and set(final.parsed) == {"city", "weather"}, final.text[:120]
    note(f"受け付けた: parsed={final.parsed}")
