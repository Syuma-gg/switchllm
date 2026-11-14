# switchllm

English | [日本語](https://github.com/Syuma-gg/switchllm/blob/main/README.ja.md)

A lightweight client library that lets you call OpenAI, Anthropic, Gemini, and OpenAI-compatible servers (Ollama, LM Studio, vLLM, and more) **with the same code**.

```python
from switchllm import Client

client = Client("anthropic:claude-sonnet-5-5")   # ← change only this string to switch providers
print(client.chat("Hello").text)
```

- The only dependency is `httpx`. No vendor SDKs: adapters absorb the differences between each provider's HTTP formats
- Keeps the signatures and reasoning data that tool-call round trips need (Gemini's thoughtSignature, Anthropic's thinking, OpenAI's reasoning)
- Not an agent framework, but a thin layer underneath one ([what it does and does not do](https://github.com/Syuma-gg/switchllm/blob/main/docs/product-specs/scope.md), in Japanese)
- You choose the provider with a string. There is no automatic switching to another provider on failure (fallback or routing). If you need it, catch `RateLimitError` and similar errors, and switch with `client.with_model()`

---

## Installation

```shell
pip install switchllm
```

Requires Python 3.10 or later. The only runtime dependency is `httpx`.

Pass API keys through environment variables (or the `api_key=` argument).

| Provider | Example model string | Environment variable |
|---|---|---|
| OpenAI (Responses API) | `openai:gpt-5.5` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini:gemini-flash-latest` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) |
| OpenAI (Chat Completions) | `openai-chat:<model>` | `OPENAI_API_KEY` |
| Ollama / LM Studio (local) | `ollama:llama3.2` / `lmstudio:<model>` | Not needed |
| Other OpenAI-compatible APIs | Register with `register_provider()` (see "Other OpenAI-compatible APIs" below) | Set when registering |

Model IDs change often, so check each provider's model list before you use one.

## Two ways to switch

1. **One place in your code**: change the string in `Client("openai:gpt-5.5")`
2. **No code changes**: write `Client()` and set the `SWITCHLLM_MODEL` environment variable

```shell
# macOS / Linux
export SWITCHLLM_MODEL="gemini:gemini-flash-latest"
# Windows (PowerShell)
$env:SWITCHLLM_MODEL = "gemini:gemini-flash-latest"

python examples/agent_loop.py
```

The model string is split at the first `:`, so model names may contain colons, as in `ollama:llama3.2:3b`.

## Usage

### Text and streaming

```python
res = client.chat("What is the capital of Japan?", system="Answer in one word.", max_tokens=100, temperature=0)
res.text          # the reply
res.stop_reason   # "end" | "tool_use" | "max_tokens" | "content_filter" | "other"
res.usage         # input_tokens / output_tokens / total_tokens / details (the raw breakdown)
res.thinking      # reasoning text (only from providers that return it)

for ev in client.stream("Write a haiku"):
    if ev.type == "text":
        print(ev.text, end="")
    elif ev.type == "done":
        final = ev.response   # the complete Response, tool calls included
```

### Tool calling (agent loop)

```python
from typing import Annotated, Literal
from switchllm import Client, Message, run_tool_calls, tool

@tool
def get_weather(city: Annotated[str, "City name"], unit: Literal["c", "f"] = "c") -> dict:
    """Return the current weather for a city."""
    return {"city": city, "weather": "sunny"}

messages = [Message.user("What's the weather in Tokyo and Osaka?")]
while True:
    res = client.chat(messages, tools=[get_weather])
    messages.append(res.message)          # append it unchanged (it carries raw data such as signatures)
    if res.stop_reason != "tool_use":
        break
    messages.extend(run_tool_calls([get_weather], res.tool_calls))
print(res.text)
```

- `@tool` builds the tool's JSON Schema from the type hints and the docstring (its first paragraph)
- Supported types: `str` / `int` / `float` / `bool` / `list[T]` (also `tuple` and `set`) / `dict` / `Literal` / `Enum` / `Optional` and `Union` / `Annotated[T, "description"]`. Parameters without a type hint are treated as strings
- Any other type (dataclasses, Pydantic models, and so on) raises `TypeError` when the function is decorated. In that case, pass a JSON Schema directly with `parameters=`

```python
@tool(parameters={
    "type": "object",
    "properties": {"order": {"type": "object", "properties": {"item": {"type": "string"}, "qty": {"type": "integer"}}}},
    "required": ["order"],
})
def place_order(order: dict) -> str:
    """Place an order."""
    return f"Ordered {order['qty']} x {order['item']}"
```

- Tool definitions can also be dicts (`{"name", "description", "parameters"}`)
- `tool_choice`: `"auto"` (default) / `"none"` / `"required"` / `{"name": "get_weather"}`
- Parallel tool calls are supported (for Anthropic and Gemini, the results are sent together in one message)
- `run_tool_calls` runs your functions with arguments chosen by the model. Validate the arguments in functions that do anything dangerous ([SECURITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/SECURITY.md), in Japanese)

### Provider-specific features

Parameters that cannot be unified are passed with `extra=` and merged into the request body as is. This is the only place where your code depends on the provider.

```python
client.chat("...", extra={"metadata": {"user_id": "u-1"}})          # Anthropic
client.chat("...", extra={"reasoning": {"effort": "high"}})          # OpenAI Responses
```

### Errors

Every provider raises the same exception classes: `AuthenticationError` (401/403) / `RateLimitError` (429) / `BadRequestError` (4xx) / `NotFoundError` (404) / `ServerError` (5xx) / `StreamError` / `APIConnectionError` / `ConfigurationError` / `UnsupportedFeatureError`.

429s, 5xx errors, and connection errors are retried automatically with exponential backoff (`max_retries=2`, honoring the `Retry-After` header). Streams are retried only before the first event arrives. See [RELIABILITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/RELIABILITY.md) (in Japanese) for details.

### Other OpenAI-compatible APIs

Servers without a preset (OpenAI-compatible cloud APIs, your own vLLM, an internal gateway, and so on) can be registered under a name. Check the base_url in each service's official documentation.

```python
from switchllm import Client, register_provider

register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())   # no key needed
client = Client("myvllm:Qwen/Qwen3-8B")
```

You can also skip registration and write `Client("openai-chat:<model>", base_url="...", api_key="...")`.

---

## Design notes

The reasons are explained in the [design documents](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/index.md) (in Japanese).

- **OpenAI itself is called through the Responses API; compatible servers through Chat Completions.** From GPT-5.4 on, Chat Completions cannot combine reasoning with tool calls ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/openai-responses-api.md))
- **Raw data is kept in `provider_state` and sent back unchanged to the same model.** Dropping signatures or reasoning items makes tool round trips fail. For a different model, the history is converted again from the common types ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/provider-state-resend.md))
- **The three major providers are called through their native APIs.** Their OpenAI-compatible endpoints limit features such as caching and thinking ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/native-apis.md))
- **`usage` means the same thing for every provider.** `input_tokens` is the total input including the cache, and `output_tokens` is the total output including reasoning ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/usage-normalization.md))
- **HTTP is called directly, without SDKs.** This keeps httpx as the only dependency and makes the format differences visible in the code ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/http-without-sdk.md))

## Known limitations

- **Not yet verified against the real APIs.** The tests run against mocks that reproduce the formats in each provider's documentation. From a clone of the repository, please check connectivity with your own keys: `python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest` (this costs a small amount)
- No async support yet
- Image input is supported; audio, PDF, and file input are not. Image URLs are not supported for Gemini (pass base64 instead)
- Structured output and reasoning effort are not unified (pass each provider's settings with `extra`)
- The OpenAI Responses API has no `stop` sequences, so specifying one raises `UnsupportedFeatureError`
- The Responses API stores responses by default. If you set `extra={"store": False}`, resending reasoning items also requires `include: ["reasoning.encrypted_content"]`
- Gemini function schemas are sent as `parameters` (a subset of OpenAPI). For complex JSON Schemas, use `Client(..., schema_field="parametersJsonSchema")`
- Google calls generateContent "legacy" and recommends the Interactions API for new projects. This library uses generateContent, which is widely documented

## Comparison with existing libraries

Other open-source libraries have the same goal. Depending on your use case, one of them may be a better fit.

| | switchllm (this one) | LiteLLM | any-llm (mozilla-ai) |
|---|---|---|---|
| Providers | 3 major + OpenAI-compatible | 100+ | Many major ones |
| Output format | Its own common types | OpenAI Chat Completions format | OpenAI format |
| Implementation | Direct HTTP, depends only on httpx | Own conversion + Proxy/Gateway | Official SDKs |
| Best for | Understanding what happens inside, fewer dependencies, building your own agent | Production use, cost management, fallback | Light use on top of the SDKs |

- LiteLLM — https://docs.litellm.ai/docs/
- any-llm — https://github.com/mozilla-ai/any-llm

## Development

```shell
git clone https://github.com/Syuma-gg/switchllm.git
cd switchllm
pip install -e ".[dev]"
python -m pytest          # tests against mocked APIs (no API keys needed)
python scripts/quality_gate.py --ci   # ruff, tests and coverage, same as CI
```

The documents below are written in Japanese.

- Architecture: [ARCHITECTURE.md](https://github.com/Syuma-gg/switchllm/blob/main/ARCHITECTURE.md)
- Documentation index: [docs/index.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/index.md) (design decisions, scope, public API, notes and sources on each provider's API, plans)
- Changelog: [CHANGELOG.md](https://github.com/Syuma-gg/switchllm/blob/main/CHANGELOG.md)

## License

MIT ([LICENSE](https://github.com/Syuma-gg/switchllm/blob/main/LICENSE))
