"""ドキュメントの機械的な検査（docs/index.md の「機械的な検査」）。

失敗メッセージには直し方を書く。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import unillm
from unillm.providers import PRESETS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ENTRY = ROOT / "CLAUDE.md"
PUBLIC_API = DOCS / "product-specs" / "public-api.md"
MAX_ENTRY_LINES = 100

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_CODE_BLOCK = re.compile(r"```.*?```", re.S)


def _md_files() -> list[Path]:
    return [ENTRY, ROOT / "README.md", ROOT / "ARCHITECTURE.md", *sorted(DOCS.rglob("*.md"))]


def _links(path: Path) -> list[str]:
    """相対リンクだけを返す（URL、ページ内アンカー、コードブロック内は除く）。"""
    text = _CODE_BLOCK.sub("", path.read_text(encoding="utf-8"))
    return [t for t in _LINK.findall(text) if not re.match(r"[a-z]+:", t) and not t.startswith("#")]


def _resolve(path: Path, target: str) -> Path:
    return (path.parent / target.split("#", 1)[0]).resolve()


def _rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


def test_relative_links_resolve():
    broken = [f"{_rel(p)} -> {t}" for p in _md_files() for t in _links(p) if not _resolve(p, t).exists()]
    assert not broken, "リンク先が存在しない。パスを直すか、リンク先の文書を作る:\n" + "\n".join(broken)


def test_every_doc_is_reachable_from_entry():
    seen: set[Path] = set()
    todo = [ENTRY.resolve()]
    while todo:
        p = todo.pop()
        if p in seen or p.suffix != ".md" or not p.exists():
            continue
        seen.add(p)
        todo.extend(_resolve(p, t) for t in _links(p))
    targets = [ROOT / "ARCHITECTURE.md", *DOCS.rglob("*.md")]
    orphans = sorted(_rel(p) for p in targets if p.resolve() not in seen)
    assert not orphans, (
        "CLAUDE.md からリンクを辿って到達できない文書がある。該当ディレクトリの index.md（計画は docs/PLANS.md）から"
        "リンクする:\n" + "\n".join(orphans)
    )


def _index_rules() -> Iterable[tuple[Path, Iterable[Path]]]:
    yield DOCS / "index.md", [*DOCS.glob("*.md"), *DOCS.glob("*/index.md")]
    for name in ("design-docs", "product-specs", "references"):
        yield DOCS / name / "index.md", (DOCS / name).glob("*.md")
    yield DOCS / "PLANS.md", (DOCS / "exec-plans").rglob("*.md")


def test_indexes_list_every_doc_in_their_directory():
    missing = []
    for index, files in _index_rules():
        linked = {_resolve(index, t) for t in _links(index)}
        for p in files:
            if p.resolve() != index.resolve() and p.resolve() not in linked:
                missing.append(f"{_rel(index)} に {_rel(p)} へのリンクがない")
    assert not missing, "索引に載っていない文書がある。索引の表に1行足す:\n" + "\n".join(sorted(missing))


def test_entry_stays_short():
    n = len(ENTRY.read_text(encoding="utf-8").splitlines())
    assert n <= MAX_ENTRY_LINES, (
        f"CLAUDE.md が {n} 行ある（上限 {MAX_ENTRY_LINES}）。CLAUDE.md は地図にとどめ、詳細は docs/ に移してリンクだけ残す"
    )


def _table_names(marker: str) -> set[str]:
    text = PUBLIC_API.read_text(encoding="utf-8")
    m = re.search(rf"<!-- {marker}:start -->(.*?)<!-- {marker}:end -->", text, re.S)
    assert m, f"{_rel(PUBLIC_API)} に <!-- {marker}:start --> と <!-- {marker}:end --> で囲んだ表がない"
    return set(re.findall(r"^\|\s*`([^`]+)`", m.group(1), re.M))


def test_public_api_doc_matches_all():
    documented, actual = _table_names("public-api"), set(unillm.__all__)
    assert documented == actual, (
        f"{_rel(PUBLIC_API)} の一覧と unillm.__all__ が一致しない。同じ変更で両方を直す。\n"
        f"  文書にだけある: {sorted(documented - actual)}\n  __all__ にだけある: {sorted(actual - documented)}"
    )


def test_presets_doc_matches_presets():
    documented, actual = _table_names("presets"), set(PRESETS)
    assert documented == actual, (
        f"{_rel(PUBLIC_API)} のプリセット表と PRESETS が一致しない。プリセットを足すなら base_url の出典も "
        "docs/references/ に書く（docs/design-docs/verified-presets.md）。\n"
        f"  文書にだけある: {sorted(documented - actual)}\n  PRESETS にだけある: {sorted(actual - documented)}"
    )
