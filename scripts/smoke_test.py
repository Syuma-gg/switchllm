"""実 API に対する疎通テスト。手元の APIキーで、各プロバイダーが本当に動くかを確かめる。

    python scripts/smoke_test.py --plan anthropic:claude-sonnet-5-5 anthropic:claude-haiku-5-5 \\
        openai:gpt-5.5 gemini:gemini-flash-latest deepseek:deepseek-flash
    python scripts/smoke_test.py anthropic:claude-sonnet-5-5 anthropic:claude-haiku-5-5 \\
        openai:gpt-5.5 gemini:gemini-flash-latest deepseek:deepseek-flash

**API 料金が発生する**（ollama / lmstudio は除く）。``--plan`` を付けると、何を何回呼ぶかを表示するだけで、
API は呼ばない。先に ``--plan`` で回数を確かめる。

モデルごとに下の確認を行う。そのあと、並べた順で隣どうしのモデル（3つ以上なら最後から最初へも）で、
ツールの往復の途中で切り替え、元のモデルに戻す。同じプロバイダーの2つのモデルを隣に並べると、
同じプロバイダーの中での切り替え（Anthropic の U8 など）を確かめられる。

確認の名前の後ろの U 番号は、docs/exec-plans/active/real-api-verification.md の「未検証の前提」と対応する。
最後に U 番号ごとの結果をまとめて表示するので、それを表の「結果」列に書き写す。

ツールの強制（``tool_choice="required"``）は、Anthropic の一部のモデルと DeepSeek の思考モードで 400 になるので
使わない。``auto`` のまま、プロンプトでツールを使うよう明示する。
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from functools import partial
from typing import Annotated, Any, Callable

from switchllm import Client, Message, Response, SwitchLLMError, parse_model, run_tool_calls, tool
from switchllm.providers import PRESETS

# 思考が既定で有効なモデル（Claude 5・Gemini 3・DeepSeek）では、max_tokens に思考の分も入る。
# 小さいと、答える前に max_tokens で止まる。課金は実際に出力した分だけなので、大きめにしておく
MAX_TOKENS = 4000

ASK_TOOL = "get_weather ツールを必ず使って、東京の天気を調べてから答えて。"
ASK_AGAIN = "ありがとう。同じように get_weather ツールを使って、大阪の天気も調べて。"


@tool
def get_weather(city: Annotated[str, "都市名"]) -> dict:
    """都市の天気を返す。"""
    return {"city": city, "weather": "晴れ"}


TOOLS = [get_weather]


# --------------------------------------------------------------------------- #
# 確認の一覧
# --------------------------------------------------------------------------- #
@dataclass
class Ctx:
    """1つのモデルの確認で共有する状態。usage の確認は、ほかの確認で集めた応答を調べる。"""

    client: Client
    responses: list[Response] = field(default_factory=list)

    @property
    def adapter(self) -> str:
        return self.client.provider.adapter

    def chat(self, messages: Any, client: Client | None = None, **kw: Any) -> Response:
        kw.setdefault("max_tokens", MAX_TOKENS)
        res = (client or self.client).chat(messages, **kw)
        self.responses.append(res)
        return res


@dataclass
class Check:
    name: str
    ids: tuple[str, ...]  # 確かめる未検証の前提
    calls: int  # API を呼ぶ回数（--plan で表示する）
    run: Callable[[Ctx], str]
    adapters: tuple[str, ...] = ()  # 空ならすべてのアダプター

    def applies(self, adapter: str) -> bool:
        return not self.adapters or adapter in self.adapters


def _answered(res: Response, where: str = "") -> None:
    """リクエストが受け付けられ、何かしら答えたこと。ツールをもう一度呼ぶのも答えのうちに入れる。"""
    assert res.text.strip() or res.tool_calls, f"empty response{where} (stop_reason={res.stop_reason})"


def _ask_tool_call(ctx: Ctx, client: Client | None = None) -> tuple[list[Message], Response]:
    msgs = [Message.user(ASK_TOOL)]
    res = ctx.chat(msgs, client, tools=TOOLS)
    assert res.tool_calls, f"no tool call (stop_reason={res.stop_reason}, text={res.text[:60]!r})"
    return msgs, res


def _describe_reasoning(res: Response) -> str:
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


def check_text(ctx: Ctx) -> str:
    res = ctx.chat("「ok」とだけ返して")
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    return f"{res.text.strip()[:30]!r} model={res.model}"


def check_history(ctx: Ctx) -> str:
    # 手で作った assistant メッセージ（生データなし）を、共通型から変換して送る
    res = ctx.chat([
        Message.user("私の名前はタロウです。覚えて。"),
        Message.assistant("わかりました、タロウさん。"),
        Message.user("私の名前は？名前だけを答えて。"),
    ])
    assert "タロウ" in res.text, f"did not recall the name: {res.text[:60]!r}"
    return repr(res.text.strip()[:30])


def check_tools(ctx: Ctx) -> str:
    # 1: ツールを呼ぶ 2: 結果を渡して答える 3: 次のターンでもう一度ツールを使う。
    # DeepSeek は 2・3 で、それまでのすべての reasoning_content を求める。Anthropic は前置きの検査がある
    msgs, first = _ask_tool_call(ctx)
    ids = [tc.id for tc in first.tool_calls]
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    second = ctx.chat(msgs, tools=TOOLS)
    _answered(second, " after tool result")
    msgs += [second.message, Message.user(ASK_AGAIN)]
    third = ctx.chat(msgs, tools=TOOLS)
    _answered(third, " on the next turn")
    synthesized = any(i.startswith("switchllm_") for i in ids)
    return (
        f"{_describe_reasoning(first)} / 呼び出しの id: {'仮 ID（API は id を返さない）' if synthesized else 'API が返した'}"
        f" / 3回目: {'ツール' if third.tool_calls else 'テキスト'}"
    )


def check_tool_choice_none(ctx: Ctx) -> str:
    res = ctx.chat([Message.user(ASK_TOOL)], tools=TOOLS, tool_choice="none")
    assert not res.tool_calls, "tool was called although tool_choice='none'"
    return f"stop_reason={res.stop_reason}"


def check_stream(ctx: Ctx) -> str:
    chunks, done = 0, None
    for ev in ctx.client.stream("1から5まで数えて", max_tokens=MAX_TOKENS):
        chunks += ev.type == "text"
        done = ev.response if ev.type == "done" else done
    assert done is not None, "no done event"
    ctx.responses.append(done)
    assert done.usage.output_tokens > 0, "stream returned no usage"
    return f"{chunks} chunks, output_tokens={done.usage.output_tokens}"


def check_stream_tools(ctx: Ctx) -> str:
    # ストリーミングで組み立てた生データ（DeepSeek の reasoning_content など）を、次のリクエストで送り返す
    msgs = [Message.user(ASK_TOOL)]
    done = None
    for ev in ctx.client.stream(msgs, tools=TOOLS, max_tokens=MAX_TOKENS):
        done = ev.response if ev.type == "done" else done
    assert done is not None, "no done event"
    ctx.responses.append(done)
    assert done.tool_calls, f"no tool call (stop_reason={done.stop_reason})"
    msgs += [done.message, *run_tool_calls(TOOLS, done.tool_calls)]
    _answered(ctx.chat(msgs, tools=TOOLS), " after tool result")
    return _describe_reasoning(done)


def _tool_roundtrip(ctx: Ctx, client: Client, **kw: Any) -> str:
    msgs = [Message.user(ASK_TOOL)]
    first = ctx.chat(msgs, client, tools=TOOLS, **kw)
    assert first.tool_calls, f"no tool call (stop_reason={first.stop_reason})"
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    _answered(ctx.chat(msgs, client, tools=TOOLS, **kw), " after tool result")
    return _describe_reasoning(first)


def check_store_false(ctx: Ctx) -> str:
    # store: false では、input に戻したアイテムの id を API が引けない。reasoning アイテムは encrypted_content で送る
    return _tool_roundtrip(ctx, ctx.client, extra={"store": False})


def check_json_schema(ctx: Ctx) -> str:
    client = ctx.client.with_model(ctx.client.model, schema_field="parametersJsonSchema")
    try:
        return _tool_roundtrip(ctx, client)
    finally:
        client.close()


def check_usage(ctx: Ctx) -> str:
    """ほかの確認で集めた応答の usage が、正規化の前提どおりの内訳になっているかを調べる（API は呼ばない）。"""
    assert ctx.responses, "no responses to inspect"
    problems: list[str] = []
    for res in ctx.responses:
        d = res.usage.details
        if ctx.adapter == "gemini" and "totalTokenCount" in d:
            # 前提: candidatesTokenCount は thoughtsTokenCount を含まない（含むなら total が合わない）
            keys = ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount")
            parts = sum(int(d.get(k) or 0) for k in keys)
            if parts != d["totalTokenCount"]:
                problems.append(f"totalTokenCount {d['totalTokenCount']} != sum of parts {parts} ({d})")
        elif ctx.adapter == "openai_chat" and "total_tokens" in d:
            # 前提: completion_tokens は推論の分を含む
            reasoning = int((d.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            if d["total_tokens"] != int(d.get("prompt_tokens") or 0) + int(d.get("completion_tokens") or 0):
                problems.append(f"total_tokens != prompt_tokens + completion_tokens ({d})")
            if reasoning > int(d.get("completion_tokens") or 0):
                problems.append(f"reasoning_tokens {reasoning} > completion_tokens ({d})")
        elif ctx.adapter == "openai":
            reasoning = int((d.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
            if reasoning > res.usage.output_tokens:
                problems.append(f"reasoning_tokens {reasoning} > output_tokens ({d})")
        # anthropic: input_tokens がキャッシュ分を含まないことは文書で確認済み（U14）。内訳を表示するだけ
    assert not problems, "; ".join(problems)
    return f"{len(ctx.responses)} 件の応答で矛盾なし / 例: {ctx.responses[-1].usage.details}"


# U 番号ごとに、どのプロバイダーで確かめる前提か（プロバイダーキー か アダプター の名前）。
# 確認はどのモデルでも回すが、結果をその U 番号に数えるのは、ここに挙げたものだけ
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
}


def _relevant(ids: tuple[str, ...], model: str) -> tuple[str, ...]:
    key, adapter = parse_model(model)[0], _adapter_of(model)
    return tuple(i for i in ids if key in SCOPE[i] or adapter in SCOPE[i])


def _switch_ids(a: str, b: str) -> tuple[str, ...]:
    """A → B → A の切り替えで確かめる前提。B へ送るときだけでなく、A へ戻すときも、
    相手のモデルが作ったメッセージを送る。"""
    (ka, _), (kb, _) = parse_model(a), parse_model(b)
    ids = []
    if ka != kb and "gemini" in (_adapter_of(a), _adapter_of(b)):
        ids.append("U5")  # 署名のない（別のプロバイダーの）履歴を、ダミー署名を付けて Gemini に送る
    if ka == kb and _adapter_of(a) == "anthropic":
        ids.append("U8")  # Anthropic のモデルどうしで thinking ブロックを送る
    if "deepseek" in (ka, kb):
        ids.append("U17")  # 別のプロバイダー・別のモデルが作った、reasoning_content のない履歴を DeepSeek に送る
    return tuple(ids)


CHECKS = [
    Check("text", ("U12", "U13"), 1, check_text),
    Check("history", ("U2",), 1, check_history),
    Check("tools", ("U3", "U4", "U6", "U7", "U11"), 3, check_tools),
    Check("tool_choice_none", ("U9", "U15"), 1, check_tool_choice_none),
    Check("stream", ("U10", "U16"), 1, check_stream),
    Check("stream_tools", ("U11", "U16"), 2, check_stream_tools),
    Check("store_false", ("U1",), 2, check_store_false, adapters=("openai",)),
    Check("json_schema", ("U4",), 2, check_json_schema, adapters=("gemini",)),
    Check("usage", ("U14", "U18"), 0, check_usage),  # 最後に置く（ほかの確認の応答を調べる）
]

# 切り替え: A でツールを呼ぶ → B で結果を渡して答える → A に戻して次のターン
SWITCH_CALLS = 3


def check_switch(a: Client, b_model: str) -> str:
    ctx = Ctx(a)
    b = a.with_model(b_model)
    try:
        msgs, first = _ask_tool_call(ctx)
        msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
        second = ctx.chat(msgs, b, tools=TOOLS)
        _answered(second, f" on {b_model}")
        msgs += [second.message, Message.user(ASK_AGAIN)]
        third = ctx.chat(msgs, tools=TOOLS)
        _answered(third, " after switching back")
        back = "ツール" if third.tool_calls else "テキスト"
        return f"{_describe_reasoning(first)} → {_describe_reasoning(second)} → 戻して {back}"
    finally:
        b.close()


# --------------------------------------------------------------------------- #
# 実行
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    model: str
    name: str
    ids: tuple[str, ...]
    ok: bool
    info: str


def _run(model: str, name: str, ids: tuple[str, ...], fn: Callable[[], str]) -> Result:
    try:
        return Result(model, name, ids, True, fn())
    except Exception as e:  # noqa: BLE001 - どの例外でも結果に記録し、残りの確認を続ける
        traceback.print_exc()
        return Result(model, name, ids, False, f"{type(e).__name__}: {e}")


def _adapter_of(model: str) -> str:
    key, _ = parse_model(model)
    if key not in PRESETS:
        raise SystemExit(f"unknown provider in {model!r}. Known: {', '.join(sorted(PRESETS))}")
    return PRESETS[key].adapter


def _pairs(models: list[str]) -> list[tuple[str, str]]:
    """隣どうしの組。3つ以上なら最後から最初へも戻る（2つなら A → B だけ。B → A は戻すときに通る）。"""
    if len(models) < 2:
        return []
    if len(models) == 2:
        return [(models[0], models[1])]
    return [(models[i], models[(i + 1) % len(models)]) for i in range(len(models))]


def plan(models: list[str]) -> int:
    total = 0
    for model in models:
        adapter = _adapter_of(model)
        checks = [c for c in CHECKS if c.applies(adapter)]
        calls = sum(c.calls for c in checks)
        total += calls
        print(f"{model}（{adapter}）: {calls} 回")
        for c in checks:
            print(f"  {c.name:16} {c.calls} 回  {','.join(_relevant(c.ids, model)) or '—'}")
    for a, b in _pairs(models):
        print(f"切り替え {a} → {b} → {a}: {SWITCH_CALLS} 回  {','.join(_switch_ids(a, b)) or '—'}")
        total += SWITCH_CALLS
    print(f"合計 {total} 回（リトライがあれば増える）")
    return 0


def main(models: list[str]) -> int:
    for model in models:
        _adapter_of(model)
    # キーがないと、途中まで料金を払ってから止まる。最初にすべてのクライアントを作って確かめる
    try:
        clients = {m: Client(m) for m in models}
    except SwitchLLMError as e:
        print(f"設定の誤り: {e}")
        return 2

    results: list[Result] = []
    try:
        for model, client in clients.items():
            print(f"\n=== {model} ===")
            ctx = Ctx(client)
            for c in CHECKS:
                if c.applies(ctx.adapter):
                    r = _run(model, c.name, _relevant(c.ids, model), partial(c.run, ctx))
                    results.append(r)
                    print(f"  [{'PASS' if r.ok else 'FAIL'}] {r.name:16} {r.info}")
        for a, b in _pairs(models):
            print(f"\n=== 切り替え {a} → {b} → {a} ===")
            r = _run(f"{a} → {b}", "switch", _switch_ids(a, b), partial(check_switch, clients[a], b))
            results.append(r)
            print(f"  [{'PASS' if r.ok else 'FAIL'}] {r.info}")
    finally:
        for client in clients.values():
            client.close()

    print("\n=== U 番号ごとの結果 ===")
    by_id: dict[str, list[Result]] = {}
    for r in results:
        for i in r.ids:
            by_id.setdefault(i, []).append(r)
    for i in sorted(by_id, key=lambda s: int(s[1:])):
        print(f"{i}: " + "、".join(f"{r.model} {r.name} {'PASS' if r.ok else 'FAIL'}" for r in by_id[i]))
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = args[:1] == ["--plan"]
    models = args[1:] if dry_run else args
    if not models:
        print(__doc__)
        sys.exit(2)
    sys.exit(plan(models) if dry_run else main(models))
