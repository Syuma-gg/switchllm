"""API が断った指定に、原因とみられる引数と、どのモデルでも動く書き方を足す（docs/design-docs/error-hints.md）。

モデル名は見ない。送った共通の引数と、その各社でのフィールド名がエラーの本文に出てくるかで判断する。
"""

import pytest

from sample_tools import WEATHER_TOOL
from switchllm import BadRequestError, ServerError
from switchllm.providers import ADAPTERS
from switchllm.providers.base import ERROR_HINTS

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def anthropic_error(message: str) -> dict:
    return {"type": "error", "error": {"type": "invalid_request_error", "message": message}, "request_id": "req_1"}


def openai_error(message: str, param: str | None = None) -> dict:
    return {"error": {"message": message, "type": "invalid_request_error", "param": param, "code": None}}


def gemini_error(message: str) -> dict:
    return {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}}


# (モデル, chat() の引数, エラーの本文, 案内が付く引数)。文言は各社の文書の例か、それに倣ったもの
CASES = [
    pytest.param(
        "anthropic:claude-sonnet-5-5",
        {"tools": [WEATHER_TOOL], "tool_choice": "required"},
        anthropic_error('tool_choice: type "tool" and "any" are not supported for this model.'),  # Errors の文書の文言
        "tool_choice",
        id="anthropic-forced-tool-choice",
    ),
    pytest.param(
        "anthropic:claude-opus-5-5",
        {"reasoning_effort": "none"},
        anthropic_error(
            '"thinking.type.disabled" is not supported for this model. '
            'Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.'
        ),
        "reasoning_effort",
        id="anthropic-effort-none",
    ),
    pytest.param(
        "anthropic:claude-sonnet-4-5",
        {"reasoning_summary": True},
        anthropic_error("adaptive thinking is not supported on this model"),  # Errors の文書の文言
        "reasoning_summary",
        id="anthropic-summary-on-4-5",
    ),
    pytest.param(
        "anthropic:claude-sonnet-5-5",
        {"response_schema": SCHEMA},
        anthropic_error("output_config.format.schema: 'minLength' is not supported"),
        "response_schema",
        id="anthropic-schema",
    ),
    pytest.param(
        "openai:gpt-5.5",
        {"reasoning_effort": "max"},
        openai_error("Unsupported value: 'max' is not supported with this model.", param="reasoning.effort"),  # param だけに出る
        "reasoning_effort",
        id="openai-effort-param",
    ),
    pytest.param(
        "openai:gpt-5.5",
        {"temperature": 0.2},
        openai_error("Unsupported parameter: 'temperature' is not supported with this model.", param="temperature"),
        "temperature",
        id="openai-temperature",
    ),
    pytest.param(
        "openai:gpt-5.5",
        {"reasoning_summary": True},
        openai_error("Your organization must be verified to generate reasoning summaries.", param="reasoning.summary"),
        "reasoning_summary",
        id="openai-summary",
    ),
    pytest.param(
        "openai:gpt-5.5",
        {"response_schema": SCHEMA},
        openai_error("Invalid schema for response_format 'Weather': ...", param="text.format.schema"),
        "response_schema",
        id="openai-schema",
    ),
    pytest.param(
        "deepseek:deepseek-v4-pro",
        {"tools": [WEATHER_TOOL], "tool_choice": {"name": "get_weather"}},
        openai_error("tool_choice is not supported in thinking mode"),
        "tool_choice",
        id="deepseek-named-tool-choice",
    ),
    pytest.param(
        "lmstudio:qwen3",
        {"reasoning_effort": "xhigh"},
        openai_error("Invalid reasoning_effort: xhigh"),
        "reasoning_effort",
        id="compatible-effort",
    ),
    pytest.param(
        "gemini:gemini-2.5-flash",
        {"reasoning_effort": "low"},
        gemini_error("thinking_level is not supported for this model."),  # Gemini の文書は snake_case で書く
        "reasoning_effort",
        id="gemini-thinking-level",
    ),
    pytest.param(
        "gemini:gemini-flash-latest",
        {"tools": [WEATHER_TOOL], "tool_choice": "required"},
        gemini_error("Invalid value at 'tool_config.function_calling_config.mode'"),
        "tool_choice",
        id="gemini-tool-config",
    ),
    pytest.param(
        "gemini:gemini-flash-latest",
        {"reasoning_summary": True},
        gemini_error('Invalid JSON payload received. Unknown name "includeThoughts"'),  # camelCase でも見つける
        "reasoning_summary",
        id="gemini-include-thoughts",
    ),
    pytest.param(
        "gemini:gemini-flash-latest",
        {"response_schema": SCHEMA},
        gemini_error("response_json_schema: unsupported keyword"),
        "response_schema",
        id="gemini-schema",
    ),
]


