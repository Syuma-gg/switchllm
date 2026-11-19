"""プロバイダー非依存の共通データ型。

ここで定義する型だけをアプリ側コードで扱えば、プロバイダーを切り替えても
アプリ側のコードは変わらない。各プロバイダー固有の形式への変換は
``switchllm.providers`` 配下のアダプターが担当する。
"""

from __future__ import annotations

import base64
import copy
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union

Role = Literal["system", "user", "assistant", "tool"]

# 正規化した終了理由。プロバイダーごとの値（end_turn / STOP / completed など）をここに寄せる。
StopReason = Literal["end", "tool_use", "max_tokens", "content_filter", "other"]


# --------------------------------------------------------------------------- #
# メッセージ本文のパーツ
# --------------------------------------------------------------------------- #
# 各社が受け付ける画像形式。mimetypes の結果は OS と Python の版で変わる
# （例: Windows の Python 3.12 は .webp を判定できず None を返す）ので、先にこの表を引く
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ImagePart:
    """画像入力。``data``（base64）か ``url`` のどちらかを指定する。"""

    data: str | None = None
    url: str | None = None
    media_type: str = "image/png"
    type: Literal["image"] = "image"

    @classmethod
    def from_file(cls, path: str | Path) -> "ImagePart":
        p = Path(path)
        media_type = _IMAGE_MEDIA_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "image/png"
        return cls(data=base64.b64encode(p.read_bytes()).decode("ascii"), media_type=media_type)

    def as_data_url(self) -> str:
        if self.url:
            return self.url
        return f"data:{self.media_type};base64,{self.data}"


Part = Union[TextPart, ImagePart]


