from conftest import WEATHER_TOOL
from switchllm import Message, register_provider
from switchllm.providers import PRESETS


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
