"""推論の強さ（``reasoning_effort=``）。同じ呼び出しが、各社の形式の指定になること。

判断は docs/design-docs/reasoning-effort.md、各社の形式は docs/references/ の「推論の強さ」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest

from switchllm import BadRequestError, ConfigurationError, UnsupportedFeatureError

LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

_RESPONSES_OK = {
    "status": "completed",
    "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
    "usage": {},
}
_CHAT_OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
_ANTHROPIC_OK = {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "usage": {}}
_GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}


@dataclass
class Case:
    model: str
    ok: dict[str, Any]
    effort: Callable[[dict[str, Any]], Any]  # 本体から、送った段階を取り出す（なければ None）
    unsupported: tuple[str, ...]  # 各社の API に名前がなく、送る前に例外にする段階


def _chat_effort(b: dict[str, Any]) -> Any:
    return b.get("reasoning_effort")


CASES = [
    Case("openai:gpt-5.5", _RESPONSES_OK, lambda b: (b.get("reasoning") or {}).get("effort"), ()),
    Case("openai-chat:gpt-5.5", _CHAT_OK, _chat_effort, ()),
    # DeepSeek と Ollama は、文書にない段階を API の側で近い段階に置き換える。switchllm は名前を変えない
    Case("deepseek:deepseek-flash", _CHAT_OK, _chat_effort, ()),
    Case("ollama:qwen3", _CHAT_OK, _chat_effort, ()),
    Case("anthropic:claude-sonnet-5-5", _ANTHROPIC_OK, lambda b: (b.get("output_config") or {}).get("effort"), ("minimal",)),
    Case(
        "gemini:gemini-flash-latest",
        _GEMINI_OK,
        lambda b: ((b.get("generationConfig") or {}).get("thinkingConfig") or {}).get("thinkingLevel"),
        ("none", "max"),
    ),
]
_IDS = [c.model.split(":")[0] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=_IDS)
def test_effort_reaches_each_apis_own_field(api, case):
    api.reply_json(case.ok)
    api.client(case.model).chat("hi", reasoning_effort="high")
    assert case.effort(api.body()) == "high"


@pytest.mark.parametrize("case", CASES, ids=_IDS)
def test_stream_sends_the_same_field(api, case):
    # 応答は読まずに、送ったリクエストだけを見る（ストリームの組み立ては各アダプターのテストにある）
    api.reply_json({"error": {"message": "stop here"}}, status=400)
    with pytest.raises(BadRequestError):
        list(api.client(case.model, max_retries=0).stream("hi", reasoning_effort="low"))
    assert case.effort(api.body()) == "low"


@pytest.mark.parametrize("case", CASES, ids=_IDS)
def test_nothing_is_sent_without_effort(api, case):
    # 指定がなければ、各モデルの既定の段階に任せる。推論の指定を何も足さない
    api.reply_json(case.ok)
    api.client(case.model).chat("hi")
    body = api.body()
    assert case.effort(body) is None
    assert "thinking" not in body and "output_config" not in body and "reasoning" not in body
    assert "thinkingConfig" not in (body.get("generationConfig") or {})


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("case", CASES, ids=_IDS)
def test_level_names_are_sent_unchanged_or_rejected_before_sending(api, case, level):
    # 段階の名前は置き換えない。各社の API に名前のない段階は、送らずに UnsupportedFeatureError
    if level in case.unsupported:
        with pytest.raises(UnsupportedFeatureError, match=repr(level)):
            api.client(case.model).chat("hi", reasoning_effort=level)
        assert api.requests == []
        return
    api.reply_json(case.ok)
    api.client(case.model).chat("hi", reasoning_effort=level)
    body = api.body()
    if case.model.startswith("anthropic:") and level == "none":
        # effort に none はないので thinking を切る（下の test_anthropic_none_turns_thinking_off）
        assert case.effort(body) is None and body["thinking"] == {"type": "disabled"}
    else:
        assert case.effort(body) == level


def test_anthropic_none_turns_thinking_off(api):
    # Anthropic の effort に none はないが、thinking を切れば同じ意味になる。effort は送らない
    # （切れないモデルは API のエラーをそのまま返す）
    api.reply_json(_ANTHROPIC_OK)
    api.client("anthropic:claude-haiku-5-5").chat("hi", reasoning_effort="none")
    body = api.body()
    assert body["thinking"] == {"type": "disabled"}
    assert "output_config" not in body


def test_unsupported_level_message_suggests_alternatives(api):
    with pytest.raises(UnsupportedFeatureError, match="'low'"):
        api.client("anthropic:claude-sonnet-5-5").chat("hi", reasoning_effort="minimal")
    with pytest.raises(UnsupportedFeatureError, match="'minimal'.*thinkingBudget"):
        api.client("gemini:gemini-flash-latest").chat("hi", reasoning_effort="none")
    with pytest.raises(UnsupportedFeatureError, match="'xhigh'"):
        api.client("gemini:gemini-flash-latest").chat("hi", reasoning_effort="max")
    assert api.requests == []


@pytest.mark.parametrize("bad", ["hgih", "HIGH", " high", "", "ultra"])
def test_unknown_level_is_rejected_before_sending(api, bad):
    # 7つ以外は、打ち間違いとして送る前に知らせる（大文字や空白も直さない）
    with pytest.raises(ConfigurationError, match="none, minimal, low, medium, high, xhigh, max"):
        api.client("openai:gpt-5.5").chat("hi", reasoning_effort=bad)
    assert api.requests == []


@pytest.mark.parametrize("bad", [3, True, {"effort": "high"}])
def test_non_string_level_is_type_error(api, bad):
    with pytest.raises(TypeError, match="reasoning_effort must be one of"):
        api.client("openai:gpt-5.5").chat("hi", reasoning_effort=bad)
    assert api.requests == []


SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def test_anthropic_effort_sits_beside_structured_output_format(api):
    api.reply_json({**_ANTHROPIC_OK, "content": [{"type": "text", "text": '{"a": "x"}'}]})
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi", reasoning_effort="max", response_schema=SCHEMA)
    out = api.body()["output_config"]
    assert out["effort"] == "max"
    assert out["format"]["type"] == "json_schema"
    assert res.parsed == {"a": "x"}


def test_gemini_thinking_config_sits_beside_other_generation_config(api):
    api.reply_json(_GEMINI_OK)
    api.client("gemini:gemini-flash-latest").chat("hi", reasoning_effort="low", max_tokens=100, response_schema=SCHEMA)
    gen = api.body()["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}
    assert gen["maxOutputTokens"] == 100 and gen["responseMimeType"] == "application/json"


@pytest.mark.parametrize(
    ("model", "ok", "extra", "pick", "expected"),
    [
        # 共通化しない推論の指定は、extra= で足せる（深いマージなので reasoning_effort= と並ぶ）。
        # 要約は reasoning_summary=True でも頼める（tests/test_reasoning_summary.py）
        ("openai:gpt-5.5", _RESPONSES_OK, {"reasoning": {"summary": "auto"}}, lambda b: b["reasoning"], {"effort": "high", "summary": "auto"}),
        (
            "anthropic:claude-opus-4-8",
            _ANTHROPIC_OK,
            {"thinking": {"type": "adaptive", "display": "summarized"}},
            lambda b: (b["thinking"], b["output_config"]),
            ({"type": "adaptive", "display": "summarized"}, {"effort": "high"}),
        ),
        (
            "gemini:gemini-flash-latest",
            _GEMINI_OK,
            {"generationConfig": {"thinkingConfig": {"includeThoughts": True}}},
            lambda b: b["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "high", "includeThoughts": True},
        ),
        # 同じ場所を extra= で指定したら extra= が勝つ（いつもの深いマージ）
        ("openai-chat:gpt-5.5", _CHAT_OK, {"reasoning_effort": "low"}, lambda b: b["reasoning_effort"], "low"),
    ],
    ids=["openai-summary", "anthropic-adaptive", "gemini-include-thoughts", "extra-wins"],
)
def test_extra_adds_to_or_overrides_effort(api, model, ok, extra, pick, expected):
    api.reply_json(ok)
    api.client(model).chat("hi", reasoning_effort="high", extra=extra)
    assert pick(api.body()) == expected
