import asyncio
import enum
import warnings
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, Optional, Union

import pytest

from switchllm import ToolCall, arun_tool_calls, run_tool_calls, tool
from switchllm.tools import as_tool


@dataclass
class Order:
    item: str
    qty: int


@tool
def get_weather(
    city: Annotated[str, "都市名"],
    unit: Literal["c", "f"] = "c",
    days: Optional[int] = None,
    tags: list[str] = [],  # noqa: B006 - 利用者が書きがちな可変の既定値でも、任意の引数として扱えるか確かめる
) -> dict:
    """指定した都市の天気を返す。

    詳細説明はスキーマに入れない。
    """
    if city == "error":
        raise ValueError("bad city")
    return {"city": city, "unit": unit, "weather": "晴れ"}


def test_schema_generation():
    assert get_weather.name == "get_weather"
    assert get_weather.description == "指定した都市の天気を返す。"
    assert get_weather.parameters == {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "都市名"},
            "unit": {"enum": ["c", "f"], "type": "string"},
            "days": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["city"],
    }


def test_execute_and_errors():
    ok = get_weather.execute(ToolCall(id="1", name="get_weather", arguments={"city": "東京"}))
    assert ok.role == "tool" and ok.tool_call_id == "1" and '"晴れ"' in ok.text and not ok.is_error
    ng = get_weather.execute(ToolCall(id="2", name="get_weather", arguments={"city": "error"}))
    assert ng.is_error and "ValueError: bad city" in ng.text


def test_run_tool_calls_unknown_tool():
    out = run_tool_calls([get_weather], [ToolCall(id="x", name="missing", arguments={})])
    assert out[0].is_error and "unknown tool" in out[0].text


# --------------------------------------------------------------------------- #
# async def のツール（docs/design-docs/async-client.md）
# --------------------------------------------------------------------------- #
@tool
async def fetch_title(url: str) -> dict:
    """ページの題名を返す。"""
    await asyncio.sleep(0)
    if url == "error":
        raise ConnectionError("unreachable")
    return {"url": url, "title": "題名"}


def test_arun_tool_calls_awaits_async_tools_and_calls_plain_ones():
    calls = [
        ToolCall(id="1", name="fetch_title", arguments={"url": "https://example.com"}),
        ToolCall(id="2", name="get_weather", arguments={"city": "東京"}),
        ToolCall(id="3", name="fetch_title", arguments={"url": "error"}),
        ToolCall(id="4", name="get_weather", arguments={"city": "error"}),
        ToolCall(id="5", name="missing", arguments={}),
        ToolCall(id="6", name="fetch_title", arguments={}, raw_arguments='{"url": "htt'),
    ]
    out = asyncio.run(arun_tool_calls([fetch_title, get_weather], calls))
    assert [m.tool_call_id for m in out] == ["1", "2", "3", "4", "5", "6"]
    assert out[0].text == '{"url": "https://example.com", "title": "題名"}' and not out[0].is_error
    assert out[2].is_error and out[2].text == "ConnectionError: unreachable"
    assert out[5].is_error and out[5].text == 'invalid arguments: not valid JSON: {"url": "htt'
    # 普通の関数のツール・未知のツールは、同期の run_tool_calls と同じ結果になる
    assert [out[1], out[3], out[4]] == run_tool_calls([get_weather], [calls[1], calls[3], calls[4]])


def test_arun_tool_calls_runs_calls_concurrently_and_keeps_their_order():
    # どの呼び出しも、全部が始まるまで終わらない。順に実行していたら待ちきれずに失敗する
    async def main():
        started: list[str] = []
        everyone = asyncio.Event()

        @tool
        async def wait_for_others(name: str, delay: float) -> str:
            """x"""
            started.append(name)
            if len(started) == 3:
                everyone.set()
            await asyncio.wait_for(everyone.wait(), timeout=5)
            await asyncio.sleep(delay)
            return name

        calls = [
            ToolCall(id=n, name="wait_for_others", arguments={"name": n, "delay": d})
            for n, d in [("a", 0.03), ("b", 0.0), ("c", 0.01)]
        ]
        return await arun_tool_calls([wait_for_others], calls)

    out = asyncio.run(main())
    assert [(m.tool_call_id, m.text) for m in out] == [("a", "a"), ("b", "b"), ("c", "c")]  # 終わった順ではない


class _Ready:
    """コルーチンではない awaitable（Future や Task のようなもの）。"""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __await__(self):
        return self.value
        yield  # ジェネレーターにする（__await__ はイテレーターを返す）


def test_tools_returning_other_awaitables_are_awaited_only_by_arun_tool_calls():
    @tool
    def start_job(job: str) -> Any:
        """x"""
        return _Ready({"job": job, "state": "done"})

    call = ToolCall(id="1", name="start_job", arguments={"job": "build"})
    assert asyncio.run(arun_tool_calls([start_job], [call]))[0].text == '{"job": "build", "state": "done"}'
    sync = run_tool_calls([start_job], [call])[0]
    assert sync.is_error and "arun_tool_calls()" in sync.text


