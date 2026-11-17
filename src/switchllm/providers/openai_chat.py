"""OpenAI Chat Completions 互換アダプター（POST {base_url}/chat/completions）。

Ollama / LM Studio / vLLM / OpenRouter / Groq / DeepSeek / xAI など、
「OpenAI 互換」を名乗るサーバーはこの形式を話すので、base_url を変えるだけで使える。

DeepSeek 形式の ``reasoning_content`` を返したときだけ、メッセージに ProviderState を付け、
同じプロバイダー・同じモデルへ送るときに assistant メッセージへ ``reasoning_content`` を足す
（docs/references/openai-chat-compatible.md の「推論テキストの送り返し」）。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from .._http import HTTPRequest
from ..errors import StreamError
from ..types import ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, deep_merge, normalize_tool_choice
from .openai_responses import _parse_args

OPENAI_BASE_URL = "https://api.openai.com/v1"

_FINISH = {
    "stop": "end",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


class OpenAIChatProvider(Provider):
    adapter = "openai_chat"
    default_base_url = OPENAI_BASE_URL
    api_key_env = ("OPENAI_API_KEY",)
    option_names = frozenset({"max_tokens_field", "stream_usage", "headers"})

    def _max_tokens_field(self) -> str:
        """OpenAI 本家は ``max_tokens`` を非推奨とし、o-series とは互換性がないと記載している。
        互換サーバー（Ollama など）の文書には ``max_tokens`` しかないので、本家に送るときだけ変える
        （docs/references/openai-chat-compatible.md）。``max_tokens_field=`` を渡せばそれに従う。"""
        if "max_tokens_field" in self.options:
            return str(self.options["max_tokens_field"])
        return "max_completion_tokens" if self.base_url == OPENAI_BASE_URL else "max_tokens"

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
                # DeepSeek は、ツールを使うリクエストでは過去の reasoning_content をすべて送り返すことを求め、
                # 送らないと 400 にする（ツールがなければ無視する）。生データ全体は送らない。
                # 返ってきた message には、リクエストでは受け付けられないフィールドがありうるため
                raw = self.native_raw(m)
                if isinstance(raw, dict) and isinstance(raw.get("reasoning_content"), str):
                    d["reasoning_content"] = raw["reasoning_content"]
                msgs.append(d)
            elif m.role == "tool":
                content = f"Error: {m.text}" if m.is_error else m.text
                msgs.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": content})

        body: dict[str, Any] = {"model": self.model, "messages": msgs}
        if req.max_tokens is not None:
            body[self._max_tokens_field()] = req.max_tokens
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.stop:
            body["stop"] = req.stop
        if req.tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools
            ]
            # 省略時の既定は auto。Ollama は tool_choice 自体に対応していないので、指定がなければ送らない
            if req.tool_choice is not None:
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
        # DeepSeek は reasoning_content、OpenRouter は reasoning に推論テキストを入れる。
        # 送り返すのは DeepSeek の形式だけ（reasoning を送り返せとする文書は確認していない）
        reasoning = msg.get("reasoning_content")
        has_reasoning = isinstance(reasoning, str) and bool(reasoning)
        state = ProviderState(self.key, self.model, msg) if has_reasoning else None
        thinking = reasoning or msg.get("reasoning")
        stop = "tool_use" if calls else _FINISH.get(choice.get("finish_reason") or "stop", "other")
        u = data.get("usage") or {}
        return Response(
            message=Message(role="assistant", content=content or "", tool_calls=calls, provider_state=state),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), details=u),
            provider=self.key,
            model=data.get("model") or self.model,
            raw=data,
            thinking=thinking if isinstance(thinking, str) and thinking else None,
        )

    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]:
        text: list[str] = []
        # 推論テキストは、来たフィールド名（reasoning_content / reasoning）ごとに分けて集める。
        # 名前を変えずに組み直さないと、送り返さない reasoning まで送り返すことになる
        thinking: dict[str, list[str]] = {}
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: dict[str, Any] | None = None
        model: str | None = None
        got_done = False
        for _event, data in events:
            if data.strip() == "[DONE]":
                got_done = True
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
                for field_name in ("reasoning_content", "reasoning"):
                    r = delta.get(field_name)
                    if isinstance(r, str) and r:
                        thinking.setdefault(field_name, []).append(r)
                        yield StreamEvent("thinking", text=r)
                        break
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

        if not got_done and finish is None:
            # 完了の合図なしに閉じた。途中までを正常終了として返すと、切れたことに気づけない
            raise StreamError("stream ended before finish_reason or [DONE]", provider=self.key)

        # 非ストリーミングと同じ形に組み直して parse_response を再利用する
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = [
                {"id": s["id"] or f"call_{i}", "type": "function", "function": {"name": s["name"], "arguments": s["arguments"]}}
                for i, s in sorted(calls.items())
            ]
        for field_name, pieces in thinking.items():
            message[field_name] = "".join(pieces)
        final = {"model": model, "choices": [{"message": message, "finish_reason": finish}], "usage": usage or {}}
        yield StreamEvent("done", response=self.parse_response(final))
