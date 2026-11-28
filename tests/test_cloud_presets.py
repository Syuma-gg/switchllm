"""クラウドの API キーで呼べる経路のプリセット（docs/design-docs/cloud-api-keys.md）。

URL を環境変数から組み立てること、キーのヘッダー、経路ごとに送る前に止めるもの、生データを経路の間で
送り返さないことを確かめる。各経路の仕様は docs/references/cloud-endpoints.md。
"""

from __future__ import annotations

import pytest

from sample_tools import WEATHER_TOOL
from switchllm import ConfigurationError, ImagePart, Message, TextPart, UnsupportedFeatureError, register_provider
from switchllm.providers import PRESETS

REPLIES = {
    "openai": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "status": "completed"},
    "openai_chat": {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
    "anthropic": {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}},
    "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]},
}

# (モデル文字列, 送り先の URL, キーを入れるヘッダー, 本文の model に入る値。None なら本文に入れない)
ROUTES = [
    ("azure:my-gpt", "https://my-resource.openai.azure.com/openai/v1/responses", "api-key", "my-gpt"),
    ("azure-chat:my-gpt", "https://my-resource.openai.azure.com/openai/v1/chat/completions", "api-key", "my-gpt"),
    (
        "foundry:claude-sonnet-5-5",
        "https://my-foundry.services.ai.azure.com/anthropic/v1/messages",
        "x-api-key",
        "claude-sonnet-5-5",
    ),
    (
        "bedrock:anthropic.claude-sonnet-5-5",
        "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages",
        "x-api-key",
        "anthropic.claude-sonnet-5-5",
    ),
    (
        "vertex:gemini-2.5-flash",
        "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:generateContent",
        "x-goog-api-key",
        None,
    ),
]

# プリセット → キーの環境変数
KEY_ENVS = {
    "azure": "AZURE_OPENAI_API_KEY",
    "azure-chat": "AZURE_OPENAI_API_KEY",
    "foundry": "ANTHROPIC_FOUNDRY_API_KEY",
    "bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "vertex": "VERTEX_API_KEY",
}

AUTH_HEADERS = ("authorization", "api-key", "x-api-key", "x-goog-api-key")


def _reply(api, model: str) -> None:
    api.reply_json(REPLIES[PRESETS[model.split(":", 1)[0]].adapter])


# --------------------------------------------------------------------------- #
# 送り先とキーのヘッダー
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("model", "url", "header", "body_model"), ROUTES, ids=[r[0].split(":")[0] for r in ROUTES])
def test_request_goes_to_the_cloud_url_with_the_key_header(api, cloud_env, model, url, header, body_model):
    _reply(api, model)
    api.client(model).chat("hi")
    req = api.requests[0]
    assert str(req.url) == url
    assert req.headers[header] == "test-key"
    # キーはそのヘッダーにだけ入れる（Azure に Authorization: Bearer で送らない）
    assert [h for h in AUTH_HEADERS if h in req.headers] == [header]
    assert api.body().get("model") == body_model


def test_streaming_goes_to_the_cloud_url(api, cloud_env):
    api.reply_sse([(None, {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})])
    events = list(api.client("vertex:gemini-2.5-flash").stream("hi"))
    assert events[-1].type == "done"
    assert str(api.requests[0].url) == (
        "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
    )


