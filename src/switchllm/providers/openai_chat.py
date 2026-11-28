"""OpenAI Chat Completions 互換アダプター（POST {base_url}/chat/completions）。

Ollama / LM Studio / vLLM / OpenRouter / Groq / DeepSeek / xAI など、
「OpenAI 互換」を名乗るサーバーはこの形式を話すので、base_url を変えるだけで使える。

DeepSeek 形式の ``reasoning_content`` を返したときだけ、メッセージに ProviderState を付け、
同じプロバイダー・同じモデルへ送るときに assistant メッセージへ ``reasoning_content`` を足す
（docs/references/openai-chat-compatible.md の「推論テキストの送り返し」）。
"""

from __future__ import annotations

import json
from typing import Any

from .._http import HTTPRequest
from ..errors import StreamError, UnsupportedFeatureError
from ..types import FilePart, ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, StreamParser, deep_merge, normalize_tool_choice, schema_name
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
    option_names = frozenset({"max_tokens_field", "stream_usage", "json_schema", "file_input"})
    auth_header = "Authorization"
    # エラーの文言か本文に出てくる名前。reasoning_summary は何も送らないので持たない（docs/design-docs/error-hints.md）
    error_fields = {
        "tool_choice": ("tool_choice",),
        "reasoning_effort": ("reasoning_effort",),
        "response_schema": ("response_format", "json_schema"),
        "temperature": ("temperature",),
    }

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
            elif isinstance(p, FilePart):
                out.append({"type": "file", "file": self._file(p)})
        return out

    def _file(self, p: FilePart) -> dict[str, str]:
        """OpenAI の形で送る。受け付けないと分かっているサーバーと、PDF 以外は送る前に止める
        （docs/references/openai-chat-compatible.md の「ファイルの入力」）。"""
        if not self.options.get("file_input", True):
            raise UnsupportedFeatureError(
                f"{self.key!r} does not support file input (FilePart). Send files with the 'openai:', "
                "'anthropic:' or 'gemini:' provider, or put the file's text in a TextPart"
            )
        if p.media_type != "application/pdf":
            raise UnsupportedFeatureError(
                f"Chat Completions accepts only PDF files, got {p.media_type}. "
                "Use the 'openai:' provider (Responses API) for other file types"
            )
        file = {"file_data": p.as_data_url()}
        if p.filename:
            file["filename"] = p.filename
        return file

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
        if req.response_schema is not None:
            # DeepSeek の response_format は text と json_object だけ（docs/references/openai-chat-compatible.md）。
            # 黙って JSON モードに替えず、送る前に知らせる
            if not self.options.get("json_schema", True):
                raise UnsupportedFeatureError(
                    f"{self.key!r} does not support JSON Schema output (response_schema=). For JSON mode, pass "
                    "extra={'response_format': {'type': 'json_object'}} and describe the JSON format in the prompt"
                )
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name(req.response_schema), "schema": req.response_schema, "strict": True},
            }
        if req.reasoning_effort is not None:
            # OpenAI・DeepSeek・Ollama とも同じ名前のフィールド。名前は変えない（DeepSeek と Ollama は、
            # 文書にない段階を接続先の側で近い段階に置き換える。docs/references/openai-chat-compatible.md）
            body["reasoning_effort"] = req.reasoning_effort
        if req.reasoning_summary and self.base_url == OPENAI_BASE_URL:
            # Chat Completions には要約を頼む標準の指定がないので、互換サーバーには何も足さない（返したら入れる。
            # DeepSeek は頼まなくても返す）。OpenAI 本家の Chat Completions は推論の本文を返さないので、
            # 黙って None にせず止める。プリセットの名前ではなく接続先で決める（docs/design-docs/reasoning-summary.md）
            raise UnsupportedFeatureError(
                "OpenAI Chat Completions does not return reasoning summaries (reasoning_summary=True). "
                "Use the 'openai:' provider (Responses API) instead"
            )
        if stream:
            body["stream"] = True
            if self.options.get("stream_usage", True):
                body["stream_options"] = {"include_usage": True}
        body = deep_merge(body, req.extra)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return HTTPRequest(url=f"{self.base_url}/chat/completions", headers=self.request_headers(headers), body=body)

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

    def stream_parser(self) -> StreamParser:
        return _ChatStream(self)


class _ChatStream(StreamParser):
    """Chat Completions の SSE。差分を集め、最後に非ストリーミングと同じ形に組み直す。"""

    def __init__(self, provider: OpenAIChatProvider) -> None:
        self.provider = provider
        self.text: list[str] = []
        # 推論テキストは、来たフィールド名（reasoning_content / reasoning）ごとに分けて集める。
        # 名前を変えずに組み直さないと、送り返さない reasoning まで送り返すことになる
        self.thinking: dict[str, list[str]] = {}
        self.calls: dict[int, dict[str, str]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.model: str | None = None

    def feed(self, event: str, data: str) -> list[StreamEvent]:
        if data.strip() == "[DONE]":
            self.closed = True
            return []
        obj = json.loads(data)
        if obj.get("error"):
            err = obj["error"]
            raise StreamError(err.get("message", str(err)) if isinstance(err, dict) else str(err), provider=self.provider.key, body=obj)
        self.model = obj.get("model") or self.model
        if obj.get("usage"):
            self.usage = obj["usage"]
        out: list[StreamEvent] = []
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.text.append(delta["content"])
                out.append(StreamEvent("text", text=delta["content"]))
            for field_name in ("reasoning_content", "reasoning"):
                r = delta.get(field_name)
                if isinstance(r, str) and r:
                    self.thinking.setdefault(field_name, []).append(r)
                    out.append(StreamEvent("thinking", text=r))
                    break
            for tc in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(tc.get("index", len(self.calls)), {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name") and not slot["name"]:
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        return out

    def finish(self) -> list[StreamEvent]:
        if not self.closed and self.finish_reason is None:
            # 完了の合図なしに閉じた。途中までを正常終了として返すと、切れたことに気づけない
            raise StreamError("stream ended before finish_reason or [DONE]", provider=self.provider.key)

        # 非ストリーミングと同じ形に組み直して parse_response を再利用する
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.text)}
        if self.calls:
            message["tool_calls"] = [
                {"id": s["id"] or f"call_{i}", "type": "function", "function": {"name": s["name"], "arguments": s["arguments"]}}
                for i, s in sorted(self.calls.items())
            ]
        for field_name, pieces in self.thinking.items():
            message[field_name] = "".join(pieces)
        final = {"model": self.model, "choices": [{"message": message, "finish_reason": self.finish_reason}], "usage": self.usage or {}}
        return [StreamEvent("done", response=self.provider.parse_response(final))]
