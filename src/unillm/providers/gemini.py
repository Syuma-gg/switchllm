"""Google Gemini API（generateContent / streamGenerateContent）アダプター。

注意点:
- Gemini 3 以降のモデルは、関数呼び出しの往復で thoughtSignature を返さないと 4xx になる。
  同じモデルへの再送では受け取った parts をそのまま返して保持する。
- 他プロバイダーの履歴を Gemini に送るとき（署名が存在しない）は、公式ドキュメントが
  「最終手段」として示しているダミー署名を付ける。性能が落ちる可能性があると明記されている。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

from .._http import HTTPRequest
from ..errors import ConfigurationError, StreamError, UnsupportedFeatureError
from ..types import ImagePart, Message, ProviderState, Response, StreamEvent, TextPart, ToolCall, Usage
from .base import ChatRequest, Provider, deep_merge, normalize_tool_choice

SYNTHETIC_ID_PREFIX = "unillm_"  # Gemini が id を返さなかった呼び出しに付ける仮 ID
DUMMY_THOUGHT_SIGNATURE = "skip_thought_signature_validator"

_FILTERED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "LANGUAGE"}


class GeminiProvider(Provider):
    adapter = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    api_key_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def _user_parts(self, m: Message) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for p in m.parts():
            if isinstance(p, TextPart):
                parts.append({"text": p.text})
            elif isinstance(p, ImagePart):
                if p.url:
                    raise UnsupportedFeatureError(
                        "Gemini generateContent does not accept arbitrary image URLs inline; "
                        "use ImagePart.from_file() / base64 data instead"
                    )
                parts.append({"inlineData": {"mimeType": p.media_type, "data": p.data}})
        return parts

    @staticmethod
    def _tool_response_payload(m: Message) -> dict[str, Any]:
        # functionResponse.response はオブジェクト必須。JSON オブジェクト文字列ならそのまま使う
        try:
            v = json.loads(m.text)
            if isinstance(v, dict) and not m.is_error:
                return v
        except (json.JSONDecodeError, TypeError):
            pass
        return {"error": m.text} if m.is_error else {"result": m.text}

    def build_request(self, req: ChatRequest, *, stream: bool) -> HTTPRequest:
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}

        def add(role: str, parts: list[dict[str, Any]]) -> None:
            if not parts:
                return
            # 並列関数呼び出しの結果は1つの user content にまとめる（FC1, FC2, FR1, FR2 の順が必要）
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": list(parts)})

        inject_dummy = self.options.get("inject_dummy_signatures", True)
        for m in req.messages:
            if m.role == "system":
                continue
            if m.role == "user":
                add("user", self._user_parts(m))
            elif m.role == "assistant":
                for tc in m.tool_calls:
                    call_names[tc.id] = tc.name
                raw = self.native_raw(m)
                if raw is not None:
                    add("model", raw)  # thoughtSignature を含む parts を受け取ったまま返す
                    continue
                parts: list[dict[str, Any]] = [{"text": m.text}] if m.text else []
                for i, tc in enumerate(m.tool_calls):
                    fc: dict[str, Any] = {"name": tc.name, "args": tc.arguments}
                    if not tc.id.startswith(SYNTHETIC_ID_PREFIX):
                        fc["id"] = tc.id
                    part: dict[str, Any] = {"functionCall": fc}
                    if i == 0 and inject_dummy:
                        part["thoughtSignature"] = DUMMY_THOUGHT_SIGNATURE
                    parts.append(part)
                add("model", parts)
            elif m.role == "tool":
                name = m.name or call_names.get(m.tool_call_id or "")
                if not name:
                    raise ConfigurationError(
                        "Gemini needs the function name for tool results; pass name=... to Message.tool_result()"
                    )
                fr: dict[str, Any] = {"name": name, "response": self._tool_response_payload(m)}
                if m.tool_call_id and not m.tool_call_id.startswith(SYNTHETIC_ID_PREFIX):
                    fr["id"] = m.tool_call_id
                add("user", [{"functionResponse": fr}])

        body: dict[str, Any] = {"contents": contents}
        system = self.system_text(req.messages)
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        gen: dict[str, Any] = {}
        if req.max_tokens is not None:
            gen["maxOutputTokens"] = req.max_tokens
        if req.temperature is not None:
            gen["temperature"] = req.temperature
        if req.stop:
            gen["stopSequences"] = req.stop
        if gen:
            body["generationConfig"] = gen
        if req.tools:
            field_name = self.options.get("schema_field", "parameters")  # "parametersJsonSchema" も選べる
            body["tools"] = [
                {"functionDeclarations": [{"name": t.name, "description": t.description, field_name: t.parameters} for t in req.tools]}
            ]
            mode, name = normalize_tool_choice(req.tool_choice)
            cfg: dict[str, Any] = {"mode": {"auto": "AUTO", "none": "NONE", "required": "ANY", "tool": "ANY"}[mode]}
            if name:
                cfg["allowedFunctionNames"] = [name]
            body["toolConfig"] = {"functionCallingConfig": cfg}
        body = deep_merge(body, req.extra)

        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return HTTPRequest(
            url=f"{self.base_url}/models/{self.model}:{method}",
            headers={"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"},
            body=body,
        )

    def parse_response(self, data: dict[str, Any]) -> Response:
        cands = data.get("candidates") or []
        u = data.get("usageMetadata") or {}
        usage = Usage(
            int(u.get("promptTokenCount") or 0),
            # 推論トークンも出力として課金されるので含める（OpenAI / Anthropic と意味を揃える）
            int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
            details=u,
        )
        if not cands:
            # プロンプト自体がブロックされると candidates が空で promptFeedback.blockReason が入る
            return Response(
                message=Message(role="assistant"),
                stop_reason="content_filter" if (data.get("promptFeedback") or {}).get("blockReason") else "other",
                usage=usage,
                provider=self.key,
                model=data.get("modelVersion") or self.model,
                raw=data,
            )
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        texts: list[str] = []
        thinking: list[str] = []
        calls: list[ToolCall] = []
        for p in parts:
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(
                    ToolCall(
                        id=fc.get("id") or f"{SYNTHETIC_ID_PREFIX}{uuid.uuid4().hex[:12]}",
                        name=fc["name"],
                        arguments=fc.get("args") or {},
                    )
                )
            elif "text" in p:
                (thinking if p.get("thought") else texts).append(p["text"])

        finish = cand.get("finishReason")
        if calls:
            stop = "tool_use"
        elif finish in (None, "STOP"):
            stop = "end"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in _FILTERED:
            stop = "content_filter"
        else:
            stop = "other"
        return Response(
            message=Message(
                role="assistant",
                content="".join(texts),
                tool_calls=calls,
                provider_state=ProviderState(self.key, self.model, parts),
            ),
            stop_reason=stop,  # type: ignore[arg-type]
            usage=usage,
            provider=self.key,
            model=data.get("modelVersion") or self.model,
            raw=data,
            thinking="".join(thinking) or None,
        )

    def parse_stream(self, events: Iterator[tuple[str, str]]) -> Iterator[StreamEvent]:
        parts: list[dict[str, Any]] = []
        finish: str | None = None
        usage: dict[str, Any] = {}
        last: dict[str, Any] = {}
        for _event, data in events:
            obj = json.loads(data)
            if obj.get("error"):
                err = obj["error"]
                raise StreamError(err.get("message", str(err)), provider=self.key, body=obj)
            last = obj
            usage = obj.get("usageMetadata") or usage
            for cand in (obj.get("candidates") or [])[:1]:
                finish = cand.get("finishReason") or finish
                for p in (cand.get("content") or {}).get("parts") or []:
                    if "text" in p and "functionCall" not in p:
                        if p["text"]:
                            yield StreamEvent("thinking" if p.get("thought") else "text", text=p["text"])
                        prev = parts[-1] if parts else None
                        # 署名付きパート同士は結合できない（公式ドキュメントの注意）ので、
                        # どちらにも署名がない連続テキストだけを結合する
                        if (
                            prev is not None
                            and "text" in prev
                            and "functionCall" not in prev
                            and bool(prev.get("thought")) == bool(p.get("thought"))
                            and "thoughtSignature" not in prev
                            and "thoughtSignature" not in p
                        ):
                            prev["text"] += p["text"]
                            continue
                    parts.append(dict(p))
        final = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": usage,
            "modelVersion": last.get("modelVersion"),
        }
        if not parts and (last.get("promptFeedback") or {}).get("blockReason"):
            final = {"candidates": [], "promptFeedback": last["promptFeedback"], "usageMetadata": usage}
        yield StreamEvent("done", response=self.parse_response(final))
