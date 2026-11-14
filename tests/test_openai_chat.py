import pytest

from conftest import WEATHER_TOOL
from switchllm import ImagePart, Message, TextPart, ToolCall, register_provider
from switchllm.providers import PRESETS


def test_image_parts_become_image_url(api):
    api.reply_json({"choices": [{"message": {"role": "assistant", "content": "どちらも猫"}, "finish_reason": "stop"}]})
    images = [ImagePart(url="https://example.com/a.png"), ImagePart(data="iVBORw0KGgo=", media_type="image/png")]
    api.client("ollama:llava").chat([Message.user([TextPart("比べて"), *images])])
    assert api.body()["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "比べて"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        }
    ]


def test_ollama_preset_without_key(api):
    api.reply_json({"choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1}})
    client = api.client("ollama:llama3.2:3b", api_key=None)
    res = client.chat("hi", system="sys", max_tokens=50, stop=["x"])
    req = api.requests[0]
    assert str(req.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in req.headers
    body = api.body()
    assert body["model"] == "llama3.2:3b"  # 2つ目以降のコロンはモデル名の一部
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert body["max_tokens"] == 50 and body["stop"] == ["x"]
    assert res.text == "hello" and res.usage.total_tokens == 3


def test_tool_call_roundtrip_via_registered_cloud_provider(api, monkeypatch):
    # プリセットにない互換クラウド API は register_provider で足す（キーは環境変数から）
    monkeypatch.setenv("MYCLOUD_API_KEY", "secret")
    register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
    try:
        _tool_call_roundtrip(api, api.client("mycloud:some-model", api_key=None))
    finally:
        PRESETS.pop("mycloud")
    assert str(api.requests[0].url) == "https://api.example.com/v1/chat/completions"
    assert api.requests[0].headers["authorization"] == "Bearer secret"


def _tool_call_roundtrip(api, client):
    api.reply_json({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"福岡"}'}}]},
        "finish_reason": "tool_calls"}]})
    api.reply_json({"choices": [{"message": {"role": "assistant", "content": "曇り"}, "finish_reason": "stop"}]})
    res = client.chat("福岡", tools=[WEATHER_TOOL])
    assert api.body(0)["tools"][0] == {"type": "function", "function": WEATHER_TOOL}
    assert res.tool_calls[0].arguments == {"city": "福岡"}

    client.chat([Message.user("福岡"), res.message, Message.tool_result("call_1", "曇り")], tools=[WEATHER_TOOL])
    msgs = api.body(1)["messages"]
    assert msgs[1] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "福岡"}'}}]}
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_1", "content": "曇り"}


def test_invalid_json_arguments_are_kept_raw(api):
    api.reply_json({"choices": [{"message": {"tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{broken"}}]}, "finish_reason": "tool_calls"}]})
    call = api.client("openai-chat:gpt-x").chat("x").tool_calls[0]
    assert call.arguments == {} and call.raw_arguments == "{broken"
    assert call.arguments_json() == "{broken"


def test_stream_assembles_split_tool_calls(api):
    api.reply_sse([
        (None, {"model": "m", "choices": [{"delta": {"role": "assistant", "reasoning_content": "うーん"}}]}),
        (None, {"choices": [{"delta": {"content": "了解"}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "get_weather", "arguments": ""}}]}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"city":'}}]}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"那覇"}'}}]}}]}),
        (None, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        (None, {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 11}}),
        (None, "[DONE]"),
    ])
    # reasoning_content は DeepSeek 形式の推論テキスト（互換サーバーが返すことがある）
    events = list(api.client("openai-chat:reasoning-model").stream("那覇", tools=[WEATHER_TOOL]))
    assert api.body()["stream_options"] == {"include_usage": True}
    res = events[-1].response
    assert res.text == "了解" and res.thinking == "うーん"
    assert res.tool_calls[0].id == "call_a" and res.tool_calls[0].arguments == {"city": "那覇"}
    assert res.usage.output_tokens == 11


