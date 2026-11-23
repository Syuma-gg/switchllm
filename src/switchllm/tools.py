"""ツール定義。Python 関数から JSON Schema を自動生成する ``@tool`` デコレーター付き。

自動生成で扱う型は ``SUPPORTED_TYPES`` に固定し、これ以上は広げない
（docs/design-docs/tool-schema-scope.md）。対応外の型はデコレート時に TypeError にして、
``@tool(parameters={...})`` で JSON Schema を直接渡してもらう。
"""

from __future__ import annotations

import asyncio
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
        例外は握りつぶさず ``is_error=True`` の結果としてモデルに返す。

        引数が JSON として壊れていたとき（``arguments`` が空で ``raw_arguments`` だけある）は、
        関数を呼ばずにそのことを返す。空の引数で呼ぶと「引数が足りない」という分かりにくい
        エラーになり、モデルが直して呼び直せないため。

        ``async def`` の関数はここでは実行できないので、``arun_tool_calls()`` を使うよう
        ``is_error=True`` の結果で知らせる（コルーチンを文字列にして返さない）。"""
        invalid = _invalid_arguments(call)
        if invalid is not None:
            return invalid
        try:
            result = self(**call.arguments)
        except Exception as e:  # noqa: BLE001 - モデルにエラー内容を伝えるため
            return _error_result(call, e)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()  # 実行しないコルーチンを閉じ、never awaited の警告を出さない
            return Message.tool_result(
                call.id,
                f"tool {self.name!r} is an async function; run it with arun_tool_calls()",
                name=call.name,
                is_error=True,
            )
        return _result_message(call, result)

    async def _aexecute(self, call: ToolCall) -> Message:
        """``execute`` の非同期版。``async def`` の関数は await し、普通の関数はそのまま呼ぶ。"""
        invalid = _invalid_arguments(call)
        if invalid is not None:
            return invalid
        try:
            result = self(**call.arguments)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001 - モデルにエラー内容を伝えるため
            return _error_result(call, e)
        return _result_message(call, result)


def _invalid_arguments(call: ToolCall) -> Message | None:
    if not call.arguments and call.raw_arguments and not _is_json(call.raw_arguments):
        return Message.tool_result(
            call.id,
            f"invalid arguments: not valid JSON: {call.raw_arguments[:500]}",
            name=call.name,
            is_error=True,
        )
    return None


def _error_result(call: ToolCall, e: Exception) -> Message:
    return Message.tool_result(call.id, f"{type(e).__name__}: {e}", name=call.name, is_error=True)


def _result_message(call: ToolCall, result: Any) -> Message:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return Message.tool_result(call.id, text, name=call.name)


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
    except json.JSONDecodeError:
        return False
    return True


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


def _unknown_tool(call: ToolCall) -> Message:
    return Message.tool_result(call.id, f"unknown tool: {call.name}", name=call.name, is_error=True)


def _runnable(table: dict[str, Tool], call: ToolCall) -> Tool | None:
    """呼び出しの名前のツール。関数のない定義（dict から作ったもの）は実行できないので None。"""
    t = table.get(call.name)
    return None if t is None or t.fn is None else t


def run_tool_calls(tools: Iterable[ToolLike], calls: Iterable[ToolCall]) -> list[Message]:
    """複数のツール呼び出しをまとめて実行する（エージェントループ用の小さなヘルパー）。
    ``async def`` のツールは ``arun_tool_calls()`` で実行する。"""
    table = {t.name: t for t in map(as_tool, tools)}
    out: list[Message] = []
    for call in calls:
        t = _runnable(table, call)
        out.append(_unknown_tool(call) if t is None else t.execute(call))
    return out


async def arun_tool_calls(tools: Iterable[ToolLike], calls: Iterable[ToolCall]) -> list[Message]:
    """``run_tool_calls`` の非同期版（docs/design-docs/async-client.md）。

    ``async def`` のツールは await し、普通の関数はイベントループの中でそのまま呼ぶ。
    呼び出しは ``asyncio.gather`` で並行に実行し、結果は呼び出しの順に返す。"""
    table = {t.name: t for t in map(as_tool, tools)}

    async def one(call: ToolCall) -> Message:
        t = _runnable(table, call)
        return _unknown_tool(call) if t is None else await t._aexecute(call)

    return list(await asyncio.gather(*(one(c) for c in calls)))


# --------------------------------------------------------------------------- #
# 型ヒント → JSON Schema
# --------------------------------------------------------------------------- #
_PRIMITIVES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
_SEQUENCES = (list, tuple, set, frozenset)

#: 自動生成で扱う型の一覧（エラーメッセージと README に出す。増やさない方針）
SUPPORTED_TYPES = "str, int, float, bool, list[T] (tuple/set too), dict, Literal, Enum, Optional/Union, Annotated[T, 'description']"


def _schema_for(tp: Any) -> tuple[dict[str, Any], bool]:
    """(schema, optional) を返す。Annotated[T, "説明"] の文字列メタデータは description に使う。

    対応外の型は TypeError（呼び出し側で引数名を添えて投げ直す）。黙って string にはしない。
    """
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

    if origin in _SEQUENCES:
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
    if tp in _SEQUENCES:
        return {"type": "array"}, False
    raise TypeError(f"unsupported type {tp!r}")


def _parameters_for(f: Callable[..., Any]) -> dict[str, Any]:
    hints = typing.get_type_hints(f, include_extras=True)
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in inspect.signature(f).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        try:
            schema, optional = _schema_for(hints.get(pname, str))  # 型ヒントなしは文字列として扱う
        except TypeError as e:
            raise TypeError(
                f"@tool cannot build a JSON Schema for parameter {pname!r} of {f.__name__}(): {e}. "
                f"Supported types: {SUPPORTED_TYPES}. "
                "For anything else, pass the schema yourself: @tool(parameters={...})"
            ) from None
        props[pname] = schema
        if param.default is inspect.Parameter.empty and not optional:
            required.append(pname)
    params: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        params["required"] = required
    return params


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """関数をツールにする。

    型ヒントと docstring（最初の段落）からツール定義を作る::

        @tool
        def get_weather(city: Annotated[str, "都市名"], unit: Literal["c", "f"] = "c") -> dict:
            '''指定した都市の現在の天気を返す。'''

    ``SUPPORTED_TYPES`` 以外の型（dataclass、Pydantic モデルなど）を使う関数は、
    ``parameters=`` で JSON Schema を直接渡す。型ヒントからの推論は行わない::

        @tool(parameters={"type": "object", "properties": {"order": {...}}, "required": ["order"]})
        def place_order(order: dict) -> str:
            '''注文を確定する。'''
    """

    def wrap(f: Callable[..., Any]) -> Tool:
        doc = inspect.getdoc(f) or ""
        desc = description or doc.split("\n\n")[0].strip() or f.__name__
        params = parameters if parameters is not None else _parameters_for(f)
        return Tool(name=name or f.__name__, description=desc, parameters=params, fn=f)

    return wrap(fn) if fn is not None else wrap
