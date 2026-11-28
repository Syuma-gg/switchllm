# switchllm

English | [日本語](https://github.com/Syuma-gg/switchllm/blob/main/README.ja.md)

A lightweight client library that lets you call OpenAI, Anthropic, Gemini, and OpenAI-compatible servers (Ollama, LM Studio, vLLM, and more) **with the same code**.

```python
from switchllm import Client

client = Client("anthropic:claude-sonnet-5-5")   # ← change only this string to switch providers
print(client.chat("Hello").text)
```

- The only dependency is `httpx`. No vendor SDKs: adapters absorb the differences between each provider's HTTP formats
- Keeps the signatures and reasoning data that tool-call round trips need (Gemini's thoughtSignature, Anthropic's thinking, OpenAI's reasoning, DeepSeek's reasoning_content)
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
| DeepSeek | `deepseek:deepseek-flash` | `DEEPSEEK_API_KEY` |
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
res = client.chat("What is the capital of Japan?", system="Answer in one word.", max_tokens=100)
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
  - It is not sent unless you give it (every provider defaults to auto)
  - Naming a tool that is not in `tools` raises `ConfigurationError` before sending
  - Some models reject `"required"` and a named tool (for example Anthropic's Opus 5.5 and Sonnet 5.5). The API's error is then raised as is as `BadRequestError`; switchllm never quietly changes it to `"auto"`
- Parallel tool calls are supported (for Anthropic and Gemini, the results are sent together in one message)
- `run_tool_calls` runs your functions with arguments chosen by the model. Validate the arguments in functions that do anything dangerous ([SECURITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/SECURITY.md), in Japanese)

### Async

`AsyncClient` takes the same arguments and has the same methods as `Client`. Await `chat()`, and iterate `stream()` with `async for` (without `await`).

```python
import asyncio
from switchllm import AsyncClient

async def main():
    async with AsyncClient("anthropic:claude-sonnet-5-5") as client:
        res = await client.chat("What is the capital of Japan?")
        async for ev in client.stream("Write a haiku"):
            if ev.type == "text":
                print(ev.text, end="")

asyncio.run(main())
```

- Requests can run concurrently (`asyncio.gather(client.chat(a), client.chat(b))`). Waits between retries do not block the event loop
- To use your own connection, pass `http_client=httpx.AsyncClient(...)`. Clients made with `with_model()` share it. Close the client with `await client.aclose()` or `async with`
- A stream closes its connection when you read it to the end. If you `break` out early and want the connection closed at once, wrap it: `async with contextlib.aclosing(client.stream(...)) as events:`
- `await arun_tool_calls(tools, res.tool_calls)` awaits `async def` tools and calls plain functions as they are. It runs the calls concurrently and returns the results in the order of the calls. `run_tool_calls` does not run `async def` tools: it returns an error result that says to use `arun_tool_calls`

### Saving and restoring history

```python
import json

saved = json.dumps([m.to_dict() for m in messages])   # store it in a file or a database

messages = json.loads(saved)                           # chat() accepts the dicts as they are
res = client.chat(messages + [{"role": "user", "content": "And tomorrow?"}], tools=[get_weather])
```

- `to_dict()` keeps the raw data (`provider_state`) too, so a restored conversation still sends back signatures and reasoning. Without it, a restored tool conversation can fail (DeepSeek returns 400) or lose the model's reasoning
- `Message.from_dict()` turns one dict back into a `Message`
- Load only history that you saved yourself: the raw data is sent to the provider unchanged

### Structured output

```python
schema = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
}
res = client.chat("Taro is 20 years old.", response_schema=schema)
res.parsed   # {"name": "Taro", "age": 20}
res.text     # the same JSON as a string
```

- `response_schema=` takes a JSON Schema dict and constrains the answer to it, in each provider's own format (OpenAI `text.format`, Anthropic `output_config.format`, Gemini `responseJsonSchema`, Chat Completions `response_format`). For a Pydantic model, pass `Model.model_json_schema()`
- `res.parsed` is set only when the model finished (`stop_reason == "end"`) and the text is valid JSON. After a refusal or at the token limit it is `None`; check `res.stop_reason` and `res.text`
- `additionalProperties: false` is added to every object that lists `properties` (OpenAI and Anthropic require it). Nothing else in the schema is changed
- With `stream()`, the `text` events are pieces of the JSON, and the final `done` response has `parsed`
- DeepSeek supports only JSON mode, so `response_schema=` raises `UnsupportedFeatureError` before sending. Use `extra={"response_format": {"type": "json_object"}}` and describe the format in the prompt ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/structured-output.md), in Japanese)