def _ok(content="ok", finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]}


@pytest.mark.parametrize(
    ("model", "kw", "field"),
    [
        # OpenAI 本家は max_tokens を非推奨とし、o-series とは互換性がないと記載している
        ("openai-chat:gpt-5.5", {}, "max_completion_tokens"),
        # 互換サーバー（Ollama の文書には max_tokens しかない）は max_tokens のまま
        ("openai-chat:llama", {"base_url": "http://gpu:8000/v1"}, "max_tokens"),
        ("ollama:llama3.2", {"api_key": None}, "max_tokens"),
        # 明示すればそれに従う
        ("openai-chat:gpt-5.5", {"max_tokens_field": "max_tokens"}, "max_tokens"),
    ],
)
def test_max_tokens_field_depends_on_the_server(api, model, kw, field):
    api.reply_json(_ok())
    api.client(model, **kw).chat("hi", max_tokens=64)
    body = api.body()
    assert body[field] == 64
    assert not ({"max_tokens", "max_completion_tokens"} - {field}) & body.keys()  # もう一方は送らない


def test_stream_usage_can_be_disabled_and_headers_added(api):
    api.reply_sse([(None, {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}), (None, "[DONE]")])
    events = list(api.client("openai-chat:gpt-x", stream_usage=False, headers={"X-Org": "team-a"}).stream("hi"))
    assert "stream_options" not in api.body() and api.body()["stream"] is True
    assert api.requests[0].headers["x-org"] == "team-a"
    assert events[-1].response.text == "x"


@pytest.mark.parametrize(
    ("finish", "stop"),
    [("stop", "end"), (None, "end"), ("length", "max_tokens"), ("content_filter", "content_filter"), ("something_new", "other")],
)
def test_finish_reason_mapping(api, finish, stop):
    api.reply_json(_ok(finish=finish))
    assert api.client("openai-chat:gpt-x").chat("hi").stop_reason == stop


def test_list_content_and_openrouter_reasoning(api):
    # 一部の互換サーバーは content を配列で返し、OpenRouter は reasoning に推論テキストを入れる
    api.reply_json({"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "reasoning": "考えた"}, "finish_reason": "stop"}]})
    res = api.client("openai-chat:gpt-x").chat("hi")
    assert res.text == "ab" and res.thinking == "考えた"


def test_missing_tool_call_ids_get_positional_ids(api):
    api.reply_json({"choices": [{"message": {"tool_calls": [
        {"type": "function", "function": {"name": "get_weather", "arguments": '{"city": "a"}'}},
        {"type": "function", "function": {"name": "get_weather", "arguments": '{"city": "b"}'}}]}, "finish_reason": "tool_calls"}]})
    res = api.client("ollama:llama3.2", api_key=None).chat("hi", tools=[WEATHER_TOOL])
    assert [c.id for c in res.tool_calls] == ["call_0", "call_1"]


def test_tool_error_result_is_prefixed(api):
    api.reply_json(_ok())
    history = [Message.user("hi"), Message.assistant(tool_calls=[ToolCall("c1", "get_weather", {"city": "a"})]),
               Message.tool_result("c1", "timeout", is_error=True)]
    api.client("openai-chat:gpt-x").chat(history, tools=[WEATHER_TOOL])
    assert api.body()["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Error: timeout"}


def test_tool_call_with_empty_arguments_string(api):
    # 引数のないツールで arguments を "" にして返すサーバーがある。壊れた JSON ではなく引数なしとして扱う
    api.reply_json({"choices": [{"message": {"tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "now", "arguments": ""}}]}, "finish_reason": "tool_calls"}]})
    call = api.client("ollama:llama3.2", api_key=None).chat("今何時", tools=[{"name": "now"}]).tool_calls[0]
    assert (call.arguments, call.arguments_json()) == ({}, "{}")
