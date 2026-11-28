import os
import re
import warnings

import pytest

import switchllm
from sample_tools import WEATHER_TOOL
from switchllm import (
    Client,
    ConfigurationError,
    DroppedParameterWarning,
    ImagePart,
    Message,
    StreamError,
    TextPart,
)


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
    assert req.headers["anthropic-version"] == "2026-10-21"  # 最新の版（docs/design-docs/anthropic-api-version.md）
    body = api.body()
    assert body["system"] == "A\n\nB"  # 複数の system はトップレベルに集約
    assert body["max_tokens"] == 64000  # 必須なので既定値を入れる（下の test_default_max_tokens_fits_current_models）
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["tools"] == [{"name": "get_weather", "description": "現在の天気を返す", "input_schema": WEATHER_TOOL["parameters"]}]
    assert body["tool_choice"] == {"type": "any"}
    assert body["stop_sequences"] == ["END"]
    assert res.text == "やあ" and res.stop_reason == "end"


def test_image_parts_become_image_blocks(api):
    api.reply_json({"content": [{"type": "text", "text": "猫です"}], "stop_reason": "end_turn", "usage": {}})
    api.client("anthropic:claude-sonnet-5-5").chat(
        [
            Message.user(
                [
                    TextPart("これは何？"),
                    TextPart(""),  # 空の text ブロックは拒否されるので送らない
                    ImagePart(data="iVBORw0KGgo=", media_type="image/png"),
                    ImagePart(url="https://example.com/cat.jpg"),
                ]
            )
        ]
    )
    assert api.body()["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "これは何？"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}},
                {"type": "image", "source": {"type": "url", "url": "https://example.com/cat.jpg"}},
            ],
        }
    ]


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
    with pytest.raises(StreamError) as e:
        list(api.client("anthropic:claude-sonnet-5-5").stream("hi"))
    assert e.value.message == "Overloaded"  # API の文言をそのまま
    assert e.value.body["error"]["type"] == "overloaded_error"


def test_stream_with_truncated_tool_json_keeps_raw_arguments(api):
    # max_tokens で tool_use の途中で切れると、JSON が閉じないまま content_block_stop が来る。
    # 以前は JSONDecodeError で落ちていた。他のアダプターと同じく {} と原文にする
    api.reply_sse([
        ("message_start", {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 5}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"city": "札'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 10}}),
        ("message_stop", {"type": "message_stop"}),
    ])
    res = list(api.client("anthropic:claude-sonnet-5-5").stream("札幌", tools=[WEATHER_TOOL]))[-1].response
    call = res.tool_calls[0]
    assert (call.arguments, call.raw_arguments) == ({}, '{"city": "札')
    assert res.stop_reason == "tool_use"  # ツール呼び出しがあれば常に tool_use（public-api.md）
    # 同じモデルへ送り返すときの生データは、API が受け付ける形（input はオブジェクト）
    assert res.message.provider_state.raw[0]["input"] == {}


@pytest.mark.parametrize("stream", [False, True])
def test_default_max_tokens_fits_current_models(api, stream):
    # max_tokens を省いたときは、提供中のモデルすべての出力の上限（64K〜128K）に収まる 64000 を送る。
    # thinking も max_tokens に含まれるので、4096 では考える問いが答える前に切れた。ほかの社は上限を送らず、
    # モデルの上限まで出すので、それに近づける（docs/design-docs/anthropic-max-tokens.md）
    client = api.client("anthropic:claude-sonnet-5-5")
    if stream:
        api.reply_sse(
            [
                ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"}),
            ]
        )
        list(client.stream("hi"))
    else:
        api.reply_json({"content": [], "stop_reason": "end_turn", "usage": {}})
        client.chat("hi")
    body = api.body()
    assert body["max_tokens"] == 64000
    assert body.get("stream", False) is stream


def test_options_change_headers_and_default_max_tokens(api):
    api.reply_json({"content": [], "stop_reason": "end_turn", "usage": {}})
    client = api.client("anthropic:claude-sonnet-5-5", default_max_tokens=32000, anthropic_version="2099-01-01", beta=["a", "b"])
    client.chat("hi")
    req = api.requests[0]
    assert req.headers["anthropic-version"] == "2099-01-01" and req.headers["anthropic-beta"] == "a,b"
    assert api.body()["max_tokens"] == 32000


@pytest.mark.parametrize("version", [None, "2026-10-21", "2099-01-01"])
def test_temperature_is_dropped_with_warning_on_2026_10_21_and_later(api, version):
    # 2026-10-21 からは temperature を送るだけで、どのモデルでも 400 になる。送らずに捨て、捨てたことを警告で
    # 知らせる。載せ替えてもコードは止まらない（docs/design-docs/dropped-parameter-warning.md）。0 も「指定あり」
    api.reply_json({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}})
    kw = {} if version is None else {"anthropic_version": version}
    with pytest.warns(DroppedParameterWarning) as rec:
        res = api.client("anthropic:claude-sonnet-5-5", **kw).chat("hi", temperature=0)
    assert res.text == "ok"
    assert "temperature" not in api.body()
    assert len(rec) == 1
    message = str(rec[0].message)
    assert "temperature=0 " in message  # 捨てた値
    assert "anthropic_version='2023-06-01'" in message  # 古い版を選べば送れることを案内する