### Reasoning effort

```python
res = client.chat("How many r's are in 'strawberry'?", reasoning_effort="low")
```

- `reasoning_effort=` takes one of `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"` (the levels OpenAI documents). The name is sent unchanged in each provider's own field (OpenAI `reasoning.effort`, Anthropic `output_config.effort`, Gemini `thinkingConfig.thinkingLevel`, Chat Completions `reasoning_effort`). Leave it out to use the model's default
- A level that a provider's API has no name for raises `UnsupportedFeatureError` before sending: `"minimal"` on Anthropic, and `"none"` and `"max"` on Gemini. On Anthropic, `"none"` turns thinking off (`thinking: {"type": "disabled"}`)
- Models differ in the levels they accept (gpt-5.5 has no `"max"`, and Claude Opus 5.5 cannot turn thinking off). The library keeps no list of models, so these come back from the API as `BadRequestError`
- The levels are hints, not token budgets: the same level does not mean the same amount of reasoning on every model. Write other reasoning settings (such as Gemini 2.5's `thinkingBudget`) in `extra_by_provider=` ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/reasoning-effort.md), in Japanese)

### Reasoning summaries

Most reasoning models don't return their reasoning text unless you ask. Pass `reasoning_summary=True` to ask for it:

```python
res = client.chat("How many integers from 1 to 1000 have digits that add up to 10?", reasoning_summary=True)
print(res.thinking)  # the reasoning summary, or None if the model returned none
```

- Each provider gets its own setting: OpenAI `reasoning.summary: "auto"`, Anthropic `thinking: {"type": "adaptive", "display": "summarized"}`, Gemini `thinkingConfig.includeThoughts: true`. Chat Completions has no standard setting, so nothing is added there; DeepSeek returns its reasoning anyway, and Ollama or LM Studio text is used if the server sends it. With `stream()`, the text arrives as `thinking` events
- The default is `False`
  - On Anthropic and Gemini the summary itself costs nothing extra (you pay for the full reasoning either way); on Anthropic it adds some latency
  - Claude Opus 4.6 to 4.8 and Sonnet 4.6 don't think unless asked, so `adaptive` turns thinking on there, which does cost more
  - Claude 4.5 models reject `adaptive` with a 400, so on those pass `extra={"thinking": {"type": "enabled", "budget_tokens": 2048}}` as well
  - OpenAI may require organization verification for summaries on its latest reasoning models
- The model decides whether to return a summary, so `res.thinking` can still be `None`
- It is a plain on/off switch. For OpenAI's `"concise"` or `"detailed"`, use `extra_by_provider=`
- Combining it with `reasoning_effort="none"` raises `ConfigurationError` before sending. The `openai-chat:` provider pointed at OpenAI raises `UnsupportedFeatureError`, because OpenAI's Chat Completions doesn't return reasoning text; use `openai:` ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/reasoning-summary.md), in Japanese)

### Files (PDF and more)

Put a `FilePart` in a user message, before the question (Anthropic recommends putting PDFs first).

```python
from switchllm import Client, FilePart, Message, TextPart

client = Client("anthropic:claude-sonnet-5-5")
res = client.chat([Message.user([FilePart.from_file("report.pdf"), TextPart("Summarize it in three lines.")])])
```

- PDF works with every provider except the `deepseek`, `ollama` and `lmstudio` presets
- Other file types depend on the provider. OpenAI (Responses API) and Gemini get them as they are and reject the types they don't support. Anthropic takes PDF and plain text (text files are sent as text). Chat Completions takes only PDF
- A file that the provider is known not to accept raises `UnsupportedFeatureError` before sending. Files are sent inline as base64 (no file URLs or upload APIs). Images go in `ImagePart`; audio and video are not supported ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/file-input.md), in Japanese)

