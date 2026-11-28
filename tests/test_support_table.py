"""public-api.md の「プリセットごとに送れるもの」の表と、ライブラリの振る舞いが一致すること。

表の各セルを、そのプリセットで実際に呼んで確かめる（docs/design-docs/provider-capabilities.md）。
○は送る、×は送る前に UnsupportedFeatureError、△は送らずに捨てて DroppedParameterWarning。
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any, Callable

import pytest

from sample_tools import WEATHER_TOOL
from switchllm import DroppedParameterWarning, FilePart, ImagePart, Message, TextPart, UnsupportedFeatureError
from switchllm.providers import PRESETS

PUBLIC_API = Path(__file__).resolve().parents[1] / "docs" / "product-specs" / "public-api.md"

MODELS = {
    "openai": "openai:gpt-5.5",
    "openai-chat": "openai-chat:gpt-5.5",
    "anthropic": "anthropic:claude-sonnet-5-5",
    "gemini": "gemini:gemini-flash-latest",
    "deepseek": "deepseek:deepseek-v4-pro",
    "ollama": "ollama:llama3.2",
    "lmstudio": "lmstudio:qwen3",
    "azure": "azure:my-gpt",
    "azure-chat": "azure-chat:my-gpt",
    "foundry": "foundry:claude-sonnet-5-5",
    "bedrock": "bedrock:anthropic.claude-sonnet-5-5",
    "vertex": "vertex:gemini-2.5-flash",
}

# 構造化出力でも読める本文（"{}"）で答える、各形式の応答
REPLIES = {
    "openai": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}], "status": "completed"},
    "openai_chat": {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
    "anthropic": {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn", "usage": {}},
    "gemini": {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]},
}

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def _user(*parts: Any) -> list[Message]:
    return [Message.user([TextPart("見て"), *parts])]


# 表の行の名前 → chat() に渡すもの
FEATURES: dict[str, Callable[[], dict[str, Any]]] = {
    "`stop`": lambda: {"messages": "hi", "stop": ["END"]},
    "画像（base64）": lambda: {"messages": _user(ImagePart(data="iVBORw0KGgo=", media_type="image/png"))},
    "画像の URL": lambda: {"messages": _user(ImagePart(url="https://example.com/cat.png", media_type="image/png"))},
    "ファイル（PDF）": lambda: {"messages": _user(FilePart(data="JVBERi0=", media_type="application/pdf", filename="a.pdf"))},
    "ファイル（テキスト）": lambda: {"messages": _user(FilePart(data="aGk=", media_type="text/plain", filename="a.txt"))},
    "`response_schema`": lambda: {"messages": "hi", "response_schema": SCHEMA},
    '`reasoning_effort="none"`': lambda: {"messages": "hi", "reasoning_effort": "none"},
    '`reasoning_effort="minimal"`': lambda: {"messages": "hi", "reasoning_effort": "minimal"},
    '`reasoning_effort="max"`': lambda: {"messages": "hi", "reasoning_effort": "max"},
    "`reasoning_summary=True`": lambda: {"messages": "hi", "reasoning_summary": True},
    '`tool_choice="none"`': lambda: {"messages": "hi", "tools": [WEATHER_TOOL], "tool_choice": "none"},
    "強制する `tool_choice`": lambda: {"messages": "hi", "tools": [WEATHER_TOOL], "tool_choice": "required"},
    "`temperature`": lambda: {"messages": "hi", "temperature": 0.5},
}


def _table() -> tuple[list[str], list[tuple[str, list[str]]]]:
    text = PUBLIC_API.read_text(encoding="utf-8")
    block = text.split("<!-- support:start -->", 1)[1].split("<!-- support:end -->", 1)[0]
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in block.strip().splitlines()]
    header, body = rows[0], [r for r in rows[2:]]
    presets = [re.fullmatch(r"`([^`]+)`", h).group(1) for h in header[1:]]
    return presets, [(r[0], r[1:]) for r in body]


PRESET_COLUMNS, ROWS = _table()
CELLS = [
    pytest.param(feature, preset, mark, id=f"{preset}-{feature}")
    for feature, marks in ROWS
    for preset, mark in zip(PRESET_COLUMNS, marks, strict=True)
]


def test_table_covers_every_preset_and_known_features():
    # プリセットを足したら、表にも列を足す（google は gemini の別名なので列にしない）
    assert set(PRESET_COLUMNS) == set(PRESETS) - {"google"}
    assert set(PRESET_COLUMNS) == set(MODELS)
    assert [f for f, _ in ROWS] == list(FEATURES), "表の行を足したら FEATURES にも足す"
    assert all(len(marks) == len(PRESET_COLUMNS) and set(marks) <= {"○", "×", "△"} for _, marks in ROWS)


@pytest.mark.parametrize(("feature", "preset", "mark"), CELLS)
def test_cell_matches_behavior(api, cloud_env, feature, preset, mark):
    model = MODELS[preset]
    kwargs = FEATURES[feature]()
    messages = kwargs.pop("messages")
    client = api.client(model)
    if mark == "×":
        with pytest.raises(UnsupportedFeatureError):
            client.chat(messages, **kwargs)
        assert api.requests == []
        return
    api.reply_json(REPLIES[PRESETS[preset].adapter])
    if mark == "△":
        with pytest.warns(DroppedParameterWarning):
            client.chat(messages, **kwargs)
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DroppedParameterWarning)
            client.chat(messages, **kwargs)
    assert len(api.requests) == 1