@pytest.mark.parametrize(("preset", "env"), KEY_ENVS.items())
def test_key_is_read_from_the_cloud_env(api, cloud_env, monkeypatch, preset, env):
    for other in set(KEY_ENVS.values()) | {"GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(env, "cloud-key")
    model = next(m for m, *_ in ROUTES if m.startswith(f"{preset}:"))
    _reply(api, model)
    api.raw_client(model, retry_backoff=0).chat("hi")
    header = next(h for m, _, h, _ in ROUTES if m == model)
    assert api.requests[0].headers[header] == "cloud-key"


@pytest.mark.parametrize(("preset", "env"), KEY_ENVS.items())
def test_missing_key_names_the_cloud_env(api, cloud_env, monkeypatch, preset, env):
    monkeypatch.delenv(env, raising=False)
    model = next(m for m, *_ in ROUTES if m.startswith(f"{preset}:"))
    with pytest.raises(ConfigurationError, match=env):
        api.raw_client(model)


def test_vertex_does_not_read_the_gemini_api_key(api, cloud_env, monkeypatch):
    # AI Studio のキーを Vertex に送らない（作者の判断。docs/design-docs/cloud-api-keys.md）
    monkeypatch.delenv("VERTEX_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "ai-studio-key")
    monkeypatch.setenv("GEMINI_API_KEY", "ai-studio-key")
    with pytest.raises(ConfigurationError, match="VERTEX_API_KEY"):
        api.raw_client("vertex:gemini-2.5-flash")


def test_key_header_can_be_replaced_with_headers(api, cloud_env, monkeypatch):
    # 名前を替えたキーのヘッダーでも、headers= で置き換えられる。そのときはキーの環境変数がなくてよい
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    api.reply_json(REPLIES["openai"])
    api.raw_client("azure:my-gpt", headers={"API-Key": "gateway-key"}).chat("hi")
    assert api.requests[0].headers.get_list("api-key") == ["gateway-key"]


def test_key_header_can_be_stopped_with_none(api, cloud_env):
    api.reply_json(REPLIES["openai"])
    api.client("azure:my-gpt", headers={"api-key": None, "Authorization": "Bearer gateway"}).chat("hi")
    req = api.requests[0]
    assert "api-key" not in req.headers
    assert req.headers["authorization"] == "Bearer gateway"


# --------------------------------------------------------------------------- #
# URL を組み立てる環境変数
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("model", "env"),
    [
        ("azure:my-gpt", "AZURE_OPENAI_ENDPOINT"),
        ("azure-chat:my-gpt", "AZURE_OPENAI_ENDPOINT"),
        ("foundry:claude-sonnet-5-5", "ANTHROPIC_FOUNDRY_RESOURCE"),
        ("bedrock:anthropic.claude-sonnet-5-5", "AWS_REGION"),
    ],
)
def test_missing_url_env_is_an_error_before_sending(api, cloud_env, monkeypatch, model, env):
    monkeypatch.delenv(env)
    with pytest.raises(ConfigurationError) as e:
        api.client(model)
    assert env in str(e.value)
    assert "base_url=" in str(e.value)
    assert api.requests == []


@pytest.mark.parametrize(
    ("model", "base_url", "url"),
    [
        ("azure:my-gpt", "https://gw.example.com/openai/v1", "https://gw.example.com/openai/v1/responses"),
        ("foundry:claude-sonnet-5-5", "https://gw.example.com/anthropic", "https://gw.example.com/anthropic/v1/messages"),
        ("bedrock:anthropic.claude-sonnet-5-5", "https://gw.example.com/anthropic", "https://gw.example.com/anthropic/v1/messages"),
        ("vertex:gemini-2.5-flash", "https://gw.example.com/v1beta1/publishers/google",
         "https://gw.example.com/v1beta1/publishers/google/models/gemini-2.5-flash:generateContent"),
    ],
)
def test_base_url_wins_and_the_url_env_is_not_needed(api, monkeypatch, model, base_url, url):
    for name in ("AZURE_OPENAI_ENDPOINT", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL", "AWS_REGION",
                 "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    _reply(api, model)
    api.client(model, base_url=base_url).chat("hi")
    assert str(api.requests[0].url) == url


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://my-resource.openai.azure.com/",
        "https://my-resource.openai.azure.com/openai/v1",
        "https://my-resource.openai.azure.com/openai/v1/",
        " https://my-resource.openai.azure.com ",
    ],
)
def test_azure_endpoint_with_or_without_the_v1_path(api, cloud_env, monkeypatch, endpoint):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    api.reply_json(REPLIES["openai"])
    api.client("azure:my-gpt").chat("hi")
    assert str(api.requests[0].url) == "https://my-resource.openai.azure.com/openai/v1/responses"


def test_azure_endpoint_on_services_ai_azure_com(api, cloud_env, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.services.ai.azure.com")
    api.reply_json(REPLIES["openai_chat"])
    api.client("azure-chat:my-gpt").chat("hi")
    assert str(api.requests[0].url) == "https://my-resource.services.ai.azure.com/openai/v1/chat/completions"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://my-resource.openai.azure.com",  # キーを平文で送らない
        "my-resource.openai.azure.com",
        "https://my-resource.openai.azure.com/openai/deployments/my-gpt",  # 古い形の URL
        "https://my-resource.openai.azure.com/openai/v1?api-version=preview",
        "https:///openai/v1",
    ],
)
def test_azure_endpoint_that_cannot_be_used_is_an_error(api, cloud_env, monkeypatch, endpoint):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_ENDPOINT"):
        api.client("azure:my-gpt")


def test_foundry_base_url_env(api, cloud_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "https://other.services.ai.azure.com/anthropic/")
    api.reply_json(REPLIES["anthropic"])
    api.client("foundry:claude-sonnet-5-5").chat("hi")
    assert str(api.requests[0].url) == "https://other.services.ai.azure.com/anthropic/v1/messages"


