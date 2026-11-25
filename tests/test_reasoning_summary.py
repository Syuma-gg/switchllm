"""推論の要約（``reasoning_summary=``）。同じ呼び出しが、各社の「要約を返す」指定になること。

判断は docs/design-docs/reasoning-summary.md、各社の形式は docs/references/ の「推論の強さ」。
返ってきた要約を ``Response.thinking`` に入れるところは、各アダプターのテストにある。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest

from switchllm import BadRequestError, ConfigurationError, UnsupportedFeatureError

_RESPONSES_OK = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "考えた"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "hi"}]},
    ],
    "usage": {},
}
_CHAT_OK = {"choices": [{"message": {"content": "hi", "reasoning_content": "考えた"}, "finish_reason": "stop"}]}
_ANTHROPIC_OK = {
    "content": [{"type": "thinking", "thinking": "考えた", "signature": "sig"}, {"type": "text", "text": "hi"}],
    "stop_reason": "end_turn",
    "usage": {},
}
_GEMINI_OK = {
    "candidates": [
        {"content": {"parts": [{"text": "考えた", "thought": True}, {"text": "hi"}]}, "finishReason": "STOP"}
    ]
}


def _gemini_thinking(b: dict[str, Any]) -> Any:
    return (b.get("generationConfig") or {}).get("thinkingConfig")


@dataclass
class Case:
    model: str
    ok: dict[str, Any]
    pick: Callable[[dict[str, Any]], Any]  # 本体から、要約の指定が入る場所を取り出す（なければ None）
    summary: Any  # reasoning_summary=True のときに、その場所に入るもの


# 各社の「要約を返す」指定。Chat Completions 互換には標準の指定がないので、何も足さない
# （DeepSeek は頼まなくても reasoning_content を返す。互換サーバーは返したら Response.thinking に入る）
NATIVE = [
    Case("openai:gpt-5.5", _RESPONSES_OK, lambda b: b.get("reasoning"), {"summary": "auto"}),
    Case(
        "anthropic:claude-sonnet-5-5",
        _ANTHROPIC_OK,
        lambda b: b.get("thinking"),
        {"type": "adaptive", "display": "summarized"},
    ),
    Case("gemini:gemini-flash-latest", _GEMINI_OK, _gemini_thinking, {"includeThoughts": True}),
]
COMPATIBLE = ["deepseek:deepseek-flash", "ollama:qwen3", "lmstudio:qwen3"]
_NATIVE_IDS = [c.model.split(":")[0] for c in NATIVE]
_COMPATIBLE_IDS = [m.split(":")[0] for m in COMPATIBLE]
ALL_MODELS = [c.model for c in NATIVE] + COMPATIBLE + ["openai-chat:gpt-5.5"]
_OK_BY_PROVIDER = {"openai": _RESPONSES_OK, "anthropic": _ANTHROPIC_OK, "gemini": _GEMINI_OK}


@pytest.mark.parametrize("case", NATIVE, ids=_NATIVE_IDS)
def test_summary_reaches_each_apis_own_field(api, case):
    api.reply_json(case.ok)
    res = api.client(case.model).chat("hi", reasoning_summary=True)
    assert case.pick(api.body()) == case.summary
    assert res.thinking == "考えた"


@pytest.mark.parametrize("case", NATIVE, ids=_NATIVE_IDS)
def test_stream_sends_the_same_field(api, case):
    # 応答は読まずに、送ったリクエストだけを見る（ストリームの組み立ては各アダプターのテストにある）
    api.reply_json({"error": {"message": "stop here"}}, status=400)
    with pytest.raises(BadRequestError):
        list(api.client(case.model, max_retries=0).stream("hi", reasoning_summary=True))
    assert case.pick(api.body()) == case.summary


@pytest.mark.parametrize("model", [c.model for c in NATIVE] + COMPATIBLE, ids=_NATIVE_IDS + _COMPATIBLE_IDS)
def test_false_sends_the_same_body_as_omitting_it(api, model):
    # 既定は頼まない。False を渡しても、渡さないときと同じ本体になる
    ok = _OK_BY_PROVIDER.get(model.split(":")[0], _CHAT_OK)
    api.reply_json(ok)
    api.reply_json(ok)
    client = api.client(model)
    client.chat("hi")
    client.chat("hi", reasoning_summary=False)
    assert api.body(0) == api.body(1)
    body = api.body(1)
    assert "thinking" not in body and "reasoning" not in body
    assert "thinkingConfig" not in (body.get("generationConfig") or {})


@pytest.mark.parametrize("model", COMPATIBLE, ids=_COMPATIBLE_IDS)
def test_compatible_servers_get_nothing_extra_and_reasoning_text_comes_back(api, model):
    # Chat Completions には要約を頼む標準の指定がない。何も足さず、返ってきた reasoning_content を入れる
    api.reply_json(_CHAT_OK)
    api.reply_json(_CHAT_OK)
    client = api.client(model)
    client.chat("hi")
    res = client.chat("hi", reasoning_summary=True)
    assert api.body(0) == api.body(1)
    assert res.thinking == "考えた"


def test_openai_chat_completions_is_rejected_before_sending(api):
    # 本物の OpenAI の Chat Completions は推論の本文を返さない。黙って None にせず、Responses API を案内する
    with pytest.raises(UnsupportedFeatureError, match="'openai:'"):
        api.client("openai-chat:gpt-5.5").chat("hi", reasoning_summary=True)
    with pytest.raises(UnsupportedFeatureError, match="'openai:'"):
        list(api.client("openai-chat:gpt-5.5").stream("hi", reasoning_summary=True))
    assert api.requests == []


def test_openai_chat_preset_pointed_at_another_server_is_allowed(api):
    # 止めるのは OpenAI のサーバーの制約なので、プリセットの名前ではなく接続先で決める（OpenRouter など）
    api.reply_json(_CHAT_OK)
    client = api.client("openai-chat:some/model", base_url="https://openrouter.example/v1")
    res = client.chat("hi", reasoning_summary=True)
    assert "reasoning" not in api.body() and res.thinking == "考えた"


@pytest.mark.parametrize("model", ALL_MODELS)
def test_none_effort_with_summary_is_rejected_before_sending(api, model):
    # 推論を切って本文を頼むのは矛盾する（Anthropic では display が type: disabled で無効）
    with pytest.raises(ConfigurationError, match="reasoning_summary.*reasoning_effort='none'"):
        api.client(model).chat("hi", reasoning_effort="none", reasoning_summary=True)
    assert api.requests == []


@pytest.mark.parametrize("bad", [None, 1, 0, "true", "auto", {"summary": "auto"}])
def test_non_bool_is_type_error(api, bad):
    # 段階（OpenAI の concise・detailed）は受け付けない。ほかの社では送れず、切り替えを妨げるため
    with pytest.raises(TypeError, match="reasoning_summary must be True or False"):
        api.client("openai:gpt-5.5").chat("hi", reasoning_summary=bad)
    assert api.requests == []


@pytest.mark.parametrize(
    ("model", "ok", "pick", "expected"),
    [
        ("openai:gpt-5.5", _RESPONSES_OK, lambda b: b["reasoning"], {"effort": "high", "summary": "auto"}),
        (
            "anthropic:claude-opus-4-8",
            _ANTHROPIC_OK,
            lambda b: (b["thinking"], b["output_config"]),
            ({"type": "adaptive", "display": "summarized"}, {"effort": "high"}),
        ),
        ("gemini:gemini-flash-latest", _GEMINI_OK, _gemini_thinking, {"thinkingLevel": "high", "includeThoughts": True}),
        ("deepseek:deepseek-flash", _CHAT_OK, lambda b: b.get("reasoning_effort"), "high"),
    ],
    ids=["openai", "anthropic", "gemini", "deepseek"],
)
def test_summary_sits_beside_effort(api, model, ok, pick, expected):
    api.reply_json(ok)
    api.client(model).chat("hi", reasoning_effort="high", reasoning_summary=True)
    assert pick(api.body()) == expected


def test_anthropic_summary_sits_beside_structured_output(api):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    api.reply_json({**_ANTHROPIC_OK, "content": [{"type": "text", "text": '{"a": "x"}'}]})
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi", reasoning_summary=True, response_schema=schema)
    body = api.body()
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert res.parsed == {"a": "x"}


@pytest.mark.parametrize(
    ("model", "ok", "extra", "pick", "expected"),
    [
        # 段階を決めたいときは extra= で渡す。同じ場所なら extra= が勝つ（いつもの深いマージ）
        ("openai:gpt-5.5", _RESPONSES_OK, {"reasoning": {"summary": "detailed"}}, lambda b: b["reasoning"], {"summary": "detailed"}),
        # Anthropic 4.5 系は adaptive を 400 にするので、enabled を extra= で渡す。display は enabled でも使える
        (
            "anthropic:claude-sonnet-4-5",
            _ANTHROPIC_OK,
            {"thinking": {"type": "enabled", "budget_tokens": 2048}},
            lambda b: b["thinking"],
            {"type": "enabled", "budget_tokens": 2048, "display": "summarized"},
        ),
        (
            "gemini:gemini-flash-latest",
            _GEMINI_OK,
            {"generationConfig": {"thinkingConfig": {"includeThoughts": False}}},
            _gemini_thinking,
            {"includeThoughts": False},
        ),
    ],
    ids=["openai-detailed", "anthropic-4-5-enabled", "gemini-extra-wins"],
)
def test_extra_adds_to_or_overrides_summary(api, model, ok, extra, pick, expected):
    api.reply_json(ok)
    api.client(model).chat("hi", reasoning_summary=True, extra=extra)
    assert pick(api.body()) == expected