def test_run_tool_calls_reports_async_tools_instead_of_sending_a_coroutine():
    # 以前は関数を実行せず、"<coroutine object ...>" を結果としてモデルに返していた
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = run_tool_calls([fetch_title], [ToolCall(id="1", name="fetch_title", arguments={"url": "x"})])
    assert out[0].is_error and out[0].tool_call_id == "1"
    assert out[0].text == "tool 'fetch_title' is an async function; run it with arun_tool_calls()"
    assert [w for w in caught if issubclass(w.category, RuntimeWarning)] == []  # never awaited の警告を出さない


def test_bare_containers_are_supported():
    @tool
    def f(a: tuple, b: set, c: dict, d) -> None:  # d は型ヒントなし → string
        """x"""

    props = f.parameters["properties"]
    assert props == {"a": {"type": "array"}, "b": {"type": "array"}, "c": {"type": "object"}, "d": {"type": "string"}}


@pytest.mark.parametrize("tp", [Order, Any, list[Order]])
def test_unsupported_type_fails_loudly_with_remedy(tp):
    # 黙って string 扱いにせず、引数名と対処法（parameters=）を示して失敗する
    def place_order(order) -> str:
        """注文を確定する。"""

    place_order.__annotations__ = {"order": tp}
    with pytest.raises(TypeError, match=r"'order' of place_order\(\).*@tool\(parameters="):
        tool(place_order)


def test_explicit_parameters_skip_inference_and_still_execute():
    schema = {
        "type": "object",
        "properties": {"order": {"type": "object", "properties": {"item": {"type": "string"}, "qty": {"type": "integer"}}}},
        "required": ["order"],
    }

    @tool(parameters=schema)
    def place_order(order: Order) -> str:
        """注文を確定する。

        詳細。
        """
        return f"ok: {order['item']} x{order['qty']}"

    assert place_order.parameters == schema and place_order.description == "注文を確定する。"
    out = run_tool_calls([place_order], [ToolCall(id="1", name="place_order", arguments={"order": {"item": "本", "qty": 2}})])
    assert out[0].text == "ok: 本 x2" and not out[0].is_error


def test_broken_json_arguments_are_reported_without_calling_the_function():
    # 弱いモデルが壊れた JSON を返したとき、「引数が足りない」ではなく JSON が壊れていると伝える
    called = []

    @tool
    def f(city: str) -> str:
        """x"""
        called.append(city)
        return "ok"

    out = run_tool_calls([f], [ToolCall(id="1", name="f", arguments={}, raw_arguments='{"city": "東')])
    assert out[0].is_error and out[0].text == 'invalid arguments: not valid JSON: {"city": "東'
    assert called == []


def test_empty_object_arguments_still_call_the_function():
    @tool
    def now() -> str:
        """x"""
        return "12:00"

    assert run_tool_calls([now], [ToolCall(id="1", name="now", arguments={}, raw_arguments="{}")])[0].text == "12:00"


def test_non_string_results_are_json_encoded():
    @tool
    def f() -> dict:
        """x"""
        return {"when": date(2026, 11, 15), "ok": True}

    assert f.execute(ToolCall(id="1", name="f")).text == '{"when": "2026-11-15", "ok": true}'


def test_as_tool_accepts_dicts_and_functions():
    anthropic_style = as_tool({"name": "a", "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}}})
    assert anthropic_style.parameters["properties"] == {"x": {"type": "integer"}}
    minimal = as_tool({"name": "b"})
    assert (minimal.description, minimal.parameters) == ("", {"type": "object", "properties": {}})

    def plain(x: int) -> int:
        """そのまま返す。"""
        return x

    assert as_tool(plain).parameters == {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    with pytest.raises(TypeError, match="cannot convert"):
        as_tool(3)


def test_dict_tool_without_function_cannot_be_run():
    definition = as_tool({"name": "get_weather"})
    with pytest.raises(TypeError, match="no Python function"):
        definition(city="東京")
    out = run_tool_calls([{"name": "get_weather"}], [ToolCall(id="1", name="get_weather", arguments={"city": "東京"})])
    assert out[0].is_error and "unknown tool" in out[0].text


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


def test_union_enum_and_int_literal_schemas():
    @tool
    def f(a: Union[int, str], b: Color, c: Literal[1, 2], d: Optional[Union[int, str]] = None, e: Optional[list] = None) -> None:
        """x"""

    assert f.parameters == {
        "type": "object",
        "properties": {
            "a": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "b": {"enum": ["red", "blue"], "type": "string"},
            "c": {"enum": [1, 2], "type": "integer"},
            "d": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "e": {"type": "array"},
        },
        "required": ["a", "b", "c"],
    }


def test_name_and_description_can_be_overridden():
    @tool(name="lookup", description="調べる")
    def f() -> None:
        """使われない説明。"""

    assert (f.name, f.description) == ("lookup", "調べる")


def test_var_args_are_not_part_of_the_schema():
    @tool
    def f(a: int, *args: int, **kwargs: str) -> None:
        """x"""

    assert f.parameters == {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