def test_foundry_resource_and_base_url_together_is_an_error(api, cloud_env, monkeypatch):
    # SDK の文書が2つを mutually exclusive とし、どちらを優先するか書いていない
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "https://other.services.ai.azure.com/anthropic")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_FOUNDRY_RESOURCE.*ANTHROPIC_FOUNDRY_BASE_URL"):
        api.client("foundry:claude-sonnet-5-5")


def test_foundry_base_url_must_be_https(api, cloud_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "http://other.services.ai.azure.com/anthropic")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_FOUNDRY_BASE_URL"):
        api.client("foundry:claude-sonnet-5-5")


def test_bedrock_region_falls_back_to_aws_default_region(api, cloud_env, monkeypatch):
    monkeypatch.delenv("AWS_REGION")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    api.reply_json(REPLIES["anthropic"])
    api.client("bedrock:anthropic.claude-sonnet-5-5").chat("hi")
    assert str(api.requests[0].url) == "https://bedrock-mantle.ap-northeast-1.api.aws/anthropic/v1/messages"


def test_bedrock_aws_region_comes_before_aws_default_region(api, cloud_env, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    api.reply_json(REPLIES["anthropic"])
    api.client("bedrock:anthropic.claude-sonnet-5-5").chat("hi")
    assert str(api.requests[0].url) == "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages"


@pytest.mark.parametrize(
    ("model", "env", "value"),
    [
        # キーは組み立てた URL のホストに送るので、別のホストになる値は止める
        ("bedrock:anthropic.claude-sonnet-5-5", "AWS_REGION", "evil.example.com/x"),
        ("bedrock:anthropic.claude-sonnet-5-5", "AWS_REGION", "us-east-1.evil"),
        ("bedrock:anthropic.claude-sonnet-5-5", "AWS_REGION", "-us-east-1"),
        ("foundry:claude-sonnet-5-5", "ANTHROPIC_FOUNDRY_RESOURCE", "my.foundry"),
        ("foundry:claude-sonnet-5-5", "ANTHROPIC_FOUNDRY_RESOURCE", "my-foundry/x"),
    ],
)
def test_region_and_resource_must_be_one_host_label(api, cloud_env, monkeypatch, model, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(ConfigurationError, match=env):
        api.client(model)


def test_empty_url_env_counts_as_missing(api, cloud_env, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    api.reply_json(REPLIES["anthropic"])
    api.client("bedrock:anthropic.claude-sonnet-5-5").chat("hi")
    assert str(api.requests[0].url) == "https://bedrock-mantle.eu-west-1.api.aws/anthropic/v1/messages"


def test_with_model_in_the_same_route_keeps_the_url(api, cloud_env):
    api.reply_json(REPLIES["anthropic"])
    api.client("bedrock:anthropic.claude-sonnet-5-5").with_model("bedrock:anthropic.claude-haiku-5-5").chat("hi")
    assert str(api.requests[0].url) == "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages"
    assert api.body()["model"] == "anthropic.claude-haiku-5-5"


def test_with_model_to_the_vendor_api_does_not_carry_the_cloud_url_or_key(api, cloud_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "vendor-key")
    api.reply_json(REPLIES["anthropic"])
    api.client("bedrock:anthropic.claude-sonnet-5-5").with_model("anthropic:claude-sonnet-5-5").chat("hi")
    req = api.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "vendor-key"


# --------------------------------------------------------------------------- #
# 経路ごとに送る前に止めるもの
# --------------------------------------------------------------------------- #
def _image_url_message() -> list[Message]:
    return [Message.user([TextPart("見て"), ImagePart(url="https://example.com/cat.png", media_type="image/png")])]


def test_bedrock_stops_image_urls_before_sending(api, cloud_env):
    # bedrock-mantle は「URL sources for images and documents」を受け付けない
    with pytest.raises(UnsupportedFeatureError, match="base64"):
        api.client("bedrock:anthropic.claude-sonnet-5-5").chat(_image_url_message())
    assert api.requests == []


def test_bedrock_sends_base64_images(api, cloud_env):
    api.reply_json(REPLIES["anthropic"])
    image = ImagePart(data="iVBORw0KGgo=", media_type="image/png")
    api.client("bedrock:anthropic.claude-sonnet-5-5").chat([Message.user([TextPart("見て"), image])])
    assert api.body()["messages"][0]["content"][1]["source"]["type"] == "base64"


def test_foundry_sends_image_urls(api, cloud_env):
    # Azure で動く deployment は「base64 encoded or URL-based images」を受け付ける
    api.reply_json(REPLIES["anthropic"])
    api.client("foundry:claude-sonnet-5-5").chat(_image_url_message())
    assert api.body()["messages"][0]["content"][1]["source"] == {"type": "url", "url": "https://example.com/cat.png"}


def test_anthropic_image_url_option_for_registered_providers(api, monkeypatch):
    monkeypatch.setenv("PROXY_API_KEY", "k")
    register_provider("myproxy", adapter="anthropic", base_url="https://proxy.example.com", api_key_env="PROXY_API_KEY",
                      image_url=False)
    try:
        with pytest.raises(UnsupportedFeatureError):
            api.raw_client("myproxy:claude-sonnet-5-5").chat(_image_url_message())
    finally:
        PRESETS.pop("myproxy")
    assert api.requests == []


def test_azure_chat_sends_max_completion_tokens(api, cloud_env):
    # Azure の推論モデルは、Chat Completions で max_completion_tokens だけを受け付ける
    api.reply_json(REPLIES["openai_chat"])
    api.client("azure-chat:my-gpt").chat("hi", max_tokens=100)
    body = api.body()
    assert body["max_completion_tokens"] == 100
    assert "max_tokens" not in body


def test_azure_chat_stops_reasoning_summary_before_sending(api, cloud_env):
    # Chat Completions は推論の本文を返さないので、黙って None にしない。Responses のプリセットを案内する
    with pytest.raises(UnsupportedFeatureError, match="'azure:'"):
        api.client("azure-chat:my-gpt").chat("hi", reasoning_summary=True)
    assert api.requests == []


def test_azure_responses_sends_reasoning_summary(api, cloud_env):
    api.reply_json(REPLIES["openai"])
    api.client("azure:my-gpt").chat("hi", reasoning_summary=True)
    assert api.body()["reasoning"] == {"summary": "auto"}


def test_openai_chat_reasoning_summary_option_for_registered_providers(api):
    register_provider("mygateway", base_url="https://gw.example.com/v1", api_key_env=(), reasoning_summary=False)
    try:
        with pytest.raises(UnsupportedFeatureError, match="reasoning_summary"):
            api.raw_client("mygateway:gpt-5.5").chat("hi", reasoning_summary=True)
    finally:
        PRESETS.pop("mygateway")
    assert api.requests == []


# --------------------------------------------------------------------------- #
# 生データの再送
# --------------------------------------------------------------------------- #
def _bedrock_history(api):
    api.reply_json({
        "content": [
            {"type": "thinking", "thinking": "考える", "signature": "BEDROCK_SIG"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "仙台"}},
        ],
        "stop_reason": "tool_use",
        "usage": {},
    })
    client = api.client("bedrock:anthropic.claude-sonnet-5-5")
    res = client.chat("仙台の天気", tools=[WEATHER_TOOL])
    return client, [Message.user("仙台の天気"), res.message, Message.tool_result("toolu_1", "雪")]


def test_thinking_blocks_are_not_resent_from_bedrock_to_the_vendor_api(api, cloud_env):
    # 再送はプロバイダーキーごと。同じ Messages API でも、経路が違えば署名を送り返さない
    client, history = _bedrock_history(api)
    api.reply_json(REPLIES["anthropic"])
    client.with_model("anthropic:claude-sonnet-5-5", api_key="k").chat(history, tools=[WEATHER_TOOL])
    assert [b["type"] for b in api.body(1)["messages"][1]["content"]] == ["tool_use"]
    assert "BEDROCK_SIG" not in api.requests[1].content.decode()


def test_thinking_blocks_are_not_resent_from_bedrock_to_foundry(api, cloud_env):
    client, history = _bedrock_history(api)
    api.reply_json(REPLIES["anthropic"])
    client.with_model("foundry:claude-sonnet-5-5", api_key="k").chat(history, tools=[WEATHER_TOOL])
    assert "BEDROCK_SIG" not in api.requests[1].content.decode()


def test_thinking_blocks_are_resent_to_another_model_on_bedrock(api, cloud_env):
    # 同じ経路の中では、Anthropic と同じく別のモデルへも送り返す（U43）
    client, history = _bedrock_history(api)
    api.reply_json(REPLIES["anthropic"])
    client.with_model("bedrock:anthropic.claude-haiku-5-5").chat(history, tools=[WEATHER_TOOL])
    assert api.body(1)["messages"][1]["content"][0] == {
        "type": "thinking", "thinking": "考える", "signature": "BEDROCK_SIG"}
