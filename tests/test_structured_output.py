"""構造化出力（``response_schema=``）。同じ呼び出しが、各社の形式の指定になり、同じ ``parsed`` を返すこと。

判断は docs/design-docs/structured-output.md、各社の形式は docs/references/ の「構造化出力」。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from sample_tools import WEATHER_TOOL
from switchllm import ConfigurationError, UnsupportedFeatureError, register_provider
from switchllm.providers import PRESETS

PERSON = {
    "title": "Person",
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
}
# 送るときは、プロパティを並べたオブジェクトに additionalProperties: false を足す
PERSON_SENT = {**PERSON, "additionalProperties": False}
ANSWER = '{"name": "タロウ", "age": 20}'
PARSED = {"name": "タロウ", "age": 20}
FRAGMENTS = ['{"name": "タロウ", ', '"age": 20}']

Events = list[tuple[str | None, Any]]


def _responses_ok(text: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {},
    }


def _responses_stream(parts: list[str]) -> Events:
    deltas: Events = [("response.output_text.delta", {"type": "response.output_text.delta", "delta": p}) for p in parts]
    done = ("response.completed", {"type": "response.completed", "response": _responses_ok("".join(parts))})
    return [*deltas, done]


def _chat_ok(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def _chat_stream(parts: list[str]) -> Events:
    chunks: Events = [(None, {"choices": [{"delta": {"content": p}}]}) for p in parts]
    return [*chunks, (None, {"choices": [{"delta": {}, "finish_reason": "stop"}]}), (None, "[DONE]")]


def _anthropic_ok(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "usage": {}}


def _anthropic_stream(parts: list[str]) -> Events:
    start: Events = [
        ("message_start", {"type": "message_start", "message": {"content": [], "usage": {}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ]
    deltas: Events = [
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}})
        for p in parts
    ]
    end: Events = [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return [*start, *deltas, *end]


def _gemini_ok(text: str, finish: str = "STOP") -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def _gemini_stream(parts: list[str]) -> Events:
    chunks: Events = [(None, {"candidates": [{"content": {"parts": [{"text": p}]}}]}) for p in parts[:-1]]
    return [*chunks, (None, _gemini_ok(parts[-1]))]


def _chat_format(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


@dataclass
class Case:
    model: str
    ok: Callable[[str], dict[str, Any]]  # 本文を返す、非ストリーミングの応答
    stream: Callable[[list[str]], Events]  # 断片を返して正しく終わるストリーム
    sent: Callable[[dict[str, Any]], Any]  # 本体から、構造化出力の指定を取り出す
    expected: Callable[[dict[str, Any], str], Any]  # 送ったスキーマと名前から、期待する指定
    absent: Callable[[dict[str, Any]], bool]  # 本体に構造化出力の指定がないか


CHAT = dict(
    ok=_chat_ok,
    stream=_chat_stream,
    sent=lambda b: b["response_format"],
    expected=_chat_format,
    absent=lambda b: "response_format" not in b,
)

CASES = [
    Case(
        model="openai:gpt-5.5",
        ok=_responses_ok,
        stream=_responses_stream,
        sent=lambda b: b["text"]["format"],
        expected=lambda s, n: {"type": "json_schema", "name": n, "schema": s, "strict": True},
        absent=lambda b: "text" not in b,
    ),
    Case(model="openai-chat:gpt-x", **CHAT),
    # Ollama・LM Studio にも OpenAI と同じ形で送る（U21）
    Case(model="ollama:llama3.2", **CHAT),
    Case(model="lmstudio:qwen3", **CHAT),
    Case(
        model="anthropic:claude-sonnet-5-5",
        ok=_anthropic_ok,
        stream=_anthropic_stream,
        sent=lambda b: b["output_config"],
        expected=lambda s, n: {"format": {"type": "json_schema", "schema": s}},  # Anthropic には name がない
        absent=lambda b: "output_config" not in b,
    ),
    Case(
        model="gemini:gemini-flash-latest",
        ok=_gemini_ok,
        stream=_gemini_stream,
        sent=lambda b: (b["generationConfig"]["responseMimeType"], b["generationConfig"]["responseJsonSchema"]),
        expected=lambda s, n: ("application/json", s),
        absent=lambda b: not {"responseMimeType", "responseJsonSchema"} & set(b.get("generationConfig", {})),
    ),
]

each_adapter = pytest.mark.parametrize("case", CASES, ids=[c.model.split(":")[0] for c in CASES])


# --------------------------------------------------------------------------- #
# 4つのアダプターで同じ振る舞い
# --------------------------------------------------------------------------- #
@each_adapter
def test_schema_goes_to_each_apis_own_field_and_parsed_is_filled(api, case):
    api.reply_json(case.ok(ANSWER))
    res = api.client(case.model).chat("タロウ、20歳", response_schema=PERSON)
    assert case.sent(api.body()) == case.expected(PERSON_SENT, "Person")
    assert res.parsed == PARSED
    assert res.text == ANSWER  # JSON の文字列も残る


@each_adapter
def test_without_schema_nothing_is_sent_and_parsed_stays_none(api, case):
    # 本文が JSON として読めても、response_schema= を指定しなければ読まない
    api.reply_json(case.ok(ANSWER))
    res = api.client(case.model).chat("hi")
    assert case.absent(api.body())
    assert res.parsed is None


@each_adapter
def test_stream_done_has_the_same_parsed_as_chat(api, case):
    # text イベントは JSON の断片。最後の done の Response は chat() と同じ（不変条件 2）
    api.reply_sse(case.stream(FRAGMENTS))
    events = list(api.client(case.model).stream("タロウ、20歳", response_schema=PERSON))
    assert case.sent(api.body()) == case.expected(PERSON_SENT, "Person")
    assert "".join(e.text for e in events if e.type == "text") == ANSWER
    assert [e.type for e in events][-1] == "done"
    assert events[-1].response is not None and events[-1].response.parsed == PARSED


@each_adapter
def test_text_that_is_not_json_gives_parsed_none(api, case):
    api.reply_json(case.ok("JSON ではない答え"))
    res = api.client(case.model).chat("hi", response_schema=PERSON)
    assert res.parsed is None
    assert res.text == "JSON ではない答え"


# --------------------------------------------------------------------------- #
# parsed を入れないとき
# --------------------------------------------------------------------------- #
def test_refusal_gives_parsed_none(api):
    # OpenAI の拒否は refusal の content で、status は completed のまま
    api.reply_json(
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "お答えできません"}]}],
            "usage": {},
        }
    )
    res = api.client("openai:gpt-5.5").chat("hi", response_schema=PERSON)
    assert res.stop_reason == "content_filter"
    assert res.parsed is None


def test_truncated_json_gives_parsed_none(api):
    api.reply_json(_anthropic_ok('{"name": "タ', stop_reason="max_tokens"))
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi", response_schema=PERSON)
    assert res.stop_reason == "max_tokens"
    assert res.parsed is None


def test_json_is_not_parsed_unless_the_model_finished(api):
    # 上限で止まった本文は、たまたま JSON として読めても、スキーマに合う保証がない
    api.reply_json(_gemini_ok('{"name": "タロウ"}', finish="MAX_TOKENS"))
    res = api.client("gemini:gemini-flash-latest").chat("hi", response_schema=PERSON)
    assert res.stop_reason == "max_tokens"
    assert res.parsed is None


def test_parsed_can_be_a_list_or_a_number(api):
    # ルートがオブジェクトでないスキーマを受け付ける接続先もある。読めた値はそのまま入れる
    api.reply_json(_anthropic_ok("[1, 2]"))
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi", response_schema={"type": "array", "items": {"type": "integer"}})
    assert res.parsed == [1, 2]


# --------------------------------------------------------------------------- #
# スキーマに足すもの
# --------------------------------------------------------------------------- #
def _sent_schema(api) -> dict[str, Any]:
    return api.body()["output_config"]["format"]["schema"]


def test_additional_properties_false_is_added_to_every_object_with_properties(api):
    obj = {"type": "object", "properties": {"x": {"type": "string"}}}
    schema = {
        "type": "object",
        "properties": {
            # プロパティの名前が properties や type でも、スキーマのキーワードと取り違えない
            "properties": copy.deepcopy(obj),
            "type": {"type": "string"},
            "list": {"type": "array", "items": copy.deepcopy(obj)},
            "tuple": {"type": "array", "prefixItems": [copy.deepcopy(obj)]},
            "either": {"anyOf": [copy.deepcopy(obj), {"type": "null"}]},
            "both": {"allOf": [copy.deepcopy(obj)]},
            "one": {"oneOf": [copy.deepcopy(obj)]},
            "ref": {"$ref": "#/$defs/Item"},
            "nullable": {"type": ["object", "null"], "properties": {"x": {"type": "string"}}},
        },
        "$defs": {"Item": copy.deepcopy(obj)},
        "definitions": {"Old": copy.deepcopy(obj)},
    }
    original = copy.deepcopy(schema)
    api.reply_json(_anthropic_ok("{}"))
    api.client("anthropic:claude-sonnet-5-5").chat("hi", response_schema=schema)
    sent = _sent_schema(api)
    props = sent["properties"]
    assert sent["additionalProperties"] is False
    assert props["properties"]["additionalProperties"] is False
    assert "additionalProperties" not in props["type"]
    assert props["list"]["items"]["additionalProperties"] is False
    assert props["tuple"]["prefixItems"][0]["additionalProperties"] is False
    assert props["either"]["anyOf"][0]["additionalProperties"] is False
    assert props["either"]["anyOf"][1] == {"type": "null"}
    assert props["both"]["allOf"][0]["additionalProperties"] is False
    assert props["one"]["oneOf"][0]["additionalProperties"] is False
    assert props["nullable"]["additionalProperties"] is False
    assert sent["$defs"]["Item"]["additionalProperties"] is False
    assert sent["definitions"]["Old"]["additionalProperties"] is False
    assert schema == original  # 利用者の dict は書き換えない


def test_objects_without_properties_and_explicit_additional_properties_are_left_alone(api):
    # キーを決めない dict に false を足すと {} しか返せなくなるので、足さない（OpenAI では 400 のままで気づける）。
    # 書いてある additionalProperties（true やスキーマ）は、中身も含めて変えない
    schema = {
        "type": "object",
        "properties": {
            "free": {"type": "object"},
            "open": {"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": True},
            "map": {
                "type": "object",
                "additionalProperties": {"type": "object", "properties": {"y": {"type": "string"}}},
            },
        },
        "required": ["free"],
    }
    api.reply_json(_anthropic_ok("{}"))
    api.client("anthropic:claude-sonnet-5-5").chat("hi", response_schema=schema)
    props = _sent_schema(api)["properties"]
    assert props["free"] == {"type": "object"}
    assert props["open"]["additionalProperties"] is True
    assert props["map"]["additionalProperties"] == {"type": "object", "properties": {"y": {"type": "string"}}}
    assert _sent_schema(api)["required"] == ["free"]  # required は補わない


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("My-Schema_1", "My-Schema_1"),
        ("a" * 64, "a" * 64),  # 上限ちょうど
        ("a" * 65, "response"),  # OpenAI の name は 64 文字まで
        ("人物", "response"),  # 英数字・_・- だけ
        ("has space", "response"),
        ("", "response"),
        (123, "response"),
        (None, "response"),
    ],
)
def test_openai_name_comes_from_a_valid_title(api, title, name):
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    if title is not None:
        schema["title"] = title
    api.reply_json(_responses_ok("{}"))
    api.client("openai:gpt-5.5").chat("hi", response_schema=schema)
    assert api.body()["text"]["format"]["name"] == name


def test_extra_overrides_the_generated_format(api):
    # extra= はいつもどおり最後に深くマージする
    api.reply_json(_responses_ok(ANSWER))
    api.client("openai:gpt-5.5").chat("hi", response_schema=PERSON, extra={"text": {"format": {"strict": False}}})
    fmt = api.body()["text"]["format"]
    assert fmt["strict"] is False and fmt["schema"] == PERSON_SENT


def test_gemini_keeps_the_other_generation_config(api):
    api.reply_json(_gemini_ok(ANSWER))
    api.client("gemini:gemini-flash-latest").chat("hi", response_schema=PERSON, max_tokens=100, temperature=0)
    gen = api.body()["generationConfig"]
    assert gen["maxOutputTokens"] == 100 and gen["temperature"] == 0
    assert gen["responseMimeType"] == "application/json"


def test_tools_and_schema_are_sent_together(api):
    # 併用を受け付けないモデルは API のエラーをそのまま返す。先回りして弾かない（U22）
    api.reply_json(_responses_ok(ANSWER))
    api.client("openai:gpt-5.5").chat("hi", tools=[WEATHER_TOOL], response_schema=PERSON)
    body = api.body()
    assert body["tools"][0]["name"] == WEATHER_TOOL["name"]
    assert body["tools"][0]["parameters"] == WEATHER_TOOL["parameters"]  # ツールのスキーマには足さない
    assert body["text"]["format"]["schema"] == PERSON_SENT


# --------------------------------------------------------------------------- #
# JSON Schema に対応しない接続先と、引数の誤り
# --------------------------------------------------------------------------- #
def test_deepseek_rejects_the_schema_before_sending(api):
    client = api.client("deepseek:deepseek-flash")
    with pytest.raises(UnsupportedFeatureError, match="json_object"):
        client.chat("hi", response_schema=PERSON)
    with pytest.raises(UnsupportedFeatureError, match="json_object"):
        next(client.stream("hi", response_schema=PERSON))
    assert api.requests == []  # 送らない（料金を払う前に知らせる）


def test_deepseek_without_schema_still_works(api):
    api.reply_json(_chat_ok("hi"))
    assert api.client("deepseek:deepseek-flash").chat("hi").text == "hi"
    assert "response_format" not in api.body()


def test_json_schema_option_turns_support_on_and_off(api):
    # DeepSeek が対応したら json_schema=True で送れる。対応しないサーバーは登録のときに False にできる
    api.reply_json(_chat_ok(ANSWER))
    res = api.client("deepseek:deepseek-flash", json_schema=True).chat("hi", response_schema=PERSON)
    assert api.body()["response_format"] == _chat_format(PERSON_SENT, "Person")
    assert res.parsed == PARSED

    register_provider("nojson", base_url="http://x/v1", api_key_env=(), json_schema=False)
    try:
        with pytest.raises(UnsupportedFeatureError):
            api.client("nojson:m", api_key=None).chat("hi", response_schema=PERSON)
    finally:
        PRESETS.pop("nojson")


def test_json_schema_option_belongs_to_chat_completions_only(api):
    with pytest.raises(ConfigurationError, match="json_schema"):
        api.client("anthropic:claude-sonnet-5-5", json_schema=False)


@pytest.mark.parametrize("bad", ['{"type": "object"}', ["type", "object"], True])
def test_response_schema_must_be_a_dict(api, bad):
    with pytest.raises(TypeError, match="response_schema must be a JSON Schema dict"):
        api.client("openai:gpt-5.5").chat("hi", response_schema=bad)
    assert api.requests == []


def test_empty_schema_dict_is_sent_as_is(api):
    # 空の dict も JSON Schema としては有効（何でもよい）。受け付けるかは API に任せる
    api.reply_json(_anthropic_ok(ANSWER))
    res = api.client("anthropic:claude-sonnet-5-5").chat("hi", response_schema={})
    assert _sent_schema(api) == {}
    assert res.parsed == PARSED
