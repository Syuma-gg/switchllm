"""会話の途中でプロバイダー（またはモデル）を変えたときの履歴変換。"""

from conftest import WEATHER_TOOL
from unillm import Message
from unillm.providers.gemini import DUMMY_THOUGHT_SIGNATURE


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


def test_same_provider_other_model_uses_normalized_history(api):
    claude, history = _claude_history(api)
    api.reply_json({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}})
    claude.with_model("anthropic:claude-haiku-5-5", api_key="k").chat(history, tools=[WEATHER_TOOL])
    assistant = api.body(1)["messages"][1]["content"]
    assert [b["type"] for b in assistant] == ["text", "tool_use"]  # 別モデルの署名は送らない