@pytest.mark.parametrize(("model", "kwargs", "body", "arg"), CASES)
def test_rejected_argument_gets_a_hint(api, model, kwargs, body, arg):
    api.reply_json(body, status=400)
    with pytest.raises(BadRequestError) as info:
        api.client(model).chat("hi", **kwargs)
    e = info.value
    assert f"\nHint ({arg}): " in str(e)
    assert str(e).count("\nHint (") == 1  # ほかの引数の案内は付けない
    assert "\nHint" not in e.message  # e.message は API の文言のまま
    assert str(e).startswith(f"[{model.split(':')[0]}] {e.message}")


def test_hint_is_added_for_stream_too(api):
    api.reply_json(anthropic_error('tool_choice: type "tool" and "any" are not supported for this model.'), status=400)
    with pytest.raises(BadRequestError, match=r"\nHint \(tool_choice\): "):
        list(api.client("anthropic:claude-sonnet-5-5").stream("hi", tools=[WEATHER_TOOL], tool_choice="required"))


def test_hint_text_points_to_a_portable_way(api):
    # 案内は、どのモデルでも動く書き方を示す（何をすればよいかが分かる）
    api.reply_json(anthropic_error('tool_choice: type "tool" and "any" are not supported for this model.'), status=400)
    with pytest.raises(BadRequestError) as info:
        api.client("anthropic:claude-sonnet-5-5").chat("hi", tools=[WEATHER_TOOL], tool_choice={"name": "get_weather"})
    hint = str(info.value).split("\nHint (tool_choice): ", 1)[1]
    assert hint == ERROR_HINTS["tool_choice"]
    assert "leave tool_choice unset" in hint


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        # tool_choice を強制していない（auto・none）ので、tool_choice が原因ではない
        ({"tools": [WEATHER_TOOL]}, 'tool_choice: type "tool" and "any" are not supported for this model.'),
        ({"tools": [WEATHER_TOOL], "tool_choice": "none"}, "tool_choice: something else is wrong"),
        # 強制したが、エラーは別のこと
        ({"tools": [WEATHER_TOOL], "tool_choice": "required"}, "messages.0.content: text content blocks must be non-empty"),
        # 指定していない引数の名前がエラーに出ても、案内しない
        ({}, '"thinking.type.disabled" is not supported for this model.'),
    ],
)
def test_no_hint_unless_the_sent_argument_is_named(api, kwargs, message):
    api.reply_json(anthropic_error(message), status=400)
    with pytest.raises(BadRequestError) as info:
        api.client("anthropic:claude-sonnet-5-5").chat("hi", **kwargs)
    assert "\nHint" not in str(info.value)


def test_no_hint_for_errors_other_than_bad_request(api):
    # 5xx は指定の誤りではないので、文言に引数の名前があっても案内しない
    api.reply_json(anthropic_error("tool_choice handling failed"), status=500)
    with pytest.raises(ServerError) as info:
        api.client("anthropic:claude-sonnet-5-5", max_retries=0).chat("hi", tools=[WEATHER_TOOL], tool_choice="required")
    assert "\nHint" not in str(info.value)


def test_several_hints_when_several_arguments_are_named(api):
    api.reply_json(
        openai_error("Unsupported parameter: 'temperature' is not supported with reasoning.effort 'max'."), status=400
    )
    with pytest.raises(BadRequestError) as info:
        api.client("openai:gpt-5.5").chat("hi", reasoning_effort="max", temperature=0.5)
    text = str(info.value)
    assert text.index("\nHint (reasoning_effort): ") < text.index("\nHint (temperature): ")  # 並びは ERROR_HINTS の順


def test_error_fields_cover_only_known_arguments_in_lowercase():
    # 本文は小文字にして比べるので、フィールド名も小文字で持つ。案内のない引数の名前は持たない
    for adapter, cls in ADAPTERS.items():
        assert set(cls.error_fields) <= set(ERROR_HINTS), adapter
        for arg, fields in cls.error_fields.items():
            assert fields and all(f == f.lower() for f in fields), (adapter, arg)


def test_e2e_rejection_note_lists_the_hinted_arguments():
    # E2E は、API が断ったときに、案内が付いた引数を記録する（U38 を実 API で確かめるため）
    from e2e_support import rejection

    e = BadRequestError("bad request", provider="anthropic", status_code=400)
    assert rejection(e) == "API が拒否した（案内: なし）: bad request"
    e.args = (str(e) + "\nHint (tool_choice): a\nHint (temperature): b",)
    assert rejection(e) == "API が拒否した（案内: tool_choice、temperature）: bad request"
