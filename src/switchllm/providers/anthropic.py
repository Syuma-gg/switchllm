"""Anthropic Messages API（POST /v1/messages）アダプター。"""

from __future__ import annotations

import base64
import json
from typing import Any

from .._http import HTTPRequest
from ..errors import StreamError, UnsupportedFeatureError
from ..types import FilePart, ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, StreamParser, deep_merge, normalize_tool_choice

ANTHROPIC_VERSION = "2026-10-21"
# この版から temperature・top_p・top_k が、どのモデルでも 400 になる（docs/design-docs/anthropic-api-version.md）
NO_SAMPLING_VERSION = "2026-10-21"
# Anthropic は max_tokens が必須。省かれたら、提供中のモデルすべての出力の上限（64K〜128K）に収まる値を送る。
# ほかの社は上限を送らずにモデルの上限まで出すので、それに近づける（docs/design-docs/anthropic-max-tokens.md）
DEFAULT_MAX_TOKENS = 64000

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
    auth_header = "x-api-key"
    reserved_headers = (("anthropic-version", "anthropic_version"), ("anthropic-beta", "beta"))
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
            elif isinstance(p, FilePart):
                blocks.append({"type": "document", "source": self._document_source(p)})
        return blocks

    @staticmethod
    def _document_source(p: FilePart) -> dict[str, str]:
        """PDF は base64 のまま、テキストは文字に戻して送る。.docx などは文書が受け付けないと書くので、
        送る前に止める（docs/references/anthropic-messages.md の「ファイルの入力」）。"""
        media_type = p.media_type.lower()
        if media_type == "application/pdf":
            return {"type": "base64", "media_type": "application/pdf", "data": p.data}
        if not (media_type.startswith("text/") or media_type in ("application/json", "application/xml")):
            raise UnsupportedFeatureError(
                f"Anthropic document blocks accept PDF and plain text files, got {p.media_type}. "
                "Convert the file to PDF or text first"
            )
        try:
            raw = base64.b64decode(p.data, validate=True)
        except ValueError:  # binascii.Error と、ASCII でない文字のとき
            raise ValueError(f"FilePart.data is not valid base64 ({p.media_type})") from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedFeatureError(
                f"Anthropic needs text files in UTF-8; this {p.media_type} file is not valid UTF-8"
            ) from None
        return {"type": "text", "media_type": "text/plain", "data": text}

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
        version = str(self.options.get("anthropic_version", ANTHROPIC_VERSION))
        if req.temperature is not None:
            if version >= NO_SAMPLING_VERSION:  # 版は YYYY-MM-DD なので文字列の大小で比べられる
                raise UnsupportedFeatureError(
                    f"Anthropic API version {version} removed temperature (every model returns 400). "
                    "Drop temperature=, or for older models that still accept it, pass anthropic_version='2023-06-01'"
                )
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
        if req.response_schema is not None:
            # name はない。beta ヘッダーも要らない（docs/references/anthropic-messages.md）
            body["output_config"] = {"format": {"type": "json_schema", "schema": req.response_schema}}
        if req.reasoning_effort == "none":
            # effort に none はないが、thinking を切れば同じ意味になる。切れないモデル（Opus 5.5 など）は
            # API のエラーを返す（docs/references/anthropic-messages.md の「推論の強さ」）
            body["thinking"] = {"type": "disabled"}
        elif req.reasoning_effort == "minimal":
            raise UnsupportedFeatureError(
                "Anthropic has no 'minimal' reasoning effort (levels: low, medium, high, xhigh, max). "
                "Use reasoning_effort='low', or 'none' to turn thinking off"
            )
        elif req.reasoning_effort is not None:
            body.setdefault("output_config", {})["effort"] = req.reasoning_effort  # format と並ぶ
        if req.reasoning_summary:
            # display だけでは送れない（type が必須）。Claude 5 系は送らなくても adaptive で考える。
            # Opus 4.6〜4.8・Sonnet 4.6 は考えるようになり、4.5 系は 400（docs/references/anthropic-messages.md）。
            # effort の none とは一緒に来ない（Client が先に止める）
            body["thinking"] = {"type": "adaptive", "display": "summarized"}
        if stream:
            body["stream"] = True
        body = deep_merge(body, req.extra)

        headers = {"anthropic-version": version, "content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        if self.options.get("beta"):
            headers["anthropic-beta"] = ",".join(self.options["beta"])
        return HTTPRequest(url=f"{self.base_url}/v1/messages", headers=self.request_headers(headers), body=body)

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

    def stream_parser(self) -> StreamParser:
        return _MessagesStream(self)


class _MessagesStream(StreamParser):
    """Messages API の SSE。ブロックごとに差分を集め、``message_stop`` で応答を組み直す。"""

    def __init__(self, provider: AnthropicProvider) -> None:
        self.provider = provider
        self.message: dict[str, Any] = {"content": [], "usage": {}}
        self.blocks: dict[int, dict[str, Any]] = {}
        self.json_buf: dict[int, str] = {}
        self.broken: dict[str, str] = {}  # tool_use の id → JSON として読めなかった引数の原文

    def feed(self, event: str, data: str) -> list[StreamEvent]:
        obj = json.loads(data)
        typ = obj.get("type")
        if typ == "message_start":
            self.message = dict(obj.get("message") or {})
            self.message.setdefault("usage", {})
        elif typ == "content_block_start":
            idx = obj["index"]
            block = dict(obj.get("content_block") or {})
            if block.get("type") == "tool_use":
                self.json_buf[idx] = ""
            self.blocks[idx] = block
        elif typ == "content_block_delta":
            return self._delta(obj)
        elif typ == "content_block_stop":
            idx = obj["index"]
            if idx in self.json_buf:
                buf = self.json_buf.pop(idx)
                args = _tool_input(buf)
                if args is None:
                    # 他のアダプターと同じく arguments は {} にし、原文を raw_arguments に残す（public-api.md）
                    self.broken[self.blocks[idx].get("id", "")] = buf
                self.blocks[idx]["input"] = args or {}
        elif typ == "message_delta":
            self.message.update({k: v for k, v in (obj.get("delta") or {}).items() if v is not None})
            self.message["usage"].update(obj.get("usage") or {})
        elif typ == "message_stop":
            self.closed = True
        elif typ == "error":
            err = obj.get("error") or {}
            raise StreamError(err.get("message", "stream error"), provider=self.provider.key, body=obj)
        # ping や未知のイベントは無視する（Anthropic は新イベント追加の可能性を明言している）
        return []

    def _delta(self, obj: dict[str, Any]) -> list[StreamEvent]:
        idx = obj["index"]
        d = obj.get("delta") or {}
        b = self.blocks.setdefault(idx, {})
        dt = d.get("type")
        if dt == "text_delta":
            b["text"] = b.get("text", "") + d.get("text", "")
            return [StreamEvent("text", text=d.get("text", ""))]
        if dt == "input_json_delta":
            self.json_buf[idx] = self.json_buf.get(idx, "") + d.get("partial_json", "")
        elif dt == "thinking_delta":
            b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
            return [StreamEvent("thinking", text=d.get("thinking", ""))]
        elif dt == "signature_delta":
            b["signature"] = b.get("signature", "") + d.get("signature", "")
        return []

    def finish(self) -> list[StreamEvent]:
        if not self.closed:
            # 最後に必ず message_stop が届く（docs/references/anthropic-messages.md）。届かなければ途中で切れている
            raise StreamError("stream ended before message_stop", provider=self.provider.key)
        self.message["content"] = [self.blocks[i] for i in sorted(self.blocks)]
        res = self.provider.parse_response(self.message)
        for call in res.tool_calls:
            if call.id in self.broken:
                call.raw_arguments = self.broken[call.id]
        return [StreamEvent("done", response=res)]