### Provider-specific features

Parameters that cannot be unified are written in each provider's own format, side by side, in `extra_by_provider=`. Only the part for the provider chosen by the model string is sent, so switching models does not change the calling code. Providers you leave out get nothing extra. The keys are the `provider:` part of the model string (use `"google"` if you write `google:`).

```python
client.chat("...", extra_by_provider={
    "openai": {"reasoning": {"summary": "auto"}},      # OpenAI Responses: return reasoning summaries
    "anthropic": {"metadata": {"user_id": "u-1"}},     # Anthropic
})
```

`extra=` merges the same thing into the request body for every provider. If you give both, the `extra_by_provider` part wins.

Client options that only one provider understands (such as Anthropic's `default_max_tokens`) go side by side in `options_by_provider=`, in the same shape. Only the chosen provider's part is used, so the same code works when the model string comes from configuration. Every part is checked when the client is created, so a typo shows up before you switch to that provider.

```python
client = Client(options_by_provider={   # the model comes from $SWITCHLLM_MODEL
    "anthropic": {"default_max_tokens": 32000},
    "gemini": {"schema_field": "parametersJsonSchema"},
})
```

`client.with_model("anthropic:claude-haiku-5-5")` creates a client for another model that shares the connection (useful for fallbacks and comparisons). For the same provider it keeps `api_key` / `base_url` / options; for a different provider it keeps only `options_by_provider=` (each provider uses its own part), so keys and auth headers are never sent to another server.

`headers=` adds HTTP headers to every request, for every provider (for example, for a gateway in front of the APIs).

- A header with the same name as one the library sets, including the API key header (`Authorization`, `x-api-key`, `x-goog-api-key`), replaces it. Names are compared case-insensitively
- If you pass the API key header this way, you need no `api_key=` or environment variable
- To stop a header the library sets, pass `None` as its value. For example, when the gateway takes its key in a different header, set the library's API key header to `None` so that your provider key is not sent to the gateway
- On Anthropic, set the API version and beta features with `anthropic_version=` and `beta=` instead

```python
Client("anthropic:claude-sonnet-5-5", base_url="https://gateway.example.com",
       headers={"x-api-key": "<gateway key>", "X-Team": "search"})
# The gateway takes its key in Authorization: do not send x-api-key
Client("anthropic:claude-sonnet-5-5", base_url="https://gateway.example.com",
       headers={"Authorization": "Bearer <gateway key>", "x-api-key": None})
```

### Errors

Every provider raises the same exception classes: `AuthenticationError` (401/403) / `RateLimitError` (429) / `BadRequestError` (4xx) / `NotFoundError` (404) / `ServerError` (5xx) / `StreamError` / `APIConnectionError` / `ConfigurationError` / `UnsupportedFeatureError`.

Errors returned by the provider are passed through unchanged. `e.message` is the API's own message, and `e.body` is the whole error body.

Some settings are accepted by some models and rejected by others: a forced `tool_choice`, `reasoning_effort` levels, `reasoning_summary=True`, parts of `response_schema`, and `temperature`. When the API rejects one of them with a 400 and the error names it, `str(e)` gets a `Hint (...)` line that says which argument it was and how to write it so that it works on every model. The decision looks at what was sent and what the error says, not at the model name ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/error-hints.md), in Japanese).

```python
from switchllm import APIError

try:
    client.chat("...", tools=[get_weather], tool_choice="required")
except APIError as e:
    print(e)          # [anthropic] tool_choice: type "tool" and "any" are not supported for this model. (HTTP 400)
                      # Hint (tool_choice): this model may not accept a forced tool_choice ('required' or a tool name). ...
    print(e.message)  # only the API's message
    print(e.body)     # {"type": "error", "error": {...}, "request_id": "req_..."}
```

When an option is not sent because every model of the provider would reject it, you get a `DroppedParameterWarning` instead of an exception (currently only `temperature` on Anthropic). To stop before sending instead, turn it into an error: `warnings.filterwarnings("error", category=DroppedParameterWarning)`.

