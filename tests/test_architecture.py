"""層構造とランタイム依存の検査（ARCHITECTURE.md の「モジュールと層」）。

失敗メッセージには直し方を書く。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "switchllm"

#: モジュール → 層。import してよいのは自分より下の層だけ（providers の中どうしは可）
LAYERS = {
    "types": 0,
    "errors": 0,
    "tools": 1,
    "_http": 1,
    "providers": 2,
    "client": 3,
    "__init__": 4,
}
#: 標準ライブラリ以外で使ってよいランタイム依存（docs/design-docs/http-without-sdk.md）
THIRD_PARTY_ALLOWED = {"httpx"}


def _module_key(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imports(path: Path) -> list[tuple[int, str, bool]]:
    """(行番号, 名前, パッケージ内か) の一覧。パッケージ内なら名前は LAYERS のキー。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = ["switchllm", *path.relative_to(SRC).parent.parts]
    out: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                out.append((node.lineno, parts[1], True) if parts[0] == "switchllm" and len(parts) > 1 else (node.lineno, parts[0], False))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                parts = (node.module or "").split(".")
                if parts[0] != "switchllm":
                    out.append((node.lineno, parts[0], False))
                    continue
            else:
                parts = pkg[: len(pkg) - (node.level - 1)] + (node.module.split(".") if node.module else [])
            if len(parts) > 1:
                out.append((node.lineno, parts[1], True))
            else:
                out += [(node.lineno, alias.name, True) for alias in node.names]
    return out


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_every_module_has_a_layer():
    unknown = sorted({_module_key(p) for p in _sources()} - set(LAYERS))
    assert not unknown, f"層が決まっていないモジュールがある: {unknown}。ARCHITECTURE.md の表と LAYERS の両方に足す"


def test_runtime_dependencies_are_stdlib_or_httpx():
    bad = [
        f"{p.relative_to(SRC.parent).as_posix()}:{line} imports {name}"
        for p in _sources()
        for line, name, internal in _imports(p)
        if not internal and name not in sys.stdlib_module_names and name not in THIRD_PARTY_ALLOWED
    ]
    assert not bad, (
        "ランタイム依存は httpx だけの方針（docs/design-docs/http-without-sdk.md）。標準ライブラリで書き直すか、"
        "先に design-docs に判断を残してから THIRD_PARTY_ALLOWED と pyproject.toml を変える:\n" + "\n".join(bad)
    )


def test_imports_point_down_the_layers():
    bad = []
    for p in _sources():
        me = _module_key(p)
        for line, name, internal in _imports(p):
            if not internal:
                continue
            if name not in LAYERS:
                bad.append(f"{p.relative_to(SRC.parent).as_posix()}:{line} imports unknown module {name}")
            elif not (LAYERS[name] < LAYERS[me] or name == me == "providers"):
                bad.append(f"{p.relative_to(SRC.parent).as_posix()}:{line} ({me}, 層{LAYERS[me]}) imports {name} (層{LAYERS[name]})")
    assert not bad, (
        "下の層から上の層を import している（ARCHITECTURE.md の「モジュールと層」）。共通の部分を下の層に移すか、"
        "依存の向きを逆にする:\n" + "\n".join(bad)
    )