# --------------------------------------------------------------------------- #
# ツール呼び出し
# --------------------------------------------------------------------------- #
@dataclass
class ToolCall:
    """モデルが要求したツール呼び出し。

    ``arguments`` は常に dict に正規化する（OpenAI は JSON 文字列、Anthropic と
    Gemini はオブジェクトで返すため）。JSON として解釈できなかった場合は
    ``arguments`` を空にし、元の文字列を ``raw_arguments`` に残す。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str | None = None

    def arguments_json(self) -> str:
        if not self.arguments and self.raw_arguments:
            return self.raw_arguments
        return json.dumps(self.arguments, ensure_ascii=False)


@dataclass(frozen=True)
class ProviderState:
    """プロバイダーが返した生データ。同じプロバイダー・同じモデルへ履歴を送り返すときに
    そのまま再送するために保持する（Anthropic は、同じプロバイダーなら別のモデルへも送る）。

    Anthropic の thinking ブロック（signature 付き）、Gemini の thoughtSignature、
    OpenAI Responses API の reasoning アイテムは、ツール呼び出しの往復で
    「受け取ったまま返す」ことが求められる。共通型に変換すると失われるので、
    ここに退避しておく。

    Chat Completions 互換では、DeepSeek 形式の ``reasoning_content`` を返したときだけ
    message 全体を持ち、送るときは ``reasoning_content`` だけを使う（ツールを使う会話で
    送り返さないと 400 になる）。
    """

    provider: str
    model: str
    raw: Any


# --------------------------------------------------------------------------- #
# メッセージ
# --------------------------------------------------------------------------- #
@dataclass
class Message:
    role: Role
    content: str | list[Part] = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # role="tool" のときに使う
    tool_call_id: str | None = None
    name: str | None = None
    is_error: bool = False
    # role="assistant" で、モデルの応答から作られたときだけ入る
    provider_state: ProviderState | None = None

    # ---- コンストラクタ ---- #
    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role="system", content=text)

    @classmethod
    def user(cls, content: str | list[Part]) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, text: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=text, tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(
        cls, tool_call_id: str, content: str, *, name: str | None = None, is_error: bool = False
    ) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name, is_error=is_error)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        """dict からメッセージを作る。``to_dict()`` の結果を、推論情報（``provider_state``）ごと元に戻せる。

        ``{"role": "user", "content": "..."}`` のような簡単な形も受け付ける。
        ``content`` が ``None``（OpenAI の形で、ツールだけを呼んだ assistant）なら空にする。
        知らない種類のパーツは変換せずに残し、送る前の検査で ``TypeError`` にする（黙って落とさない）。
        """
        role = d["role"]
        content = d.get("content")
        if role == "tool":
            return cls.tool_result(
                d.get("tool_call_id") or d.get("id") or "",
                "" if content is None else str(content),
                name=d.get("name"),
                is_error=bool(d.get("is_error", False)),
            )
        calls = [
            c if isinstance(c, ToolCall) else ToolCall(
                id=c["id"], name=c["name"], arguments=c.get("arguments") or {}, raw_arguments=c.get("raw_arguments"),
            )
            for c in d.get("tool_calls") or []
        ]
        if content is None:
            content = ""
        elif isinstance(content, list):
            content = [_part_from_dict(p) for p in content]
        state = d.get("provider_state")
        if isinstance(state, dict):
            state = ProviderState(state["provider"], state["model"], state["raw"])
        return cls(role=role, content=content, tool_calls=calls, name=d.get("name"), provider_state=state)

    def to_dict(self) -> dict[str, Any]:
        """JSON にそのまま書ける dict にする。``from_dict()`` で元に戻せる。

        推論情報（``provider_state``）も含むので、保存した会話を復元しても、署名や推論を落とさずに
        送り返せる。``Client.chat()`` は、この dict のリストをそのまま受け取る。既定値の項目は省く。
        """
        d: dict[str, Any] = {"role": self.role}
        d["content"] = self.content if isinstance(self.content, str) else [_part_to_dict(p) for p in self.content]
        if self.tool_calls:
            d["tool_calls"] = [_call_to_dict(tc) for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        if self.is_error:
            d["is_error"] = True
        if self.provider_state is not None:
            st = self.provider_state
            d["provider_state"] = {"provider": st.provider, "model": st.model, "raw": copy.deepcopy(st.raw)}
        return d

    # ---- 便利プロパティ ---- #
    def parts(self) -> list[Part]:
        if isinstance(self.content, str):
            return [TextPart(self.content)] if self.content else []
        return list(self.content)

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text for p in self.content if isinstance(p, TextPart))


def _part_to_dict(p: Any) -> Any:
    if isinstance(p, TextPart):
        return {"type": "text", "text": p.text}
    if isinstance(p, ImagePart):
        out: dict[str, Any] = {"type": "image", "media_type": p.media_type}
        if p.data is not None:
            out["data"] = p.data
        if p.url is not None:
            out["url"] = p.url
        return out
    return p  # 知らないものは変えずに残す（送る前の検査が TypeError にする）


def _part_from_dict(p: Any) -> Any:
    if isinstance(p, dict) and p.get("type") == "text":
        return TextPart(p.get("text", ""))
    if isinstance(p, dict) and p.get("type") == "image":
        return ImagePart(data=p.get("data"), url=p.get("url"), media_type=p.get("media_type", "image/png"))
    return p


def _call_to_dict(tc: ToolCall) -> dict[str, Any]:
    out: dict[str, Any] = {"id": tc.id, "name": tc.name, "arguments": copy.deepcopy(tc.arguments)}
    if tc.raw_arguments is not None:
        out["raw_arguments"] = tc.raw_arguments
    return out


# --------------------------------------------------------------------------- #
# ツール定義・レスポンス
# --------------------------------------------------------------------------- #
@dataclass
class Usage:
    """トークン使用量。

    ``input_tokens`` はキャッシュ読み書き分を含む「処理した入力トークン総数」、
    ``output_tokens`` は推論（thinking/reasoning）トークンを含む出力トークン総数に揃える。
    プロバイダーごとの内訳は ``details`` に生のまま残す。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Response:
    message: Message
    stop_reason: StopReason
    usage: Usage
    provider: str
    model: str
    raw: dict[str, Any]
    thinking: str | None = None
    #: ``response_schema=`` を指定し、``stop_reason`` が ``end`` で、本文が JSON として読めたときだけ、
    #: 読んだ値。それ以外は None（docs/design-docs/structured-output.md）
    parsed: Any = None

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCall]:
        return self.message.tool_calls


@dataclass
class StreamEvent:
    """``Client.stream()`` が返すイベント。

    - ``text``: 本文の差分（``event.text``）
    - ``thinking``: 推論テキストの差分（返すプロバイダーのみ）
    - ``done``: 最後に1回だけ。``event.response`` に組み立て済みの Response が入る
    """

    type: Literal["text", "thinking", "done"]
    text: str = ""
    response: Response | None = None
