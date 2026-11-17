"""会話の途中でプロバイダー（またはモデル）を変えたときの履歴変換。"""

from sample_tools import WEATHER_TOOL
from switchllm import Message, register_provider
from switchllm.providers import PRESETS
from switchllm.providers.gemini import DUMMY_THOUGHT_SIGNATURE


def _claude_history(api):
    api.reply_json({
        "content": [
            {"type": "thinking", "thinking": "考える", "signature": "CLAUDE_SIG"},
            {"type": "text", "text": "調べます"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "仙台"}},
        ],
        "stop_reason": "tool_use",
        "usage": {},
    })
    claude = api.client("anthropic:claude-sonnet-5-5")
    res = claude.chat("仙台の天気", tools=[WEATHER_TOOL])
    return claude, [Message.user("仙台の天気"), res.message, Message.tool_result("toolu_1", "雪")]


def test_claude_history_to_openai_drops_thinking(api):
    claude, history = _claude_history(api)
    api.reply_json({"status": "completed", "output": [], "usage": {}})
    claude.with_model("openai:gpt-5.5", api_key="k").chat(history, tools=[WEATHER_TOOL])
    items = api.body(1)["input"]
    assert items == [
        {"role": "user", "content": "仙台の天気"},
        {"role": "assistant", "content": "調べます"},
        {"type": "function_call", "call_id": "toolu_1", "name": "get_weather", "arguments": '{"city": "仙台"}'},
        {"type": "function_call_output", "call_id": "toolu_1", "output": "雪"},
    ]
    assert "CLAUDE_SIG" not in api.requests[1].content.decode()


def test_claude_history_to_deepseek_has_no_reasoning_content(api, monkeypatch):
    # Claude の thinking は DeepSeek の reasoning_content にしない（別のモデルの推論は送らない）。
    # reasoning_content のない履歴を DeepSeek が受け付けるかは実 API で確かめる（U17）
    claude, history = _claude_history(api)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    api.reply_json({"choices": [{"message": {"content": "雪です"}, "finish_reason": "stop"}]})
    claude.with_model("deepseek:deepseek-flash").chat(history, tools=[WEATHER_TOOL])
    assert api.body(1)["messages"][1] == {"role": "assistant", "content": "調べます", "tool_calls": [
        {"id": "toolu_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "仙台"}'}}]}
    assert "考える" not in api.requests[1].content.decode()


def test_claude_history_to_gemini_injects_dummy_signature(api):
    claude, history = _claude_history(api)
    api.reply_json({"candidates": [{"content": {"parts": [{"text": "雪です"}]}, "finishReason": "STOP"}]})
    claude.with_model("gemini:gemini-flash-latest", api_key="k").chat(history, tools=[WEATHER_TOOL])
    contents = api.body(1)["contents"]
    assert contents[1] == {"role": "model", "parts": [
        {"text": "調べます"},
        {"functionCall": {"name": "get_weather", "args": {"city": "仙台"}, "id": "toolu_1"},
         "thoughtSignature": DUMMY_THOUGHT_SIGNATURE},
    ]}
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "get_weather"  # 名前を履歴から補完


def test_anthropic_other_model_gets_thinking_blocks_unchanged(api):
    # Anthropic は、モデルを切り替えるときも thinking ブロックを捨てるなと求める。
    # 新しいモデルが読めないブロックは API が捨てる（エラーにならず、料金もかからない）（U8）
    claude, history = _claude_history(api)
    api.reply_json({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}})
    claude.with_model("anthropic:claude-haiku-5-5").chat(history, tools=[WEATHER_TOOL])
    assert api.body(1)["messages"][1] == {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "考える", "signature": "CLAUDE_SIG"},
        {"type": "text", "text": "調べます"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "仙台"}},
    ]}


def test_anthropic_adapter_under_another_key_does_not_get_thinking_blocks(api, monkeypatch):
    # 再送するのは同じプロバイダーキーのときだけ。同じ形式でも接続先が違えば署名は通じない
    claude, history = _claude_history(api)
    monkeypatch.setenv("PROXY_API_KEY", "k")
    register_provider("myproxy", adapter="anthropic", base_url="https://proxy.example.com", api_key_env="PROXY_API_KEY")
    try:
        api.reply_json({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}})
        claude.with_model("myproxy:claude-sonnet-5-5").chat(history, tools=[WEATHER_TOOL])
    finally:
        PRESETS.pop("myproxy")
    assert [b["type"] for b in api.body(1)["messages"][1]["content"]] == ["text", "tool_use"]


def test_openai_other_model_does_not_get_reasoning_items(api):
    # OpenAI は、別のモデルへ reasoning アイテムを送ってよいとする文書がないので、変換し直す
    api.reply_json({"status": "completed", "usage": {}, "output": [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"仙台"}'},
    ]})
    api.reply_json({"status": "completed", "output": [], "usage": {}})
    client = api.client("openai:gpt-5.5")
    res = client.chat("仙台の天気", tools=[WEATHER_TOOL])
    history = [Message.user("仙台の天気"), res.message, Message.tool_result("call_1", "雪")]
    client.with_model("openai:gpt-6-luna").chat(history, tools=[WEATHER_TOOL])
    assert api.body(1)["input"][1] == {
        "type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city": "仙台"}'}
    assert "ENC" not in api.requests[1].content.decode()


def test_gemini_other_model_gets_dummy_signature_instead_of_real_one(api):
    # Gemini も、別のモデルへ thoughtSignature を送ってよいとする文書がないので、変換し直す
    api.reply_json({"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "get_weather", "args": {"city": "仙台"}, "id": "fc_1"}, "thoughtSignature": "GEMINI_SIG"},
    ]}, "finishReason": "STOP"}]})
    api.reply_json({"candidates": [{"content": {"parts": [{"text": "雪です"}]}, "finishReason": "STOP"}]})
    client = api.client("gemini:gemini-flash-latest")
    res = client.chat("仙台の天気", tools=[WEATHER_TOOL])
    history = [Message.user("仙台の天気"), res.message, Message.tool_result("fc_1", "雪")]
    client.with_model("gemini:gemini-pro-latest").chat(history, tools=[WEATHER_TOOL])
    assert api.body(1)["contents"][1]["parts"][0]["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE
    assert "GEMINI_SIG" not in api.requests[1].content.decode()
