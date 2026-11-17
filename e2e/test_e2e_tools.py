"""ツールの往復とスキーマの境界（docs/e2e-tests.md の「ツール」）。"""

from __future__ import annotations

from typing import Annotated, Literal

import pytest
from e2e_support import ASK_AGAIN, MAX_TOKENS, TOOLS, answered, ask_tool_call, describe_reasoning, tool_roundtrip

from switchllm import BadRequestError, Message, run_tool_calls, tool


@tool
def get_time() -> str:
    """今の時刻を返す。"""
    return "12:34"


@tool
def search_hotels(
    city: Annotated[str, "都市名"],
    nights: Annotated[int, "泊数"],
    budget: Annotated[float, "1泊あたりの予算（円）"],
    smoking: Annotated[bool, "喫煙できる部屋なら true"],
    room: Annotated[Literal["single", "double"], "部屋の種類"],
    tags: Annotated[list[str], "条件のタグ"],
    memo: Annotated[str | None, "備考"] = None,
) -> dict:
    """条件に合うホテルを探す。"""
    return {"hotels": []}


@tool
def get_stock(item: Annotated[str, "商品名"]) -> dict:
    """商品の在庫数を返す。"""
    raise RuntimeError("在庫システムに接続できません")


@tool
def echo(text: Annotated[str, "そのまま返す文字列"]) -> dict:
    """受け取った文字列をそのまま返す。"""
    return {"echo": text}


@pytest.mark.premise("U3", "U4", "U6", "U7", "U11")
@pytest.mark.calls(3)
def test_tool_roundtrip_three_turns(client, note):
    # 1: ツールを呼ぶ 2: 結果を渡して答える 3: 次のターンでもう一度ツール。
    # DeepSeek は 2・3 で、それまでのすべての reasoning_content を求める。Anthropic は前置きの検査がある
    msgs, first = ask_tool_call(client)
    ids = [tc.id for tc in first.tool_calls]
    msgs += [first.message, *run_tool_calls(TOOLS, first.tool_calls)]
    second = client.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS)
    answered(second, " after tool result")
    msgs += [second.message, Message.user(ASK_AGAIN)]
    third = client.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS)
    answered(third, " on the next turn")
    synthesized = any(i.startswith("switchllm_") for i in ids)
    note(describe_reasoning(first))
    note(f"呼び出しの id: {'仮 ID（API は id を返さない）' if synthesized else 'API が返した'}")
    note(f"3回目: {'ツール' if third.tool_calls else 'テキスト'}")


@pytest.mark.premise("U9", "U15")
@pytest.mark.calls(1)
def test_tool_choice_none(client):
    res = client.chat([Message.user("get_weather ツールで東京の天気を調べて。")], tools=TOOLS,
                      tool_choice="none", max_tokens=MAX_TOKENS)
    assert not res.tool_calls, "tool was called although tool_choice='none'"


