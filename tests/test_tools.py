from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional

import pytest

from unillm import ToolCall, run_tool_calls, tool


@dataclass
class Order:
    item: str
    qty: int


@tool
def get_weather(
    city: Annotated[str, "都市名"],
    unit: Literal["c", "f"] = "c",
    days: Optional[int] = None,
    tags: list[str] = [],  # noqa: B006
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
