"""E2E テストの共通部品（docs/e2e-tests.md）。

モデルごとの制限の一覧は持たない。モデルが拒否してよい指定は、API のエラーをそのまま返すことを確かめる。
"""

from __future__ import annotations

import struct
import zlib
from typing import Annotated, Any

from switchllm import Client, Message, Response, parse_model, run_tool_calls, tool
from switchllm.providers import PRESETS

# 思考が既定で有効なモデル（Claude 5・Gemini 3・DeepSeek）では、max_tokens に思考の分も入る。
# 小さいと、答える前に max_tokens で止まる。課金は実際に出力した分だけなので、大きめにしておく
MAX_TOKENS = 4000

ASK_TOOL = "get_weather ツールを必ず使って、東京の天気を調べてから答えて。"
ASK_AGAIN = "ありがとう。同じように get_weather ツールを使って、大阪の天気も調べて。"

# 画像のテストを常に回すアダプター（今のモデルはどれも画像を受け付ける）。
# Chat Completions 互換はモデルによるので、--vision-models に挙げたものだけ
VISION_ADAPTERS = frozenset({"openai", "anthropic", "gemini"})


@tool
def get_weather(city: Annotated[str, "都市名"]) -> dict:
    """都市の天気を返す。"""
    return {"city": city, "weather": "晴れ"}


TOOLS = [get_weather]


