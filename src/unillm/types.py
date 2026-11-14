"""プロバイダー非依存の共通データ型。

ここで定義する型だけをアプリ側コードで扱えば、プロバイダーを切り替えても
アプリ側のコードは変わらない。各プロバイダー固有の形式への変換は
``unillm.providers`` 配下のアダプターが担当する。
"""

from __future__ import annotations

import base64
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
        media_type = mimetypes.guess_type(p.name)[0] or "image/png"
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
    そのまま再送するために保持する。

    Anthropic の thinking ブロック（signature 付き）、Gemini の thoughtSignature、
    OpenAI Responses API の reasoning アイテムは、ツール呼び出しの往復で
    「受け取ったまま返す」ことが求められる。共通型に変換すると失われるので、
    ここに退避しておく。
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
        """``{"role": "user", "content": "..."}`` 形式の dict を受け付ける。"""
        role = d["role"]
        if role == "tool":
            return cls.tool_result(
                d.get("tool_call_id") or d.get("id") or "",
                str(d.get("content", "")),
                name=d.get("name"),
                is_error=bool(d.get("is_error", False)),
            )
        calls = [
            c if isinstance(c, ToolCall) else ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
            for c in d.get("tool_calls", [])
        ]
        return cls(role=role, content=d.get("content", ""), tool_calls=calls)

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
