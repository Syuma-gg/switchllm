import pytest

from sample_tools import WEATHER_TOOL
from switchllm import ImagePart, Message, StreamError, TextPart, UnsupportedFeatureError


def _text_response(text="こんにちは"):
    return {
        "id": "resp_1",
        "model": "gpt-5.5",
        "status": "completed",
        "output": [
            {"id": "rs_1", "type": "reasoning", "summary": [{"type": "summary_text", "text": "挨拶を返す"}]},
            {"id": "msg_1", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]},
        ],
        "usage": {"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
    }


def test_text_request_and_response(api):
    api.reply_json(_text_response())
    res = api.client("openai:gpt-5.5").chat("hi", system="簡潔に", max_tokens=100, temperature=0.2)

    req = api.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/responses"
    assert req.headers["authorization"] == "Bearer test-key"
    body = api.body()
    assert body["instructions"] == "簡潔に"
    assert body["input"] == [{"role": "user", "content": "hi"}]
    assert body["max_output_tokens"] == 100 and body["temperature"] == 0.2
    assert "max_tokens" not in body and "messages" not in body

    assert res.text == "こんにちは"
    assert res.thinking == "挨拶を返す"
    assert res.stop_reason == "end"
    assert (res.usage.input_tokens, res.usage.output_tokens) == (12, 30)
    assert res.provider == "openai"


def test_image_parts_become_input_image(api):
    api.reply_json(_text_response("どちらも猫"))
    images = [ImagePart(url="https://example.com/a.png"), ImagePart(data="iVBORw0KGgo=", media_type="image/png")]
    api.client("openai:gpt-5.5").chat([Message.user([TextPart("比べて"), *images])])
    assert api.body()["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "比べて"},
                {"type": "input_image", "image_url": "https://example.com/a.png"},
                {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="},
            ],
        }
    ]


def test_tool_roundtrip_replays_output_items(api):
    first_output = [
        {"id": "rs_9", "type": "reasoning", "summary": [], "encrypted_content": "ENC"},
        {"id": "fc_1", "type": "function_call", "call_id": "call_abc", "name": "get_weather", "arguments": '{"city": "東京"}', "status": "completed"},
    ]
    api.reply_json({"status": "completed", "output": first_output, "usage": {}})
    api.reply_json(_text_response("東京は晴れ"))

    client = api.client("openai:gpt-5.5")
    msgs = [Message.user("東京の天気は？")]
    res = client.chat(msgs, tools=[WEATHER_TOOL], tool_choice={"name": "get_weather"})
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0].id == "call_abc"
    assert res.tool_calls[0].arguments == {"city": "東京"}

    body1 = api.body(0)
    assert body1["tools"][0] == {**{"type": "function", "strict": False}, **WEATHER_TOOL}
    assert body1["tool_choice"] == {"type": "function", "name": "get_weather"}

    msgs += [res.message, Message.tool_result("call_abc", '{"weather": "晴れ"}', name="get_weather")]
    client.chat(msgs, tools=[WEATHER_TOOL])
    items = api.body(1)["input"]
    # reasoning アイテムを含め、受け取った output をそのまま返していること
    assert items[1:3] == first_output
    assert items[3] == {"type": "function_call_output", "call_id": "call_abc", "output": '{"weather": "晴れ"}'}


def test_incomplete_maps_to_max_tokens(api):
    data = _text_response()
    data.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    api.reply_json(data)
    assert api.client("openai:gpt-5.5").chat("hi").stop_reason == "max_tokens"


def test_stop_sequences_are_rejected(api):
    with pytest.raises(UnsupportedFeatureError):
        api.client("openai:gpt-5.5").chat("hi", stop=["\n"])


def test_stream(api):
    final = {
        "status": "completed",
        "output": [{"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"大阪"}'}],
        "usage": {"input_tokens": 5, "output_tokens": 7},
    }
    api.reply_sse(
        [
            ("response.created", {"type": "response.created", "response": {}}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "調べ"}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "ます"}),
            ("response.completed", {"type": "response.completed", "response": final}),
        ]
    )
    events = list(api.client("openai:gpt-5.5").stream("大阪の天気", tools=[WEATHER_TOOL]))
    assert api.body()["stream"] is True
    assert [e.text for e in events if e.type == "text"] == ["調べ", "ます"]
    done = events[-1]
    assert done.type == "done"
    assert done.response.tool_calls[0].arguments == {"city": "大阪"}
    assert done.response.usage.output_tokens == 7


def test_refusal_maps_to_content_filter(api):
    api.reply_json({
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "お答えできません"}]}],
        "usage": {},
    })
    res = api.client("openai:gpt-5.5").chat("...")
    assert res.stop_reason == "content_filter" and res.text == "お答えできません"


def test_unfinished_status_maps_to_other(api):
    api.reply_json({"status": "in_progress", "output": [], "usage": {}})
    assert api.client("openai:gpt-5.5").chat("hi").stop_reason == "other"


def test_tool_error_result_and_call_id_fallback(api):
    # call_id がなければ id を使う。エラーの結果は先頭に Error: を付けて伝える
    api.reply_json({"status": "completed", "output": [{"type": "function_call", "id": "fc_1", "name": "get_weather", "arguments": "{}"}], "usage": {}})
    api.reply_json(_text_response())
    client = api.client("openai:gpt-5.5")
    res = client.chat("hi", tools=[WEATHER_TOOL])
    assert res.tool_calls[0].id == "fc_1"
    client.chat([Message.user("hi"), res.message, Message.tool_result("fc_1", "timeout", is_error=True)], tools=[WEATHER_TOOL])
    assert api.body(1)["input"][-1] == {"type": "function_call_output", "call_id": "fc_1", "output": "Error: timeout"}


def test_stream_error_event(api):
    api.reply_sse([("error", {"type": "error", "code": "server_error", "message": "upstream failed"})])
    with pytest.raises(StreamError, match="upstream failed"):
        list(api.client("openai:gpt-5.5").stream("hi"))


def test_stream_incomplete_is_done_with_max_tokens(api):
    data = _text_response("途中まで")
    data.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    api.reply_sse([("response.incomplete", {"type": "response.incomplete", "response": data})])
    events = list(api.client("openai:gpt-5.5").stream("hi"))
    assert events[-1].response.stop_reason == "max_tokens" and events[-1].response.text == "途中まで"


def test_stream_reasoning_summary_is_thinking(api):
    api.reply_sse([
        ("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "delta": "考える"}),
        ("response.completed", {"type": "response.completed", "response": _text_response()}),
    ])
    events = list(api.client("openai:gpt-5.5").stream("hi"))
    assert [(e.type, e.text) for e in events[:-1]] == [("thinking", "考える")]


def test_stream_done_marker_before_completed_is_stream_error(api):
    # Responses API は [DONE] を送らないが、間に入るプロキシが送ることがある。JSON として読まずに打ち切る
    api.reply_sse([(None, "[DONE]")])
    with pytest.raises(StreamError, match="before response.completed"):
        list(api.client("openai:gpt-5.5").stream("hi"))
