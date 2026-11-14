"""OpenAI Chat Completions 互換アダプター（POST {base_url}/chat/completions）。

Ollama / LM Studio / vLLM / OpenRouter / Groq / DeepSeek / xAI など、
「OpenAI 互換」を名乗るサーバーはこの形式を話すので、base_url を変えるだけで使える。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from .._http import HTTPRequest
from ..errors import StreamError
from ..types import ImagePart, Message, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, deep_merge, normalize_tool_choice
from .openai_responses import _parse_args

_FINISH = {
    "stop": "end",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


class OpenAIChatProvider(Provider):
    adapter = "openai_chat"
    default_base_url = "https://api.openai.com/v1"
    api_key_env = ("OPENAI_API_KEY",)

    def _user_content(self, m: Message) -> str | list[dict[str, Any]]:
        if isinstance(m.content, str):
            return m.content
        out: list[dict[str, Any]] = []
        for p in m.content:
            if isinstance(p, TextPart):
                out.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                out.append({"type": "image_url", "image_url": {"url": p.as_data_url()}})
        return out

    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest:
        msgs: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                msgs.append({"role": "system", "content": m.text})
            elif m.role == "user":
                msgs.append({"role": "user", "content": self._user_content(m)})
            elif m.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    d["tool_calls"] = [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments_json()}}
                        for tc in m.tool_calls
                    ]
                msgs.append(d)
            elif m.role == "tool":
                content = f"Error: {m.text}" if m.is_error else m.text
                msgs.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": content})

        body: dict[str, Any] = {"model": self.model, "messages": msgs}
        if req.max_tokens is not None:
            # 互換サーバーの多くは max_tokens を受け付ける。必要なら options で max_completion_tokens に変更可
            body[self.options.get("max_tokens_field", "max_tokens")] = req.max_tokens
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.stop:
            body["stop"] = req.stop
        if req.tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools
            ]
            mode, name = normalize_tool_choice(req.tool_choice)
            body["tool_choice"] = {"type": "function", "function": {"name": name}} if mode == "tool" else mode
        if stream:
            body["stream"] = True
            if self.options.get("stream_usage", True):
                body["stream_options"] = {"include_usage": True}
        body = deep_merge(body, req.extra)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.options.get("headers", {}))
        return HTTPRequest(url=f"{self.base_url}/chat/completions", headers=headers, body=body)

    def parse_response(self, data: dict[str, Any]) -> Response:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args, raw = _parse_args(fn.get("arguments"))
            calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args, raw_arguments=raw))
        content = msg.get("content")
        if isinstance(content, list):  # 一部の互換サーバーは配列で返す
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        # DeepSeek は reasoning_content、OpenRouter は reasoning に推論テキストを入れる
        thinking = msg.get("reasoning_content") or msg.get("reasoning")
        stop = "tool_use" if calls else _FINISH.get(choice.get("finish_reason") or "stop", "other")
        u = data.get("usage") or {}
        return Response(
            message=Message(role="assistant", content=content or "", tool_calls=calls),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), details=u),
            provider=self.key,
            model=data.get("model") or self.model,
            raw=data,
            thinking=thinking if isinstance(thinking, str) and thinking else None,
        )

    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]:
        text: list[str] = []
        thinking: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: dict[str, Any] | None = None
        model: str | None = None
        for _event, data in events:
            if data.strip() == "[DONE]":
                break
            obj = json.loads(data)
            if obj.get("error"):
                err = obj["error"]
                raise StreamError(err.get("message", str(err)) if isinstance(err, dict) else str(err), provider=self.key, body=obj)
            model = obj.get("model") or model
            if obj.get("usage"):
                usage = obj["usage"]
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    yield StreamEvent("text", text=delta["content"])
                r = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(r, str) and r:
                    thinking.append(r)
                    yield StreamEvent("thinking", text=r)
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name") and not slot["name"]:
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]

        # 非ストリーミングと同じ形に組み直して parse_response を再利用する
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = [
                {"id": s["id"] or f"call_{i}", "type": "function", "function": {"name": s["name"], "arguments": s["arguments"]}}
                for i, s in sorted(calls.items())
            ]
        if thinking:
            message["reasoning_content"] = "".join(thinking)
        final = {"model": model, "choices": [{"message": message, "finish_reason": finish}], "usage": usage or {}}
        yield StreamEvent("done", response=self.parse_response(final))
