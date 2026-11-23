"""モデル文字列を変えても、同じコードが同じように振る舞うこと（基本方針の優先順位 1）。

同じテストを4つのアダプターで回す。アダプターごとの細かい形式は tests/test_<adapter>.py にある。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from sample_tools import WEATHER_TOOL
from switchllm import ConfigurationError, StreamError


@dataclass
class Case:
    model: str
    ok: dict[str, Any]  # 「hi」とだけ返す非ストリーミングの応答
    has_tool_choice: Callable[[dict[str, Any]], bool]
    text_stream: Callable[[str], list[tuple[str | None, Any]]]  # 受け取った本文を返して正しく終わるストリーム
    truncated_stream: list[tuple[str | None, Any]]  # 「途中」まで送って、完了の合図なしに閉じる
    error_stream: list[tuple[str | None, Any]]  # 途中で「boom」というエラーイベントが届く
    unknown_event: tuple[str | None, Any]  # 将来足されるかもしれない、知らないイベント
    common_params: Callable[[dict[str, Any]], tuple[Any, Any, Any]]  # 本体から (max_tokens, temperature, system) を取り出す
    has_stop: Callable[[dict[str, Any]], bool]  # 本体に stop の指定があるか
    auth_header: str  # APIキーを入れるヘッダー
    # temperature を送るときのクライアントのオプション。Anthropic の既定の版（2026-10-21）は temperature を
    # 送る前に UnsupportedFeatureError にするので、古い版を選ぶ（docs/design-docs/anthropic-api-version.md）
    temperature_options: dict[str, Any] = field(default_factory=dict)


def _anthropic_text(text: str) -> list[tuple[str | None, Any]]:
    return [
        ("message_start", {"type": "message_start", "message": {"content": [], "usage": {}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
    ]


def _anthropic_end() -> list[tuple[str | None, Any]]:
    return [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def _responses_ok(text: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {},
    }


_RESPONSES_OK = _responses_ok("hi")

CASES = [
    Case(
        model="openai:gpt-5.5",
        ok=_RESPONSES_OK,
        has_tool_choice=lambda body: "tool_choice" in body,
        text_stream=lambda text: [
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": text}),
            ("response.completed", {"type": "response.completed", "response": _responses_ok(text)}),
        ],
        truncated_stream=[("response.output_text.delta", {"type": "response.output_text.delta", "delta": "途中"})],
        error_stream=[
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "途中"}),
            ("response.failed", {"type": "response.failed", "response": {"error": {"message": "boom"}}}),
        ],
        unknown_event=("response.something_new", {"type": "response.something_new"}),
        common_params=lambda b: (b["max_output_tokens"], b["temperature"], b["instructions"]),
        has_stop=lambda b: "stop" in b,
        auth_header="Authorization",
    ),
    Case(
        model="openai-chat:gpt-x",
        ok={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
        has_tool_choice=lambda body: "tool_choice" in body,
        text_stream=lambda text: [
            (None, {"choices": [{"delta": {"content": text}}]}),
            (None, {"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            (None, "[DONE]"),
        ],
        truncated_stream=[(None, {"choices": [{"delta": {"content": "途中"}}]})],
        error_stream=[(None, {"choices": [{"delta": {"content": "途中"}}]}), (None, {"error": {"message": "boom"}})],
        unknown_event=(None, {"object": "chat.completion.chunk", "choices": [{"delta": {"something_new": 1}}]}),
        # 接続先が OpenAI 本家なので max_completion_tokens（S11）
        common_params=lambda b: (b["max_completion_tokens"], b["temperature"], b["messages"][0]["content"]),
        has_stop=lambda b: "stop" in b,
        auth_header="Authorization",
    ),
    Case(
        model="anthropic:claude-sonnet-5-5",
        ok={"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "usage": {}},
        has_tool_choice=lambda body: "tool_choice" in body,
        text_stream=lambda text: [*_anthropic_text(text), *_anthropic_end()],
        truncated_stream=_anthropic_text("途中"),
        error_stream=[*_anthropic_text("途中"), ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "boom"}})],
        unknown_event=("something_new", {"type": "something_new"}),
        common_params=lambda b: (b["max_tokens"], b["temperature"], b["system"]),
        has_stop=lambda b: "stop_sequences" in b,
        auth_header="x-api-key",
        temperature_options={"anthropic_version": "2023-06-01"},
    ),
    Case(
        model="gemini:gemini-flash-latest",
        ok={"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]},
        has_tool_choice=lambda body: "toolConfig" in body,
        text_stream=lambda text: [(None, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})],
        truncated_stream=[(None, {"candidates": [{"content": {"parts": [{"text": "途中"}]}}]})],
        error_stream=[
            (None, {"candidates": [{"content": {"parts": [{"text": "途中"}]}}]}),
            (None, {"error": {"code": 500, "message": "boom", "status": "INTERNAL"}}),
        ],
        unknown_event=(None, {"somethingNew": {"x": 1}}),
        common_params=lambda b: (
            b["generationConfig"]["maxOutputTokens"],
            b["generationConfig"]["temperature"],
            b["systemInstruction"]["parts"][0]["text"],
        ),
        has_stop=lambda b: "stopSequences" in b["generationConfig"],
        auth_header="x-goog-api-key",
    ),
]

pytestmark = pytest.mark.parametrize("case", CASES, ids=[c.model.split(":")[0] for c in CASES])


def test_common_parameters_reach_each_apis_own_field(api, case):
    # 同じ呼び出しが、各社の形式のフィールドに入る（docs/references/index.md の対応表）
    api.reply_json(case.ok)
    api.client(case.model, **case.temperature_options).chat("hi", system="簡潔に", max_tokens=123, temperature=0.3)
    assert case.common_params(api.body()) == (123, 0.3, "簡潔に")


def test_zero_is_sent_and_empty_stop_means_none(api, case):
    # 境界値: 0 は有効な値（temperature の下限）なので、偽と見なして落とさずにそのまま送る。
    # max_tokens=0 は API が拒否する値だが、ライブラリは先回りして弾かない（API のエラーをそのまま返す）。
    # 空の stop は「指定なし」。OpenAI Responses でも UnsupportedFeatureError にしない
    api.reply_json(case.ok)
    api.client(case.model, **case.temperature_options).chat("hi", system="s", max_tokens=0, temperature=0, stop=[])
    body = api.body()
    assert case.common_params(body)[:2] == (0, 0)
    assert not case.has_stop(body)


def test_tool_choice_is_sent_only_when_given(api, case):
    # 4社とも省略時の既定は auto。Ollama は tool_choice 自体を受け付けないので、指定がなければ送らない
    api.reply_json(case.ok)
    api.reply_json(case.ok)
    client = api.client(case.model)
    client.chat("hi", tools=[WEATHER_TOOL])
    client.chat("hi", tools=[WEATHER_TOOL], tool_choice="auto")
    assert not case.has_tool_choice(api.body(0))
    assert case.has_tool_choice(api.body(1))


def test_stream_ends_with_one_done_matching_chat(api, case):
    api.reply_json(case.ok)
    api.reply_sse(case.text_stream("hi"))
    client = api.client(case.model)
    res = client.chat("hi")
    events = list(client.stream("hi"))
    assert [e.type for e in events].count("done") == 1 and events[-1].type == "done"
    assert "".join(e.text for e in events if e.type == "text") == "hi"
    assert (events[-1].response.text, events[-1].response.stop_reason) == (res.text, res.stop_reason) == ("hi", "end")


def test_streams_on_one_client_do_not_mix(api, case):
    # エージェントのループのように、同じクライアントで stream() を何度も呼ぶ。2本を交互に読んでも、
    # 読み終えたあとにもう1本読んでも、前のストリームの本文や終わりの合図が混ざらない
    for text in ("一つ目", "二つ目", "三つ目"):
        api.reply_sse(case.text_stream(text))
    client = api.client(case.model)
    first, second = [], []
    for a, b in itertools.zip_longest(client.stream("1"), client.stream("2")):
        first += [a] if a is not None else []
        second += [b] if b is not None else []
    third = list(client.stream("3"))
    for events, text in ((first, "一つ目"), (second, "二つ目"), (third, "三つ目")):
        assert [e.type for e in events] == ["text", "done"], [e.type for e in events]
        assert (events[0].text, events[-1].response.text) == (text, text)


def _header_values(request: Any, name: str) -> list[str]:
    """送ったヘッダーのうち、名前が同じもの（大文字と小文字を区別しない）の値をすべて返す。"""
    return [v.decode() for k, v in request.headers.raw if k.decode().lower() == name.lower()]


def test_headers_are_added_and_replace_the_librarys_own(api, case):
    # ゲートウェイを経由するときのヘッダーを、どのアダプターにも同じ書き方で渡せる。ライブラリが付ける
    # ヘッダー（認証など）と同じ名前なら、大文字と小文字が違っても置き換え、2本送らない（RFC 9110 の 5.1・5.3）
    api.reply_json(case.ok)
    api.reply_sse(case.text_stream("hi"))
    headers = {"X-Team": "a", case.auth_header.swapcase(): "gateway-key", "content-type": "application/json"}
    client = api.client(case.model, headers=headers)
    client.chat("hi")
    list(client.stream("hi"))
    for req in api.requests:
        assert _header_values(req, "x-team") == ["a"]
        assert _header_values(req, case.auth_header) == ["gateway-key"]  # プロバイダーのキー（test-key）は送らない
        assert _header_values(req, "content-type") == ["application/json"]


def test_auth_header_in_headers_needs_no_api_key(api, case, monkeypatch):
    # キーを持つのがゲートウェイで、利用者はゲートウェイのキーだけを持つとき
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    api.reply_json(case.ok)
    api.raw_client(case.model, headers={case.auth_header.upper(): "gateway-key"}).chat("hi")
    assert _header_values(api.requests[0], case.auth_header) == ["gateway-key"]
    with pytest.raises(ConfigurationError, match="API key"):  # 認証のヘッダーでなければ、今までどおりキーが要る
        api.raw_client(case.model, headers={"X-Team": "a"})


def test_none_in_headers_stops_the_librarys_header(api, case):
    # ゲートウェイがライブラリと違う名前の認証のヘッダーを求めるとき、プロバイダーのキーを送らない
    # （requests の None、公式 SDK の Omit() と同じ考え方。docs/design-docs/custom-headers.md）
    api.reply_json(case.ok)
    api.reply_sse(case.text_stream("hi"))
    headers = {case.auth_header.swapcase(): None, "X-Gateway-Key": "gateway-key", "X-Not-Set": None}
    client = api.client(case.model, api_key="provider-key", headers=headers)
    client.chat("hi")
    list(client.stream("hi"))
    for req in api.requests:
        assert _header_values(req, case.auth_header) == []
        assert _header_values(req, "x-gateway-key") == ["gateway-key"]
        assert _header_values(req, "x-not-set") == []  # ライブラリが付けないヘッダーなら、何も起きない
        assert not any(b"provider-key" in value for _, value in req.headers.raw)


def test_none_for_the_auth_header_needs_no_api_key(api, case, monkeypatch):
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    api.reply_json(case.ok)
    api.raw_client(case.model, headers={case.auth_header: None, "X-Gateway-Key": "gateway-key"}).chat("hi")
    assert _header_values(api.requests[0], case.auth_header) == []
    assert _header_values(api.requests[0], "x-gateway-key") == ["gateway-key"]


def test_truncated_stream_raises_instead_of_looking_complete(api, case):
    # 完了の合図なしに閉じたストリームを、正常終了の done として返さない
    api.reply_sse(case.truncated_stream)
    received = []
    with pytest.raises(StreamError, match="stream ended before") as e:
        for ev in api.client(case.model).stream("hi"):
            received.append(ev)
    assert [ev.text for ev in received] == ["途中"]  # それまでの差分は届いている
    assert e.value.provider == case.model.split(":")[0]


def test_error_event_raises_stream_error(api, case):
    api.reply_sse(case.error_stream)
    with pytest.raises(StreamError, match="boom") as e:
        list(api.client(case.model).stream("hi"))
    assert e.value.provider == case.model.split(":")[0] and e.value.body is not None


def test_unknown_stream_events_are_ignored(api, case):
    # プロバイダーは新しいイベントを足すので、知らないイベントで失敗しない（CLAUDE.md の不変条件 3）
    api.reply_sse([case.unknown_event, *case.text_stream("hi")])
    events = list(api.client(case.model).stream("hi"))
    assert events[-1].type == "done" and events[-1].response.text == "hi"
