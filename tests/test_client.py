import inspect
import re
from pathlib import Path

import httpx
import pytest

from conftest import WEATHER_TOOL
from switchllm import (
    AuthenticationError,
    Client,
    ConfigurationError,
    ImagePart,
    Message,
    RateLimitError,
    ServerError,
    Tool,
    parse_model,
    register_provider,
)
from switchllm._http import iter_sse
from switchllm.providers import ADAPTERS, PRESETS

OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}


def test_parse_model():
    assert parse_model("ollama:llama3.2:3b") == ("ollama", "llama3.2:3b")
    assert parse_model("OpenAI:gpt-5.5") == ("openai", "gpt-5.5")
    with pytest.raises(ConfigurationError):
        parse_model("gpt-5.5")


def test_model_from_env(monkeypatch, api):
    monkeypatch.setenv("SWITCHLLM_MODEL", "anthropic:claude-sonnet-5-5")
    api.reply_json(OK)
    client = Client(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(api.handler)))
    assert client.model == "anthropic:claude-sonnet-5-5"
    assert client.chat("hi").text == "ok"


def test_missing_key_and_unknown_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        Client("anthropic:claude-sonnet-5-5")
    with pytest.raises(ConfigurationError, match="unknown provider"):
        Client("nope:model", api_key="k")


def test_api_key_from_env(monkeypatch, api):
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    api.reply_json({"candidates": [{"content": {"parts": [{"text": "x"}]}}]})
    Client("gemini:m", http_client=httpx.Client(transport=httpx.MockTransport(api.handler))).chat("hi")
    assert api.requests[0].headers["x-goog-api-key"] == "from-env"


def test_retry_on_429_then_success(api):
    api.reply_json({"error": {"message": "slow down"}}, status=429, headers={"retry-after": "0"})
    api.reply_json(OK)
    assert api.client("anthropic:claude-sonnet-5-5").chat("hi").text == "ok"
    assert len(api.requests) == 2


def test_error_mapping(api):
    api.reply_json({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, status=401)
    with pytest.raises(AuthenticationError, match="invalid x-api-key") as e:
        api.client("anthropic:claude-sonnet-5-5").chat("hi")
    assert e.value.status_code == 401 and e.value.provider == "anthropic"


def test_retries_exhausted(api):
    for _ in range(3):
        api.reply_json({"error": {"message": "boom"}}, status=503)
    with pytest.raises(ServerError):
        api.client("openai:gpt-5.5", max_retries=2).chat("hi")
    assert len(api.requests) == 3


def test_stream_retries_before_first_byte(api):
    api.reply_json({"error": {"message": "rate"}}, status=429)
    api.reply_sse([("message_stop", {"type": "message_stop"})])
    events = list(api.client("anthropic:claude-sonnet-5-5").stream("hi"))
    assert events[-1].type == "done" and len(api.requests) == 2


def test_rate_limit_error_class(api):
    api.reply_json({"error": {"message": "quota"}}, status=429)
    with pytest.raises(RateLimitError):
        api.client("gemini:m", max_retries=0).chat("hi")


def test_register_custom_provider(api):
    register_provider("myvllm", base_url="http://gpu:8000/v1", api_key_env=())
    try:
        api.reply_json({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})
        api.client("myvllm:Qwen/Qwen3-8B", api_key=None).chat("x")
        assert str(api.requests[0].url) == "http://gpu:8000/v1/chat/completions"
    finally:
        PRESETS.pop("myvllm")


def test_dict_messages_and_extra_passthrough(api):
    api.reply_json(OK)
    api.client("anthropic:claude-sonnet-5-5").chat(
        [{"role": "user", "content": "hi"}],
        extra={"thinking": {"type": "enabled", "budget_tokens": 2048}, "metadata": {"user_id": "u1"}},
    )
    body = api.body()
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert body["metadata"] == {"user_id": "u1"}


def test_sse_parser_edge_cases():
    lines = [": comment", "event: a", "data: line1", "data: line2", "", "data: x\r", "", "data: tail"]
    assert list(iter_sse(iter(lines))) == [("a", "line1\nline2"), ("", "x"), ("", "tail")]


# --------------------------------------------------------------------------- #
# Client の入口
# --------------------------------------------------------------------------- #
def test_missing_model_without_env(monkeypatch):
    monkeypatch.delenv("SWITCHLLM_MODEL", raising=False)
    with pytest.raises(ConfigurationError, match="SWITCHLLM_MODEL"):
        Client()


def test_single_message_and_single_dict_are_accepted(api):
    api.reply_json(OK)
    api.reply_json(OK)
    client = api.client("anthropic:claude-sonnet-5-5")
    client.chat(Message.user("a"), system="s")
    client.chat({"role": "user", "content": "b"})
    assert api.body(0)["system"] == "s" and api.body(0)["messages"][0]["content"] == [{"type": "text", "text": "a"}]
    assert api.body(1)["messages"][0]["content"] == [{"type": "text", "text": "b"}]


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "user", "content": [{"type": "text", "text": "OpenAI 形式の dict"}]}],
        [Message.user(["str はそのまま入れられない", ImagePart(data="AAAA")])],
        [Message.tool_result("call_1", {"not": "a string"})],
    ],
    ids=["dict-part", "str-in-list", "dict-content"],
)
def test_content_that_adapters_cannot_read_is_rejected_before_sending(api, messages):
    # 以前は読めないパーツを黙って落として送っていた
    with pytest.raises(TypeError, match="TextPart / ImagePart"):
        api.client("openai:gpt-5.5").chat(messages)
    assert api.requests == []