@pytest.mark.premise("U15")
@pytest.mark.calls(1)
@pytest.mark.parametrize("choice", ["required", {"name": "get_weather"}], ids=["required", "named"])
def test_forced_tool_choice(client, choice, note):
    # 受け付けるモデルでは指定どおりに呼ぶ。拒否するモデル（Anthropic の一部、DeepSeek の思考モードなど）では
    # API のエラーをそのまま返す（モデルごとの一覧は持たない）
    try:
        res = client.chat("東京の天気を教えて。", tools=TOOLS, tool_choice=choice, max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        assert e.status_code == 400 and e.message
        note(f"API が拒否した: {e.message[:160]}")
        return
    assert res.tool_calls and res.tool_calls[0].name == "get_weather", f"stop_reason={res.stop_reason}"
    note("受け付けて、指定どおりに呼んだ")


@pytest.mark.premise("U4")
@pytest.mark.calls(2)
def test_tool_without_parameters(client, note):
    # 引数のないツールのスキーマは、properties が空のオブジェクトになる
    msgs, res = ask_tool_call(client, "get_time ツールを必ず使って、今の時刻を調べてから答えて。", tools=[get_time])
    assert res.tool_calls[0].name == "get_time"
    assert res.tool_calls[0].arguments == {}, res.tool_calls[0]
    msgs += [res.message, *run_tool_calls([get_time], res.tool_calls)]
    final = client.chat(msgs, tools=[get_time], max_tokens=MAX_TOKENS)
    answered(final, " after tool result")
    note(f"答えに結果が入った: {'12:34' in final.text}")


@pytest.mark.premise("U4")
@pytest.mark.calls(1)
def test_tool_schema_types(client, note):
    # str・int・float・bool・Literal・list[str]・省略できる引数。スキーマを受け付け、型どおりの値で呼ぶか
    _, res = ask_tool_call(
        client,
        "search_hotels ツールを必ず使って、東京で2泊、1泊あたりの予算 15000.5 円、禁煙、ダブルの部屋、"
        "タグは「駅近」と「朝食付き」で探して。",
        tools=[search_hotels],
    )
    args = res.tool_calls[0].arguments
    assert isinstance(args.get("city"), str)
    assert isinstance(args.get("nights"), int) and not isinstance(args.get("nights"), bool)
    assert isinstance(args.get("budget"), (int, float)) and not isinstance(args.get("budget"), bool)
    assert isinstance(args.get("smoking"), bool)
    assert args.get("room") in ("single", "double")
    assert isinstance(args.get("tags"), list) and all(isinstance(t, str) for t in args["tags"])
    assert args.get("memo") is None or isinstance(args["memo"], str)
    note(f"arguments={args}")


@pytest.mark.calls(3)
def test_parallel_tool_calls(client, note):
    # 2つの都市を調べさせる。1回の応答で並列に呼ぶか、順に呼ぶかはモデルに任せる
    msgs = [Message.user("get_weather ツールで、東京と大阪の天気をそれぞれ調べて、まとめて答えて。")]
    counts = []
    res = None
    for _ in range(3):
        res = client.chat(msgs, tools=TOOLS, max_tokens=MAX_TOKENS)
        if not res.tool_calls:
            break
        counts.append(len(res.tool_calls))
        msgs += [res.message, *run_tool_calls(TOOLS, res.tool_calls)]
    assert sum(counts) >= 2, f"tool calls per response: {counts}"
    assert res is not None and not res.tool_calls and res.text.strip(), "did not answer within 3 responses"
    note(f"1回の応答での呼び出し数: {counts}")


@pytest.mark.calls(2)
def test_tool_error_result(client, note):
    # ツールの例外は is_error の結果として返す。各社のエラーの形（is_error / "Error: " の前置き）を受け付けるか
    msgs, res = ask_tool_call(client, "get_stock ツールを必ず使って、りんごの在庫を調べて。", tools=[get_stock])
    results = run_tool_calls([get_stock], res.tool_calls)
    assert results[0].is_error
    msgs += [res.message, *results]
    final = client.chat(msgs, tools=[get_stock], max_tokens=MAX_TOKENS)
    answered(final, " after error result")
    note(f"答え: {final.text.strip()[:60]!r}")


@pytest.mark.calls(2)
def test_unicode_arguments(client, note):
    # 引用符・バックスラッシュ・日本語・絵文字。引数が JSON として読め、同じ文字を結果として送り返せるか
    target = 'He said "こんにちは" \\ 🗾'
    msgs, res = ask_tool_call(client, f"echo ツールに、次の文字列を一字一句変えずに渡して: {target}", tools=[echo])
    call = res.tool_calls[0]
    assert isinstance(call.arguments.get("text"), str), f"arguments={call.arguments} raw={call.raw_arguments!r}"
    assert "こんにちは" in call.arguments["text"]
    msgs += [res.message, *run_tool_calls([echo], res.tool_calls)]
    answered(client.chat(msgs, tools=[echo], max_tokens=MAX_TOKENS), " after unicode result")
    note(f"一字一句同じ: {call.arguments['text'] == target}（{call.arguments['text']!r}）")


@pytest.mark.only("openai")
@pytest.mark.premise("U1")
@pytest.mark.calls(2)
def test_store_false(client, note):
    # store: false では、input に戻したアイテムの id を API が引けない。reasoning アイテムは encrypted_content で送る
    first = tool_roundtrip(client, extra={"store": False})
    note(describe_reasoning(first))


@pytest.mark.only("gemini")
@pytest.mark.premise("U4")
@pytest.mark.calls(2)
def test_parameters_json_schema(client, note):
    other = client.with_model(client.model, schema_field="parametersJsonSchema")
    try:
        first = tool_roundtrip(other)
    finally:
        other.close()
    note(describe_reasoning(first))
