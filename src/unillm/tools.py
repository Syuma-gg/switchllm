"""ツール定義。Python 関数から JSON Schema を自動生成する ``@tool`` デコレーター付き。"""

from __future__ import annotations

import enum
import inspect
import json
import types
import typing
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .types import Message, ToolCall


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any] | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.fn is None:
            raise TypeError(f"tool {self.name!r} has no Python function attached")
        return self.fn(*args, **kwargs)

    def execute(self, call: ToolCall) -> Message:
        """ツール呼び出しを実行し、履歴にそのまま追加できる tool メッセージを返す。
        例外は握りつぶさず ``is_error=True`` の結果としてモデルに返す。"""
        try:
            result = self(**call.arguments)
        except Exception as e:  # noqa: BLE001 - モデルにエラー内容を伝えるため
            return Message.tool_result(call.id, f"{type(e).__name__}: {e}", name=call.name, is_error=True)
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        return Message.tool_result(call.id, text, name=call.name)


ToolLike = typing.Union[Tool, Callable[..., Any], dict]


def as_tool(obj: ToolLike) -> Tool:
    if isinstance(obj, Tool):
        return obj
    if isinstance(obj, dict):
        return Tool(
            name=obj["name"],
            description=obj.get("description", ""),
            parameters=obj.get("parameters") or obj.get("input_schema") or {"type": "object", "properties": {}},
        )
    if callable(obj):
        return tool(obj)
    raise TypeError(f"cannot convert {obj!r} to Tool")


def run_tool_calls(tools: Iterable[ToolLike], calls: Iterable[ToolCall]) -> list[Message]:
    """複数のツール呼び出しをまとめて実行する（エージェントループ用の小さなヘルパー）。"""
    table = {t.name: t for t in map(as_tool, tools)}
    out: list[Message] = []
    for call in calls:
        t = table.get(call.name)
        if t is None or t.fn is None:
            out.append(Message.tool_result(call.id, f"unknown tool: {call.name}", name=call.name, is_error=True))
        else:
            out.append(t.execute(call))
    return out


# --------------------------------------------------------------------------- #
# 型ヒント → JSON Schema
# --------------------------------------------------------------------------- #
_PRIMITIVES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_for(tp: Any) -> tuple[dict[str, Any], bool]:
    """(schema, optional) を返す。Annotated[T, "説明"] の文字列メタデータは description に使う。"""
    origin = typing.get_origin(tp)

    if origin is typing.Annotated:
        base, *meta = typing.get_args(tp)
        schema, optional = _schema_for(base)
        desc = next((m for m in meta if isinstance(m, str)), None)
        if desc:
            schema = {**schema, "description": desc}
        return schema, optional

    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        optional = len(args) != len(typing.get_args(tp))
        if len(args) == 1:
            schema, _ = _schema_for(args[0])
            return schema, optional
        return {"anyOf": [_schema_for(a)[0] for a in args]}, optional

    if origin is typing.Literal:
        values = list(typing.get_args(tp))
        schema: dict[str, Any] = {"enum": values}
        t = _PRIMITIVES.get(type(values[0]))
        if t:
            schema["type"] = t
        return schema, False

    if origin in (list, tuple, set, frozenset):
        args = typing.get_args(tp)
        items = _schema_for(args[0])[0] if args else {}
        return ({"type": "array", "items": items} if items else {"type": "array"}), False

    if origin is dict or tp is dict:
        return {"type": "object"}, False

    if inspect.isclass(tp) and issubclass(tp, enum.Enum):
        values = [m.value for m in tp]
        schema = {"enum": values}
        t = _PRIMITIVES.get(type(values[0])) if values else None
        if t:
            schema["type"] = t
        return schema, False

    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}, False
    if tp is list:
        return {"type": "array"}, False
    return {"type": "string"}, False


def tool(
    fn: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None
) -> Any:
    """関数をツールにする。

    >>> @tool
    ... def get_weather(city: Annotated[str, "都市名"], unit: Literal["c", "f"] = "c") -> dict:
    ...     '''指定した都市の現在の天気を返す。'''
    """

    def wrap(f: Callable[..., Any]) -> Tool:
        hints = typing.get_type_hints(f, include_extras=True)
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in inspect.signature(f).parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            schema, optional = _schema_for(hints.get(pname, str))
            props[pname] = schema
            if param.default is inspect.Parameter.empty and not optional:
                required.append(pname)
        params: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            params["required"] = required
        doc = inspect.getdoc(f) or ""
        desc = description or doc.split("\n\n")[0].strip() or f.__name__
        return Tool(name=name or f.__name__, description=desc, parameters=params, fn=f)

    return wrap(fn) if fn is not None else wrap