429s, 5xx errors, and connection errors are retried automatically with exponential backoff (`max_retries=2`, honoring the `Retry-After` header). Streams are retried only before the first event arrives. See [RELIABILITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/RELIABILITY.md) (in Japanese) for details.

### Other OpenAI-compatible APIs

Servers without a preset (OpenAI-compatible cloud APIs, your own vLLM, an internal gateway, and so on) can be registered under a name. Check the base_url in each service's official documentation.

```python
from switchllm import Client, register_provider

register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())   # no key needed
client = Client("myvllm:Qwen/Qwen3-8B")
```

You can also skip registration and write `Client("openai-chat:<model>", base_url="...", api_key="...")`. `openai-chat` sends max_tokens as `max_completion_tokens` only when the server is OpenAI itself (OpenAI has deprecated `max_tokens`), and as `max_tokens` to any other server.

---

## Design notes

The reasons are explained in the [design documents](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/index.md) (in Japanese).

- **OpenAI itself is called through the Responses API; compatible servers through Chat Completions.** From GPT-5.4 on, Chat Completions cannot combine reasoning with tool calls ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/openai-responses-api.md))
- **Raw data is kept in `provider_state` and sent back unchanged to the same model.** Dropping signatures or reasoning items makes tool round trips fail. For a different model, the history is converted again from the common types. Anthropic is the exception: its thinking blocks are also sent unchanged to other Claude models, as Anthropic's documentation asks, and the API drops the ones the new model cannot read, without an error or a charge ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/provider-state-resend.md))
- **The three major providers are called through their native APIs.** Their OpenAI-compatible endpoints limit features such as caching and thinking ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/native-apis.md))
- **`usage` means the same thing for every provider.** `input_tokens` is the total input including the cache, and `output_tokens` is the total output including reasoning ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/usage-normalization.md))
- **HTTP is called directly, without SDKs.** This keeps httpx as the only dependency and makes the format differences visible in the code ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/http-without-sdk.md))

## Known limitations

- **Not yet verified against the real APIs.** The tests run against mocks that reproduce the formats in each provider's documentation. From a clone of the repository, please check connectivity with your own keys: `python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5,gemini:gemini-flash-latest"` (this costs a small amount; add `--collect-only` first to see how many calls it makes without calling the APIs)
- Image and file input are supported (files as base64 only); audio input is not. Image URLs are not supported for Gemini (pass base64 instead)
- Reasoning text: OpenAI, Claude 5 models, and Gemini return no reasoning summary unless you ask, so `res.thinking` is often `None`. Pass `reasoning_summary=True` to ask for it (see "Reasoning summaries" above). Even then, the model decides whether to return one
- Anthropic: the default API version is `2026-10-21`, which rejects `temperature` on every model. So `temperature=` is not sent, and a `DroppedParameterWarning` tells you so; the rest of the request goes through. For older models that still accept it, use `Client(..., anthropic_version="2023-06-01")`. Current Claude models reject a non-default `temperature` even on the older version. Gemini 3.6 and later, and DeepSeek in thinking mode, ignore `temperature` without an error ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/dropped-parameter-warning.md), in Japanese)
- Structured output: OpenAI requires every property to be listed in `required` (make an optional field nullable instead, for example `{"type": ["string", "null"]}`), and each provider supports a different subset of JSON Schema. Keywords a provider does not support are rejected with `BadRequestError` (Gemini ignores them)
- Anthropic requires max_tokens, so it is sent as 64000 when you do not give one. Every current Claude model accepts this value, and Claude 5 models count their reasoning toward it (change it with `Client(..., default_max_tokens=...)`). The other providers send no limit unless you give one, so the model's own limit applies ([details](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/anthropic-max-tokens.md), in Japanese)
- The OpenAI Responses API has no `stop` sequences, so specifying one raises `UnsupportedFeatureError`
- The Responses API stores responses by default. With `extra={"store": False}`, reasoning items still include `encrypted_content` by default, so they can be resent without `include` (not yet verified against the real API)
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
