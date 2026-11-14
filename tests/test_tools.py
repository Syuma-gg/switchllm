from typing import Annotated, Literal, Optional

from unillm import ToolCall, run_tool_calls, tool


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