def test_close_only_closes_own_http_client():
    owned = Client("ollama:llama3.2")
    with owned:
        assert repr(owned) == "Client('ollama:llama3.2')"
    assert owned._http.is_closed  # with を抜けると閉じる

    shared = httpx.Client()
    Client("ollama:llama3.2", http_client=shared).close()
    assert not shared.is_closed  # 渡された接続は呼び出し側のもの
    shared.close()


# --------------------------------------------------------------------------- #
# with_model
# --------------------------------------------------------------------------- #
def test_with_model_same_provider_keeps_key_base_url_and_options(api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    api.reply_json(OK)
    client = api.client(
        "anthropic:claude-sonnet-5-5", api_key="explicit", base_url="https://gw.example.com", beta=["b1"]
    )
    other = client.with_model("anthropic:claude-haiku-5-5")  # 以前は APIキーが見つからず ConfigurationError
    other.chat("hi")
    req = api.requests[0]
    assert str(req.url) == "https://gw.example.com/v1/messages"
    assert req.headers["x-api-key"] == "explicit" and req.headers["anthropic-beta"] == "b1"
    assert api.body()["model"] == "claude-haiku-5-5"
    assert other._http is client._http  # 接続を共有する


def test_with_model_other_provider_does_not_carry_key_or_headers(api):
    # headers= に認証情報を入れていても、別のサーバーへは送らない。
    # 同じアダプター（openai_chat）を使う別のプロバイダーへの切り替えで確かめる
    api.reply_json({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    client = api.client("openai-chat:gpt-x", api_key="openai-key", headers={"X-Secret": "s3cret"}, max_retries=0)
    other = client.with_model("ollama:llama3.2")
    other.chat("hi")
    req = api.requests[0]
    assert str(req.url) == "http://localhost:11434/v1/chat/completions"
    assert "x-secret" not in req.headers and "authorization" not in req.headers
    assert other.retry.max_retries == 0  # 通信の設定は引き継ぐ


def test_with_model_overrides_win(api):
    api.reply_json({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    client = api.client("anthropic:claude-sonnet-5-5")
    client.with_model("ollama:llama3.2", base_url="http://gpu:11434/v1").chat("hi")
    assert str(api.requests[0].url) == "http://gpu:11434/v1/chat/completions"


# --------------------------------------------------------------------------- #
# プロバイダー別のオプション
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("model", "options"),
    [
        ("openai-chat:gpt-x", {"stream_usge": False}),  # 打ち間違い
        ("anthropic:claude-sonnet-5-5", {"schema_field": "parametersJsonSchema"}),  # Gemini 用
        ("openai:gpt-5.5", {"headers": {"X": "1"}}),  # Responses にはオプションがない
    ],
)
def test_unknown_option_is_rejected(model, options):
    with pytest.raises(ConfigurationError, match="unknown option"):
        Client(model, api_key="k", **options)


def test_register_provider_rejects_unknown_option():
    with pytest.raises(ConfigurationError, match="unknown option.*beta"):
        register_provider("mycloud", base_url="http://x/v1", beta=["b"])
    assert "mycloud" not in PRESETS


def test_option_names_match_the_options_each_adapter_reads():
    # 読むオプションを足したのに option_names に足し忘れると、利用者が渡したときに ConfigurationError になる
    pattern = re.compile(r'self\.options(?:\.get\(|\[)"(\w+)"|"(\w+)" in self\.options')
    for cls in ADAPTERS.values():
        source = Path(inspect.getfile(cls)).read_text(encoding="utf-8")
        used = {a or b for a, b in pattern.findall(source)}
        assert used == set(cls.option_names), f"{cls.__name__}.option_names を、読んでいるオプション {sorted(used)} にそろえる"


# --------------------------------------------------------------------------- #
# register_provider
# --------------------------------------------------------------------------- #
def test_register_provider_name_is_case_insensitive(api):
    register_provider(" MyCloud ", base_url="http://x/v1", api_key_env=())
    try:
        api.reply_json({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
        client = api.client("MYCLOUD:m", api_key=None)  # 以前は unknown provider
        assert client.model == "mycloud:m" and client.chat("hi").text == "ok"
    finally:
        PRESETS.pop("mycloud")


@pytest.mark.parametrize("name", ["", "  ", "my:cloud"])
def test_register_provider_rejects_names_model_strings_cannot_use(name):
    with pytest.raises(ConfigurationError, match="provider name"):
        register_provider(name, base_url="http://x/v1")


def test_register_provider_rejects_unknown_adapter():
    with pytest.raises(ConfigurationError, match="unknown adapter"):
        register_provider("mycloud", adapter="bedrock")


# --------------------------------------------------------------------------- #
# extra_by_provider
# --------------------------------------------------------------------------- #
THINKING = {
    "openai": {"reasoning": {"effort": "high"}},
    "anthropic": {"thinking": {"type": "enabled", "budget_tokens": 2048}},
    "gemini": {"generationConfig": {"thinkingConfig": {"thinkingBudget": 2048}}},
}


def test_extra_by_provider_sends_only_the_chosen_providers_part(api):
    api.reply_json(OK)
    api.reply_json({"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})
    api.client("anthropic:claude-sonnet-5-5").chat("hi", extra_by_provider=THINKING)
    api.client("gemini:gemini-flash-latest").chat("hi", max_tokens=10, extra_by_provider=THINKING)
    claude, gemini = api.body(0), api.body(1)
    assert claude["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "reasoning" not in claude and "generationConfig" not in claude
    # 共通パラメータ由来の generationConfig と深くマージされる
    assert gemini["generationConfig"] == {"maxOutputTokens": 10, "thinkingConfig": {"thinkingBudget": 2048}}
    assert "thinking" not in gemini


def test_extra_by_provider_overrides_extra_and_works_for_stream(api):
    api.reply_sse([("message_stop", {"type": "message_stop"})])
    events = api.client("anthropic:claude-sonnet-5-5").stream(
        "hi",
        extra={"metadata": {"user_id": "u1"}, "temperature": 0.1},
        extra_by_provider={"Anthropic": {"temperature": 1.0}},  # 名前は大文字小文字を区別しない
    )
    assert list(events)[-1].type == "done"
    assert api.body()["metadata"] == {"user_id": "u1"} and api.body()["temperature"] == 1.0


def test_extra_by_provider_rejects_unknown_names_and_non_dicts(api):
    client = api.client("anthropic:claude-sonnet-5-5")
    with pytest.raises(ConfigurationError, match="unknown provider 'antropic'"):
        client.chat("hi", extra_by_provider={"antropic": {"x": 1}})
    with pytest.raises(TypeError, match="must be a dict"):
        client.chat("hi", extra_by_provider={"openai": "high"})
    assert api.requests == []


# --------------------------------------------------------------------------- #
# tool_choice の検査
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("tools", "tool_choice", "message"),
    [
        ([WEATHER_TOOL], {"name": "get_wether"}, "not in tools"),
        ([WEATHER_TOOL], "require", "not in tools"),  # "required" の打ち間違いはツール名として扱われる
        ([], "required", "needs tools"),
        ([], {"name": "get_weather"}, "needs tools"),
    ],
)
def test_tool_choice_mistakes_are_caught_before_sending(api, tools, tool_choice, message):
    with pytest.raises(ConfigurationError, match=message):
        api.client("anthropic:claude-sonnet-5-5").chat("hi", tools=tools, tool_choice=tool_choice)
    assert api.requests == []


def test_tool_choice_accepts_tool_object_and_none_without_tools(api):
    api.reply_json(OK)
    api.reply_json(OK)
    client = api.client("anthropic:claude-sonnet-5-5")
    weather = Tool(**WEATHER_TOOL)
    client.chat("hi", tools=[weather], tool_choice=weather)
    client.chat("hi", tool_choice="none")  # ツールなしの none は害がないので通す
    assert api.body(0)["tool_choice"] == {"type": "tool", "name": "get_weather"}
    assert "tool_choice" not in api.body(1)


def test_invalid_tool_choice_type(api):
    with pytest.raises(ConfigurationError, match="invalid tool_choice"):
        api.client("anthropic:claude-sonnet-5-5").chat("hi", tools=[WEATHER_TOOL], tool_choice=3)
