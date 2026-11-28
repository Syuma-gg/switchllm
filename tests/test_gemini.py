import pytest

from sample_tools import WEATHER_TOOL
from switchllm import ConfigurationError, ImagePart, Message, TextPart, ToolCall
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


def test_image_file_becomes_inline_data(api, tmp_path):
    # webp は mimetypes が判定できない環境がある（Windows の Python 3.12）。拡張子の表で image/webp にする
    path = tmp_path / "cat.WEBP"
    path.write_bytes(b"RIFF")
    api.reply_json(_resp([{"text": "猫です"}]))
    api.client("gemini:gemini-flash-latest").chat([Message.user([TextPart("これは何？"), ImagePart.from_file(path)])])
    assert api.body()["contents"] == [
        {"role": "user", "parts": [{"text": "これは何？"}, {"inlineData": {"mimeType": "image/webp", "data": "UklGRg=="}}]}
    ]


def test_image_url_is_sent_as_file_data(api):
    # 公開の HTTPS の URL は、Gemini の API が取りに行く（docs/design-docs/provider-capabilities.md、U39）。
    # ライブラリは取りに行かない（通信先を増やさない）
    # mimeType は必ず送る（「Always provide the correct MIME type」）。media_type が既定（image/png）のままなら、
    # URL の拡張子から推す。利用者が URL の画像に media_type を書くことは少ないため
    images = [
        (ImagePart(url="https://example.com/cat.png"), "image/png"),
        (ImagePart(url="https://example.com/a/cat.JPG?size=large#top"), "image/jpeg"),  # クエリと大文字は見ない
        (ImagePart(url="https://example.com/cat", media_type="image/webp"), "image/webp"),  # 書いてあればそれ
        (ImagePart(url="https://example.com/cat.jpg", media_type="image/webp"), "image/webp"),
        (ImagePart(url="https://example.com/cat"), "image/png"),  # 推せなければ既定のまま
    ]
    client = api.client("gemini:gemini-flash-latest")
    for image, _ in images:
        api.reply_json(_resp([{"text": "猫"}]))
        client.chat([Message.user([TextPart("これは何？"), image])])
    for i, (image, mime) in enumerate(images):
        assert api.body(i)["contents"][0]["parts"] == [
            {"text": "これは何？"},
            {"fileData": {"fileUri": image.url, "mimeType": mime}},
        ]


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


def test_tool_result_without_name_is_rejected_when_history_has_no_call(api):
    # Gemini は functionResponse に関数名が要る。履歴から補えないときは送る前に知らせる
    with pytest.raises(ConfigurationError, match="name="):
        api.client("gemini:gemini-flash-latest").chat([Message.user("hi"), Message.tool_result("fc_9", "雨")])
    assert api.requests == []


def test_options_disable_dummy_signature_and_switch_schema_field(api):
    api.reply_json(_resp([{"text": "ok"}]))
    history = [Message.user("hi"), Message.assistant(tool_calls=[ToolCall("toolu_1", "get_weather", {"city": "a"})]),
               Message.tool_result("toolu_1", "雨", name="get_weather")]
    client = api.client("gemini:gemini-flash-latest", inject_dummy_signatures=False, schema_field="parametersJsonSchema")
    client.chat(history, tools=[WEATHER_TOOL])
    body = api.body()
    assert body["contents"][1]["parts"] == [{"functionCall": {"name": "get_weather", "args": {"city": "a"}, "id": "toolu_1"}}]
    decl = body["tools"][0]["functionDeclarations"][0]
    assert decl["parametersJsonSchema"] == WEATHER_TOOL["parameters"] and "parameters" not in decl


def test_stop_and_specific_tool_choice(api):
    api.reply_json(_resp([{"text": "ok"}]))
    api.client("gemini:gemini-flash-latest").chat("hi", stop=["END"], tools=[WEATHER_TOOL], tool_choice={"name": "get_weather"})
    body = api.body()
    assert body["generationConfig"] == {"stopSequences": ["END"]}
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}}


@pytest.mark.parametrize(
    ("finish", "stop"),
    [("MAX_TOKENS", "max_tokens"), ("SAFETY", "content_filter"), ("PROHIBITED_CONTENT", "content_filter"), ("MALFORMED_FUNCTION_CALL", "other")],
)
def test_finish_reason_mapping(api, finish, stop):
    api.reply_json(_resp([{"text": "x"}], finish=finish))
    assert api.client("gemini:gemini-flash-latest").chat("hi").stop_reason == stop


def test_thought_parts_are_thinking(api):
    api.reply_json(_resp([{"text": "考える", "thought": True}, {"text": "答え"}]))
    res = api.client("gemini:gemini-flash-latest").chat("hi")
    assert (res.text, res.thinking) == ("答え", "考える")


def test_error_tool_result_is_sent_as_error_object(api):
    api.reply_json(_resp([{"text": "ok"}]))
    history = [Message.user("hi"), Message.assistant(tool_calls=[ToolCall("fc_1", "get_weather", {})]),
               Message.tool_result("fc_1", '{"detail": "timeout"}', is_error=True)]
    api.client("gemini:gemini-flash-latest").chat(history, tools=[WEATHER_TOOL])
    fr = api.body()["contents"][2]["parts"][0]["functionResponse"]
    assert fr["response"] == {"error": '{"detail": "timeout"}'}  # JSON でも、エラーは error に入れる


def test_stream_blocked_prompt_is_done_with_content_filter(api):
    api.reply_sse([(None, {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 4}})])
    res = list(api.client("gemini:gemini-flash-latest").stream("..."))[-1].response
    assert res.stop_reason == "content_filter" and res.text == "" and res.usage.input_tokens == 4


def test_empty_assistant_message_from_another_provider_is_not_sent(api):
    api.reply_json(_resp([{"text": "ok"}]))
    api.client("gemini:gemini-flash-latest").chat([Message.user("1つ目"), Message.assistant(""), Message.user("2つ目")])
    assert api.body()["contents"] == [{"role": "user", "parts": [{"text": "1つ目"}, {"text": "2つ目"}]}]
