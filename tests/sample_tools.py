"""テストで共有するツール定義。

conftest.py から import しない（e2e/ にも conftest.py があり、両方を一度に回すと
``import conftest`` がどちらを指すか決まらないため。pytest の文書も避けるよう勧めている）。
"""

from __future__ import annotations

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "現在の天気を返す",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
