import httpx
import pytest

from unillm import (
    AuthenticationError,
    Client,
    ConfigurationError,
    RateLimitError,
    ServerError,
    parse_model,
    register_provider,
)
from unillm._http import iter_sse
from unillm.providers import PRESETS

OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}


def test_parse_model():
    assert parse_model("ollama:llama3.2:3b") == ("ollama", "llama3.2:3b")
    assert parse_model("OpenAI:gpt-5.5") == ("openai", "gpt-5.5")
    with pytest.raises(ConfigurationError):
        parse_model("gpt-5.5")


def test_model_from_env(monkeypatch, api):
    monkeypatch.setenv("UNILLM_MODEL", "anthropic:claude-sonnet-5-5")
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