def test_dropped_temperature_warning_points_at_the_caller(api):
    # 警告は、ライブラリの中ではなく、chat() / stream() を呼んだ側を指す。同じ行の警告は Python の既定で
    # 1回しか表示されないので、指す場所が呼んだ側でないと、どこで捨てたかが分からない
    api.reply_json({"content": [], "stop_reason": "end_turn", "usage": {}})
    api.reply_sse(
        [
            ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    client = api.client("anthropic:claude-sonnet-5-5")
    with pytest.warns(DroppedParameterWarning) as rec:
        client.chat("hi", temperature=0.5)
        list(client.stream("hi", temperature=0.5))
    assert len(rec) == 2
    assert all("temperature" not in api.body(i) for i in range(2))
    package_dir = os.path.dirname(switchllm.__file__)
    assert all(not os.path.normcase(w.filename).startswith(os.path.normcase(package_dir)) for w in rec)
    if api.mode == "sync":  # 非同期は、テストの道具（AsyncDriver）がイベントループで回すので、その中を指す
        assert [w.filename for w in rec] == [__file__, __file__]


def test_dropped_temperature_can_be_turned_into_an_error(api):
    # 警告を例外にすれば、送る前に止まる（以前の UnsupportedFeatureError と同じく、リクエストを送らない）
    with warnings.catch_warnings():
        warnings.simplefilter("error", DroppedParameterWarning)
        with pytest.raises(DroppedParameterWarning):
            api.client("anthropic:claude-sonnet-5-5").chat("hi", temperature=0)
    assert api.requests == []


def test_temperature_is_sent_on_older_api_version(api):
    # 古い版なら今までどおり送り、警告しない。モデルが拒否すれば API のエラーをそのまま返す
    api.reply_json({"content": [], "stop_reason": "end_turn", "usage": {}})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DroppedParameterWarning)
        api.client("anthropic:claude-opus-4-6", anthropic_version="2023-06-01").chat("hi", temperature=0.5)
    assert api.requests[0].headers["anthropic-version"] == "2023-06-01"
    assert api.body()["temperature"] == 0.5


@pytest.mark.parametrize(
    ("tool_choice", "expected"),
    [("auto", {"type": "auto"}), ("none", {"type": "none"}), ({"name": "get_weather"}, {"type": "tool", "name": "get_weather"})],
)
def test_tool_choice_mapping(api, tool_choice, expected):
    api.reply_json({"content": [], "stop_reason": "end_turn", "usage": {}})
    api.client("anthropic:claude-sonnet-5-5").chat("hi", tools=[WEATHER_TOOL], tool_choice=tool_choice)
    assert api.body()["tool_choice"] == expected


@pytest.mark.parametrize(
    ("reason", "stop"),
    [("stop_sequence", "end"), ("model_context_window_exceeded", "max_tokens"), ("refusal", "content_filter"), ("pause_turn", "other")],
)
def test_stop_reason_mapping(api, reason, stop):
    api.reply_json({"content": [{"type": "text", "text": "x"}], "stop_reason": reason, "usage": {}})
    assert api.client("anthropic:claude-sonnet-5-5").chat("hi").stop_reason == stop


def test_stream_tool_call_without_arguments(api):
    # 引数のないツールは input_json_delta が来ない（来ても空）。arguments は {} になる
    api.reply_sse([
        ("message_start", {"type": "message_start", "message": {"content": [], "usage": {}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "now", "input": {}}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}}),
        ("message_stop", {"type": "message_stop"}),
    ])
    call = list(api.client("anthropic:claude-sonnet-5-5").stream("今何時", tools=[{"name": "now"}]))[-1].response.tool_calls[0]
    assert (call.name, call.arguments, call.raw_arguments) == ("now", {}, None)


def test_empty_assistant_message_from_another_provider_is_not_sent(api):
    # 別のプロバイダーが空の応答を返した履歴でも、Anthropic が拒否する空の content を送らない。
    # 前後の user は1つのメッセージにまとまる
    api.reply_json({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}})
    api.client("anthropic:claude-sonnet-5-5").chat([Message.user("1つ目"), Message.assistant(""), Message.user("2つ目")])
    assert api.body()["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "1つ目"}, {"type": "text", "text": "2つ目"}]}
    ]


@pytest.mark.parametrize("value", ["x", None])
@pytest.mark.parametrize("name, option", [("Anthropic-Version", "anthropic_version="), ("anthropic-beta", "beta=")])
def test_version_and_beta_headers_point_to_their_options(name, option, value):
    # temperature= を送るか捨てるかは anthropic_version= の版で決めるので、ヘッダーで版を変えさせない（None で止めることも）
    with pytest.raises(ConfigurationError, match=re.escape(f"'{name.lower()}' is set by the {option} option")):
        Client("anthropic:claude-sonnet-5-5", api_key="k", headers={name: value})
