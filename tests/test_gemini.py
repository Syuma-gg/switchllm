from conftest import WEATHER_TOOL
from switchllm import Message
from switchllm.providers.gemini import SYNTHETIC_ID_PREFIX


def _resp(parts, finish="STOP", usage=None):
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 8, "candidatesTokenCount": 4, "thoughtsTokenCount": 6},
        "modelVersion": "gemini-flash-latest",
    }


def test_request_shape(api):
    api.reply_json(_resp([{"text": "こんにちは"}]))
    res = api.client("gemini:gemini-flash-latest").chat(
        "hi", system="丁寧に", max_tokens=256, temperature=0.5, tools=[WEATHER_TOOL], tool_choice="none"
    )
    req = api.requests[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert req.headers["x-goog-api-key"] == "test-key"
    body = api.body()
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "丁寧に"}]}
    assert body["generationConfig"] == {"maxOutputTokens": 256, "temperature": 0.5}
    assert body["tools"][0]["functionDeclarations"][0]["parameters"] == WEATHER_TOOL["parameters"]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}
    assert res.text == "こんにちは"
    assert (res.usage.input_tokens, res.usage.output_tokens) == (8, 10)  # thoughts を出力に含める


def test_parallel_calls_keep_signature_and_order(api):
    parts = [
        {"functionCall": {"id": "fc_1", "name": "get_weather", "args": {"city": "パリ"}}, "thoughtSignature": "SIG_A"},
        {"functionCall": {"id": "fc_2", "name": "get_weather", "args": {"city": "ロンドン"}}},
    ]
    api.reply_json(_resp(parts))
    api.reply_json(_resp([{"text": "どちらも雨"}]))

    client = api.client("gemini:gemini-flash-latest")
    msgs = [Message.user("パリとロンドンの天気")]
    res = client.chat(msgs, tools=[WEATHER_TOOL])
    assert res.stop_reason == "tool_use"
    msgs += [res.message, Message.tool_result("fc_1", "雨"), Message.tool_result("fc_2", '{"weather": "雨"}')]
    client.chat(msgs, tools=[WEATHER_TOOL])

    contents = api.body(1)["contents"]
    assert contents[1] == {"role": "model", "parts": parts}  # 署名を含め受け取ったまま
    # FC1, FC2 の後に FR1, FR2 が1つの user content で並ぶ。name は履歴から補完される
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "get_weather", "response": {"result": "雨"}, "id": "fc_1"}},
            {"functionResponse": {"name": "get_weather", "response": {"weather": "雨"}, "id": "fc_2"}},
        ],
    }
    assert len(contents) == 3


def test_missing_function_call_id_gets_synthetic_id_not_sent_back(api):
    api.reply_json(_resp([{"functionCall": {"name": "get_weather", "args": {"city": "京都"}}}]))
    api.reply_json(_resp([{"text": "晴れ"}]))
    client = api.client("gemini:gemini-flash-latest")
    res = client.chat("京都", tools=[WEATHER_TOOL])
    call = res.tool_calls[0]
    assert call.id.startswith(SYNTHETIC_ID_PREFIX)
    client.chat([Message.user("京都"), res.message, Message.tool_result(call.id, "晴れ")], tools=[WEATHER_TOOL])
    fr = api.body(1)["contents"][2]["parts"][0]["functionResponse"]
    assert "id" not in fr and fr["name"] == "get_weather"


def test_stream_merges_text_but_not_signed_parts(api):
    api.reply_sse(
        [
            (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "今日は"}]}}]}),
            (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "晴れです"}]}}]}),
            (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "", "thoughtSignature": "SIG_END"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5}}),
        ]
    )
    events = list(api.client("gemini:gemini-flash-latest").stream("天気"))
    assert str(api.requests[0].url).endswith(":streamGenerateContent?alt=sse")
    assert [e.text for e in events if e.type == "text"] == ["今日は", "晴れです"]
    res = events[-1].response
    assert res.text == "今日は晴れです"
    assert res.message.provider_state.raw == [{"text": "今日は晴れです"}, {"text": "", "thoughtSignature": "SIG_END"}]
    assert res.usage.output_tokens == 5


def test_blocked_prompt(api):
    api.reply_json({"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 4}})
    res = api.client("gemini:gemini-flash-latest").chat("...")
    assert res.stop_reason == "content_filter" and res.text == ""
