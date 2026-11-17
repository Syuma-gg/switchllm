"""Anthropic Messages API（POST /v1/messages）アダプター。"""

from __future__ import annotations

import json
from typing import Any, Iterator

from .._http import HTTPRequest
from ..errors import StreamError
from ..types import ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, deep_merge, normalize_tool_choice

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096  # Anthropic は max_tokens が必須

_STOP = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "content_filter",
}


def _tool_input(buf: str) -> dict[str, Any] | None:
    """ストリーミングで届いた input_json_delta をつないだ文字列を dict にする。
    JSON のオブジェクトとして読めなければ None（max_tokens で途中で切れたときなど）。"""
    if not buf:
        return {}
    try:
        value = json.loads(buf)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class AnthropicProvider(Provider):
    adapter = "anthropic"
    default_base_url = "https://api.anthropic.com"
    api_key_env = ("ANTHROPIC_API_KEY",)
    option_names = frozenset({"default_max_tokens", "anthropic_version", "beta"})
    # Anthropic は、モデルを切り替えるときも thinking ブロックを捨てずにそのまま送ることを求める。
    # 新しいモデルが読めないブロックは、API がエラーにせず、料金もかけずに捨てる
    # （docs/references/anthropic-messages.md の「モデルの切り替え」）
    resend_across_models = True

    def _user_blocks(self, m: Message) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for p in m.parts():
            if isinstance(p, TextPart):
                if p.text:  # 空の text ブロックは拒否される
                    blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                src = (
                    {"type": "url", "url": p.url}
                    if p.url
                    else {"type": "base64", "media_type": p.media_type, "data": p.data}
                )
                blocks.append({"type": "image", "source": src})
        return blocks

    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest:
        msgs: list[dict[str, Any]] = []

        def add(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            # 連続する同じ role は1メッセージにまとめる（並列ツール結果は1つの user メッセージに入れる）
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"].extend(blocks)
            else:
                msgs.append({"role": role, "content": list(blocks)})

        for m in req.messages:
            if m.role == "system":
                continue  # トップレベルの system に集約
            if m.role == "user":
                add("user", self._user_blocks(m))
            elif m.role == "assistant":
                raw = self.native_raw(m)
                if raw is not None:
                    add("assistant", raw)  # thinking（signature 付き）を含めてそのまま返す
                    continue
                blocks = [{"type": "text", "text": m.text}] if m.text else []
                blocks += [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in m.tool_calls]
                add("assistant", blocks)
            elif m.role == "tool":
                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.text}
                if m.is_error:
                    block["is_error"] = True
                add("user", [block])

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens if req.max_tokens is not None else self.options.get("default_max_tokens", DEFAULT_MAX_TOKENS),
            "messages": msgs,
        }
        system = self.system_text(req.messages)
        if system:
            body["system"] = system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.stop:
            body["stop_sequences"] = req.stop
        if req.tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in req.tools]
            if req.tool_choice is not None:  # 省略時の既定は auto（docs/references/anthropic-messages.md）
                mode, name = normalize_tool_choice(req.tool_choice)
                body["tool_choice"] = {
                    "auto": {"type": "auto"},
                    "none": {"type": "none"},
                    "required": {"type": "any"},
                    "tool": {"type": "tool", "name": name},
                }[mode]
        if stream:
            body["stream"] = True
        body = deep_merge(body, req.extra)

        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": self.options.get("anthropic_version", ANTHROPIC_VERSION),
            "content-type": "application/json",
        }
        if self.options.get("beta"):
            headers["anthropic-beta"] = ",".join(self.options["beta"])
        return HTTPRequest(url=f"{self.base_url}/v1/messages", headers=headers, body=body)

    def parse_response(self, data: dict[str, Any]) -> Response:
        blocks = data.get("content") or []
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        thinking = [b.get("thinking", "") for b in blocks if b.get("type") == "thinking"]
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        u = data.get("usage") or {}
        # Anthropic の input_tokens はキャッシュ分を含まないので足して「処理した入力総数」に揃える
        input_tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        stop = "tool_use" if calls else _STOP.get(data.get("stop_reason") or "", "other")
        return Response(
            message=Message(
                role="assistant",
                content="".join(texts),
                tool_calls=calls,
                provider_state=ProviderState(self.key, self.model, blocks),
            ),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=Usage(input_tokens, int(u.get("output_tokens") or 0), details=u),
            provider=self.key,
            model=data.get("model") or self.model,
            raw=data,
            thinking="\n".join(t for t in thinking if t) or None,
        )

    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]:
        message: dict[str, Any] = {"content": [], "usage": {}}
        blocks: dict[int, dict[str, Any]] = {}
        json_buf: dict[int, str] = {}
        broken: dict[str, str] = {}  # tool_use の id → JSON として読めなかった引数の原文
        stopped = False
        for _event, data in events:
            obj = json.loads(data)
            typ = obj.get("type")
            if typ == "message_start":
                message = dict(obj.get("message") or {})
                message.setdefault("usage", {})
            elif typ == "content_block_start":
                idx = obj["index"]
                block = dict(obj.get("content_block") or {})
                if block.get("type") == "tool_use":
                    json_buf[idx] = ""
                blocks[idx] = block
            elif typ == "content_block_delta":
                idx = obj["index"]
                d = obj.get("delta") or {}
                b = blocks.setdefault(idx, {})
                dt = d.get("type")
                if dt == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                    yield StreamEvent("text", text=d.get("text", ""))
                elif dt == "input_json_delta":
                    json_buf[idx] = json_buf.get(idx, "") + d.get("partial_json", "")
                elif dt == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                    yield StreamEvent("thinking", text=d.get("thinking", ""))
                elif dt == "signature_delta":
                    b["signature"] = b.get("signature", "") + d.get("signature", "")
            elif typ == "content_block_stop":
                idx = obj["index"]
                if idx in json_buf:
                    buf = json_buf.pop(idx)
                    args = _tool_input(buf)
                    if args is None:
                        # 他のアダプターと同じく arguments は {} にし、原文を raw_arguments に残す（public-api.md）
                        broken[blocks[idx].get("id", "")] = buf
                    blocks[idx]["input"] = args or {}
            elif typ == "message_delta":
                message.update({k: v for k, v in (obj.get("delta") or {}).items() if v is not None})
                message["usage"].update(obj.get("usage") or {})
            elif typ == "message_stop":
                stopped = True
                break
            elif typ == "error":
                err = obj.get("error") or {}
                raise StreamError(err.get("message", "stream error"), provider=self.key, body=obj)
            # ping や未知のイベントは無視する（Anthropic は新イベント追加の可能性を明言している）
        if not stopped:
            # 最後に必ず message_stop が届く（docs/references/anthropic-messages.md）。届かなければ途中で切れている
            raise StreamError("stream ended before message_stop", provider=self.key)
        message["content"] = [blocks[i] for i in sorted(blocks)]
        res = self.parse_response(message)
        for call in res.tool_calls:
            if call.id in broken:
                call.raw_arguments = broken[call.id]
        yield StreamEvent("done", response=res)
