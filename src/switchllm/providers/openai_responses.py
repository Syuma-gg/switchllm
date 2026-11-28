"""OpenAI Responses API（POST /v1/responses）アダプター。

OpenAI 本家は Chat Completions ではなくこちらを使う。理由:
OpenAI の移行ガイドに「GPT-5.4 以降、Chat Completions は reasoning_effort が none 以外の
ときツール呼び出しに対応しない」とあり、推論とツールを両立するには Responses API が必要なため。
"""

from __future__ import annotations

import json
from typing import Any

from .._http import HTTPRequest
from ..errors import StreamError, UnsupportedFeatureError
from ..types import FilePart, ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, StreamParser, deep_merge, normalize_tool_choice, schema_name


def _parse_args(s: str | None) -> tuple[dict[str, Any], str | None]:
    if not s:
        return {}, s
    try:
        v = json.loads(s)
        return (v if isinstance(v, dict) else {"value": v}), s
    except json.JSONDecodeError:
        return {}, s


class OpenAIResponsesProvider(Provider):
    adapter = "openai"
    default_base_url = "https://api.openai.com/v1"
    api_key_env = ("OPENAI_API_KEY",)
    auth_header = "Authorization"
    auth_scheme = "Bearer"
    # エラーの param（"reasoning.effort" など）か文言に出てくる名前（docs/design-docs/error-hints.md）
    error_fields = {
        "tool_choice": ("tool_choice",),
        "reasoning_effort": ("reasoning.effort", "reasoning_effort"),
        "reasoning_summary": ("reasoning.summary",),
        "response_schema": ("text.format", "response_format", "json_schema"),
        "temperature": ("temperature",),
    }

    # ------------------------------------------------------------------ #
    def _user_content(self, m: Message) -> str | list[dict[str, Any]]:
        if isinstance(m.content, str):
            return m.content
        out: list[dict[str, Any]] = []
        for p in m.content:
            if isinstance(p, TextPart):
                out.append({"type": "input_text", "text": p.text})
            elif isinstance(p, ImagePart):
                out.append({"type": "input_image", "image_url": p.as_data_url()})
            elif isinstance(p, FilePart):
                # 種類は確かめずに送る。受け付ける種類の一覧が長く、拡張子で書かれているため
                # （docs/references/openai-responses.md の「ファイルの入力」）
                item = {"type": "input_file", "file_data": p.as_data_url()}
                if p.filename:
                    item["filename"] = p.filename
                out.append(item)
        return out

    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest:
        items: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                continue  # instructions に集約
            if m.role == "user":
                items.append({"role": "user", "content": self._user_content(m)})
            elif m.role == "assistant":
                raw = self.native_raw(m)
                if raw is not None:
                    # reasoning / message / function_call アイテムを受け取ったまま返す
                    items.extend(raw)
                    continue
                if m.text:
                    items.append({"role": "assistant", "content": m.text})
                for tc in m.tool_calls:
                    items.append(
                        {"type": "function_call", "call_id": tc.id, "name": tc.name, "arguments": tc.arguments_json()}
                    )
            elif m.role == "tool":
                output = f"Error: {m.text}" if m.is_error else m.text
                items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": output})

        body: dict[str, Any] = {"model": self.model, "input": items}
        instructions = self.system_text(req.messages)
        if instructions:
            body["instructions"] = instructions
        if req.max_tokens is not None:
            body["max_output_tokens"] = req.max_tokens
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.stop:
            raise UnsupportedFeatureError("OpenAI Responses API does not support stop sequences")
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    # strict の既定値に依存しないよう明示する（strict は全プロパティ required 等の制約がある）
                    "strict": False,
                }
                for t in req.tools
            ]
            if req.tool_choice is not None:  # 省略時の既定は auto（docs/references/openai-responses.md）
                mode, name = normalize_tool_choice(req.tool_choice)
                body["tool_choice"] = {"type": "function", "name": name} if mode == "tool" else mode
        if req.response_schema is not None:
            # strict が false ではスキーマどおりの出力が保証されない（docs/references/openai-responses.md）
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name(req.response_schema),
                    "schema": req.response_schema,
                    "strict": True,
                }
            }
        if req.reasoning_effort is not None:
            # 7段階とも同じ名前。モデルが受け付けない段階は API のエラーを返す（docs/references/openai-responses.md）
            body["reasoning"] = {"effort": req.reasoning_effort}
        if req.reasoning_summary:
            # 頼まなければ要約は返らない。auto は多くのモデルで detailed と同じ（docs/references/openai-responses.md）
            body.setdefault("reasoning", {})["summary"] = "auto"
        if stream:
            body["stream"] = True
        body = deep_merge(body, req.extra)

        headers = {"Content-Type": "application/json", **self.auth_headers()}
        return HTTPRequest(url=f"{self.base_url}/responses", headers=self.request_headers(headers), body=body)

    # ------------------------------------------------------------------ #
    def parse_response(self, data: dict[str, Any]) -> Response:
        texts: list[str] = []
        thinking: list[str] = []
        calls: list[ToolCall] = []
        refused = False
        output = data.get("output") or []
        for item in output:
            t = item.get("type")
            if t == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        texts.append(c.get("text", ""))
                    elif c.get("type") == "refusal":
                        refused = True
                        texts.append(c.get("refusal", ""))
            elif t == "function_call":
                args, raw = _parse_args(item.get("arguments"))
                calls.append(ToolCall(id=item.get("call_id") or item.get("id", ""), name=item["name"], arguments=args, raw_arguments=raw))
            elif t == "reasoning":
                for s in item.get("summary") or []:
                    if s.get("text"):
                        thinking.append(s["text"])

        reason = (data.get("incomplete_details") or {}).get("reason")
        if calls:
            stop = "tool_use"
        elif reason == "max_output_tokens":
            stop = "max_tokens"
        elif reason == "content_filter" or refused:
            stop = "content_filter"
        elif data.get("status") in (None, "completed"):
            stop = "end"
        else:
            stop = "other"

        u = data.get("usage") or {}
        return Response(
            message=Message(
                role="assistant",
                content="".join(texts),
                tool_calls=calls,
                provider_state=ProviderState(self.key, self.model, output),
            ),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), details=u),
            provider=self.key,
            model=data.get("model") or self.model,
            raw=data,
            thinking="\n".join(thinking) or None,
        )

    def stream_parser(self) -> StreamParser:
        return _ResponsesStream(self)


