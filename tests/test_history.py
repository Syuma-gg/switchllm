"""履歴の JSON 保存・復元（Message.to_dict / from_dict。docs/design-docs/history-serialization.md）。

保存した会話を復元しても、推論情報（provider_state）を落とさずに同じリクエストを送れること。
"""

from __future__ import annotations

import json

import pytest
from sample_tools import WEATHER_TOOL

from switchllm import ImagePart, Message, TextPart, ToolCall
from switchllm.types import ProviderState


def _json_round_trip(messages: list[Message]) -> list[dict]:
    """データベースなどに保存して読み戻したのと同じ状態にする。"""
    return json.loads(json.dumps([m.to_dict() for m in messages]))


def test_to_dict_omits_defaults():
    assert Message.user("hi").to_dict() == {"role": "user", "content": "hi"}
    assert Message.system("s").to_dict() == {"role": "system", "content": "s"}


def test_every_field_survives_the_round_trip():
    messages = [
        Message.user([TextPart("比べて"), ImagePart(data="iVBORw0KGgo=", media_type="image/png"),
                      ImagePart(url="https://example.com/a.jpg", media_type="image/jpeg")]),
        Message(
            role="assistant",
            content="調べます",
            tool_calls=[ToolCall(id="c1", name="f", arguments={"city": "東京"}),
                        ToolCall(id="c2", name="g", arguments={}, raw_arguments="{broken")],
            provider_state=ProviderState("anthropic", "claude-sonnet-5-5",
                                         [{"type": "thinking", "thinking": "", "signature": "SIG"}]),
        ),
        Message.tool_result("c1", "晴れ", name="f"),
        Message.tool_result("c2", "invalid arguments", name="g", is_error=True),
    ]
    restored = [Message.from_dict(d) for d in _json_round_trip(messages)]
    assert restored == messages


def test_from_dict_still_accepts_the_simple_forms():
    # 以前から受け付けていた形（OpenAI 風の dict、tool の id の別名）はそのまま読める
    assert Message.from_dict({"role": "user", "content": "hi"}) == Message.user("hi")
    tool = Message.from_dict({"role": "tool", "id": "c1", "content": 42})
    assert (tool.tool_call_id, tool.content) == ("c1", "42")


def test_from_dict_treats_null_content_as_empty():
    # OpenAI の形では、ツールだけを呼んだ assistant の content は null になる
    m = Message.from_dict({"role": "assistant", "content": None,
                           "tool_calls": [{"id": "c1", "name": "f", "arguments": {}}]})
    assert m.content == "" and m.text == "" and m.tool_calls[0].id == "c1"


def test_unknown_part_types_are_left_for_the_send_check():
    # 知らない種類のパーツは変換せずに残し、送る前の検査（TypeError）に任せる。黙って落とさない
    m = Message.from_dict({"role": "user", "content": [{"type": "audio", "data": "x"}]})
    assert m.content == [{"type": "audio", "data": "x"}]


# --------------------------------------------------------------------------- #
# 各アダプターで、復元した履歴から同じリクエストを送れること
# --------------------------------------------------------------------------- #
_ANTHROPIC_TOOL = {
    "content": [
        {"type": "thinking", "thinking": "考える", "signature": "CLAUDE_SIG"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "仙台"}},
    ],
    "stop_reason": "tool_use", "usage": {},
}
_OPENAI_TOOL = {
    "status": "completed", "usage": {},
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "OPENAI_ENC"},
        {"type": "function_call", "id": "fc_1", "call_id": "toolu_1", "name": "get_weather", "arguments": '{"city":"仙台"}'},
    ],
}
_GEMINI_TOOL = {
    "candidates": [{"content": {"parts": [
        {"functionCall": {"name": "get_weather", "args": {"city": "仙台"}, "id": "toolu_1"}, "thoughtSignature": "GEMINI_SIG"},
    ]}, "finishReason": "STOP"}],
}
_DEEPSEEK_TOOL = {
    "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "DEEPSEEK_REASONING",
                             "tool_calls": [{"id": "toolu_1", "type": "function",
                                             "function": {"name": "get_weather", "arguments": '{"city":"仙台"}'}}]},
                 "finish_reason": "tool_calls"}],
}
_ANSWERS = {
    "anthropic": {"content": [{"type": "text", "text": "雪"}], "stop_reason": "end_turn", "usage": {}},
    "openai": {"status": "completed", "output": [], "usage": {}},
    "gemini": {"candidates": [{"content": {"parts": [{"text": "雪"}]}, "finishReason": "STOP"}]},
    "deepseek": {"choices": [{"message": {"content": "雪"}, "finish_reason": "stop"}]},
}


@pytest.mark.parametrize(
    ("model", "tool_response", "secret"),
    [
        ("anthropic:claude-sonnet-5-5", _ANTHROPIC_TOOL, "CLAUDE_SIG"),
        ("openai:gpt-5.5", _OPENAI_TOOL, "OPENAI_ENC"),
        ("gemini:gemini-flash-latest", _GEMINI_TOOL, "GEMINI_SIG"),
        ("deepseek:deepseek-flash", _DEEPSEEK_TOOL, "DEEPSEEK_REASONING"),
    ],
    ids=["anthropic", "openai", "gemini", "deepseek"],
)
def test_restored_history_sends_the_same_request(api, model, tool_response, secret):
    api.reply_json(tool_response)
    for _ in range(2):
        api.reply_json(_ANSWERS[model.split(":")[0]])
    client = api.client(model)
    res = client.chat("仙台の天気", tools=[WEATHER_TOOL])
    history = [Message.user("仙台の天気"), res.message, Message.tool_result("toolu_1", "雪", name="get_weather")]

    client.chat(history, tools=[WEATHER_TOOL])
    client.chat(_json_round_trip(history), tools=[WEATHER_TOOL])  # chat() は保存した dict をそのまま受け取る
    assert api.body(2) == api.body(1)
    assert secret in api.requests[2].content.decode()  # 推論情報を送り返している