# --------------------------------------------------------------------------- #
# モデル文字列
# --------------------------------------------------------------------------- #
def split_models(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def adapter_of(model: str) -> str:
    key, _ = parse_model(model)
    if key not in PRESETS:
        raise ValueError(f"unknown provider in {model!r}. Known: {', '.join(sorted(PRESETS))}")
    return PRESETS[key].adapter


def requires_key(model: str) -> bool:
    """APIキーが要るか。ローカルのプリセット（ollama / lmstudio）は要らない。"""
    key, _ = parse_model(model)
    return PRESETS[key].api_key_env != ()


def file_input_supported(model: str) -> bool:
    """``FilePart`` を送るか。送らない接続先（DeepSeek・Ollama・LM Studio のプリセット）は、送る前に
    UnsupportedFeatureError にする。モデルごとの一覧ではなく、ライブラリのプリセットの設定を見る。"""
    key, _ = parse_model(model)
    return bool(PRESETS[key].options.get("file_input", True))


def json_schema_supported(model: str) -> bool:
    """``response_schema=`` を送るか。送らない接続先（DeepSeek のプリセット）は、送る前に UnsupportedFeatureError にする。
    モデルごとの一覧ではなく、ライブラリのプリセットの設定を見る。"""
    key, _ = parse_model(model)
    return bool(PRESETS[key].options.get("json_schema", True))


def pairs(models: list[str]) -> list[tuple[str, str]]:
    """隣どうしの組。3つ以上なら最後から最初へも戻る（2つなら A → B だけ。B → A は戻すときに通る）。"""
    if len(models) < 2:
        return []
    if len(models) == 2:
        return [(models[0], models[1])]
    return [(models[i], models[(i + 1) % len(models)]) for i in range(len(models))]


# --------------------------------------------------------------------------- #
# 未検証の前提（docs/exec-plans/active/real-api-verification.md の U 番号）
# --------------------------------------------------------------------------- #
# U 番号ごとに、どのプロバイダーで確かめる前提か（プロバイダーキー か アダプター の名前）。
# テストはどのモデルでも回すが、結果をその U 番号に数えるのは、ここに挙げたものだけ
SCOPE: dict[str, frozenset[str]] = {
    "U1": frozenset({"openai"}),
    "U2": frozenset({"openai"}),
    "U3": frozenset({"openai"}),
    "U4": frozenset({"gemini"}),
    "U6": frozenset({"gemini"}),
    "U7": frozenset({"gemini"}),
    "U9": frozenset({"anthropic"}),
    "U10": frozenset({"openai_chat"}),
    "U11": frozenset({"openai_chat"}),
    "U12": frozenset({"ollama", "lmstudio"}),
    "U13": frozenset({"openai", "anthropic", "gemini"}),
    "U14": frozenset({"anthropic", "gemini"}),
    "U15": frozenset({"ollama"}),
    "U16": frozenset({"openai_chat", "gemini"}),
    "U18": frozenset({"deepseek"}),
    "U19": frozenset({"anthropic"}),
    "U20": frozenset({"gemini"}),
    "U21": frozenset({"ollama", "lmstudio"}),
    "U22": frozenset({"openai"}),
    "U23": frozenset({"openai", "openai-chat"}),
    "U24": frozenset({"anthropic"}),
    "U25": frozenset({"anthropic"}),
    "U26": frozenset({"gemini"}),
    "U27": frozenset({"ollama", "lmstudio"}),
    "U28": frozenset({"openai"}),
    "U29": frozenset({"openai-chat"}),
    "U30": frozenset({"anthropic"}),
    "U31": frozenset({"gemini"}),
    "U32": frozenset({"openai"}),
    "U33": frozenset({"anthropic"}),
    "U34": frozenset({"gemini"}),
    "U35": frozenset({"ollama", "lmstudio"}),
}


def relevant(ids: tuple[str, ...], model: str) -> tuple[str, ...]:
    key, adapter = parse_model(model)[0], adapter_of(model)
    return tuple(i for i in ids if key in SCOPE[i] or adapter in SCOPE[i])


def switch_premises(a: str, b: str) -> tuple[str, ...]:
    """A → B → A の切り替えで確かめる前提。B へ送るときだけでなく、A へ戻すときも、
    相手のモデルが作ったメッセージを送る。"""
    (ka, _), (kb, _) = parse_model(a), parse_model(b)
    ids = []
    if ka != kb and "gemini" in (adapter_of(a), adapter_of(b)):
        ids.append("U5")  # 署名のない（別のプロバイダーの）履歴を、ダミー署名を付けて Gemini に送る
    if ka == kb and adapter_of(a) == "anthropic":
        ids.append("U8")  # Anthropic のモデルどうしで thinking ブロックを送る
    if "deepseek" in (ka, kb):
        ids.append("U17")  # 別のプロバイダー・別のモデルが作った、reasoning_content のない履歴を DeepSeek に送る
    return tuple(ids)


# --------------------------------------------------------------------------- #
# 確かめるための部品
# --------------------------------------------------------------------------- #
def answered(res: Response, where: str = "") -> None:
    """リクエストが受け付けられ、何かしら答えたこと。ツールをもう一度呼ぶのも答えのうちに入れる。"""
    assert res.text.strip() or res.tool_calls, f"empty response{where} (stop_reason={res.stop_reason})"


def ask_tool_call(client: Client, prompt: str = ASK_TOOL, **kw: Any) -> tuple[list[Message], Response]:
    msgs = [Message.user(prompt)]
    kw.setdefault("max_tokens", MAX_TOKENS)
    res = client.chat(msgs, tools=kw.pop("tools", TOOLS), **kw)
    assert res.tool_calls, f"no tool call (stop_reason={res.stop_reason}, text={res.text[:80]!r})"
    return msgs, res


def tool_roundtrip(client: Client, **kw: Any) -> Response:
    """ツールを呼ばせ、結果を渡して答えさせる。最初の応答を返す。"""
    msgs, first = ask_tool_call(client, **kw)
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    kw.setdefault("max_tokens", MAX_TOKENS)
    answered(client.chat(msgs, tools=TOOLS, **kw), " after tool result")
    return first


def describe_reasoning(res: Response) -> str:
    """推論情報が返ったか（再送の確認が意味を持つか）を表示する。"""
    st = res.message.provider_state
    raw = st.raw if st is not None else None
    if isinstance(raw, dict):  # openai_chat（DeepSeek 形式）
        return f"reasoning_content {len(raw.get('reasoning_content') or '')}文字"
    if not isinstance(raw, list):
        return "推論情報なし"
    blocks = [b for b in raw if isinstance(b, dict)]
    counts = (
        ("thinking", sum(b.get("type") in ("thinking", "redacted_thinking") for b in blocks)),  # Anthropic
        ("reasoning", sum(b.get("type") == "reasoning" for b in blocks)),  # OpenAI
        ("thoughtSignature", sum("thoughtSignature" in b for b in blocks)),  # Gemini
    )
    found = [f"{label} {n}" for label, n in counts if n]
    return "推論情報: " + ("、".join(found) if found else "なし")


def usage_problems(adapter: str, res: Response) -> list[str]:
    """usage の内訳が、正規化の前提（docs/design-docs/usage-normalization.md）と矛盾するところ。"""
    d = res.usage.details
    problems: list[str] = []
    if res.usage.input_tokens <= 0 or res.usage.output_tokens <= 0:
        problems.append(f"input/output tokens should be positive: {res.usage}")
    if adapter == "gemini" and "totalTokenCount" in d:
        # 前提: candidatesTokenCount は thoughtsTokenCount を含まない（含むなら total が合わない）
        keys = ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount")
        parts = sum(int(d.get(k) or 0) for k in keys)
        if parts != d["totalTokenCount"]:
            problems.append(f"totalTokenCount {d['totalTokenCount']} != sum of parts {parts}: {d}")
    elif adapter == "openai_chat" and "total_tokens" in d:
        # 前提: completion_tokens は推論の分を含む
        if d["total_tokens"] != int(d.get("prompt_tokens") or 0) + int(d.get("completion_tokens") or 0):
            problems.append(f"total_tokens != prompt_tokens + completion_tokens: {d}")
        reasoning = int((d.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        if reasoning > int(d.get("completion_tokens") or 0):
            problems.append(f"reasoning_tokens {reasoning} > completion_tokens: {d}")
    elif adapter == "openai":
        reasoning = int((d.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        if reasoning > res.usage.output_tokens:
            problems.append(f"reasoning_tokens {reasoning} > output_tokens: {d}")
    # anthropic: input_tokens がキャッシュ分を含まないことは文書で確認済み（U14）。内訳はメモに残す
    return problems


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """単色の PNG を作る（標準ライブラリだけで。画像の依存を増やさないため）。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + bytes(rgb) * size  # 各行の先頭はフィルターの種類（0 = なし）
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit・RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b"")


def text_pdf(text: str) -> bytes:
    """1ページに text を書いた PDF を作る（標準ライブラリだけで。PDF の依存を増やさないため）。
    標準の Type1 フォント（Helvetica）で書くので、text は ASCII にする。"""
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)  # 読み手は、ここに書いた各オブジェクトの位置（バイト）で本文を探す
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
