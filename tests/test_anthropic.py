import pytest

from conftest import WEATHER_TOOL
from switchllm import Message, StreamError


def test_request_shape(api):
    api.reply_json({"content": [{"type": "text", "text": "やあ"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}})
    res = api.client("anthropic:claude-sonnet-5-5").chat(
        [Message.system("A"), Message.system("B"), Message.user("hi")],
        tools=[WEATHER_TOOL],
        tool_choice="required",
        stop=["END"],
    )
    req = api.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "test-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = api.body()
    assert body["system"] == "A\n\nB"  # 複数の system はトップレベルに集約
    assert body["max_tokens"] == 4096  # 必須なので既定値を入れる
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["tools"] == [{"name": "get_weather", "description": "現在の天気を返す", "input_schema": WEATHER_TOOL["parameters"]}]
    assert body["tool_choice"] == {"type": "any"}
    assert body["stop_sequences"] == ["END"]
    assert res.text == "やあ" and res.stop_reason == "end"


def test_usage_includes_cache_tokens(api):
    api.reply_json(
        {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 5, "output_tokens": 4},
        }
    )
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi")
    assert res.usage.input_tokens == 1015
    assert res.usage.details["cache_read_input_tokens"] == 1000


def test_thinking_blocks_are_replayed_and_parallel_results_merged(api):
    blocks = [
        {"type": "thinking", "thinking": "2都市を調べる", "signature": "SIG123"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "東京"}},
        {"type": "tool_use", "id": "toolu_2", "name": "get_weather", "input": {"city": "大阪"}},
    ]
    api.reply_json({"content": blocks, "stop_reason": "tool_use", "usage": {}})
    api.reply_json({"content": [{"type": "text", "text": "両方晴れ"}], "stop_reason": "end_turn", "usage": {}})

    client = api.client("anthropic:claude-sonnet-5-5")
    msgs = [Message.user("東京と大阪の天気")]
    res = client.chat(msgs, tools=[WEATHER_TOOL])
    assert res.thinking == "2都市を調べる"
    assert [c.id for c in res.tool_calls] == ["toolu_1", "toolu_2"]

    msgs.append(res.message)
    msgs.append(Message.tool_result("toolu_1", "晴れ"))
    msgs.append(Message.tool_result("toolu_2", "取得失敗", is_error=True))
    client.chat(msgs, tools=[WEATHER_TOOL])

    sent = api.body(1)["messages"]
    assert sent[1] == {"role": "assistant", "content": blocks}  # signature 付き thinking がそのまま
    assert sent[2]["role"] == "user" and len(sent) == 3  # 2つの結果が1メッセージに入る
    assert sent[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "晴れ"},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "取得失敗", "is_error": True},
    ]


def test_stream_reassembles_thinking_text_and_tool_input(api):
    api.reply_sse(
        [
            ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "content": [], "usage": {"input_tokens": 20, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "考え中"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("ping", {"type": "ping"}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "確認"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "します"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "get_weather", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"city": "札'}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '幌"}'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 42}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    events = list(api.client("anthropic:claude-sonnet-5-5").stream("札幌の天気", tools=[WEATHER_TOOL]))
    assert "".join(e.text for e in events if e.type == "text") == "確認します"
    assert "".join(e.text for e in events if e.type == "thinking") == "考え中"
    res = events[-1].response
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0].arguments == {"city": "札幌"}
    assert (res.usage.input_tokens, res.usage.output_tokens) == (20, 42)
    assert res.message.provider_state.raw[0] == {"type": "thinking", "thinking": "考え中", "signature": "SIG"}


def test_stream_error_event(api):
    api.reply_sse(
        [
            ("message_start", {"type": "message_start", "message": {"content": [], "usage": {}}}),
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        ]
    )
    with pytest.raises(StreamError, match="Overloaded"):
        list(api.client("anthropic:claude-sonnet-5-5").stream("hi"))