class _ResponsesStream(StreamParser):
    """Responses API の SSE。``response.completed`` / ``response.incomplete`` に完成した応答が丸ごと入る。"""

    def __init__(self, provider: OpenAIResponsesProvider) -> None:
        self.provider = provider
        self.completed = False

    def feed(self, event: str, data: str) -> list[StreamEvent]:
        if data.strip() == "[DONE]":
            self.closed = True
            return []
        obj = json.loads(data)
        typ = obj.get("type") or event
        if typ == "response.output_text.delta":
            return [StreamEvent("text", text=obj.get("delta", ""))]
        if typ == "response.reasoning_summary_text.delta":
            return [StreamEvent("thinking", text=obj.get("delta", ""))]
        if typ in ("response.completed", "response.incomplete"):
            self.closed = self.completed = True
            return [StreamEvent("done", response=self.provider.parse_response(obj["response"]))]
        if typ == "response.failed":
            err = (obj.get("response") or {}).get("error") or {}
            raise StreamError(err.get("message", "response failed"), provider=self.provider.key, body=obj)
        if typ == "error":
            raise StreamError(obj.get("message") or str(obj), provider=self.provider.key, body=obj)
        return []

    def finish(self) -> list[StreamEvent]:
        if not self.completed:
            raise StreamError("stream ended before response.completed", provider=self.provider.key)
        return []  # done は response.completed で返した
