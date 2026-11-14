"""品質検査（scripts/quality_gate.py）の判定と、検査を黙らせる書き方に理由があることの検査。

ハーネスのテンプレート（quality-gate）の一部で、どのプロジェクトでも同じファイルを使う。
失敗メッセージには直し方を書く。
"""

from __future__ import annotations

import copy

import quality_gate as gate  # scripts/ は pyproject.toml の pytest の pythonpath に入れる


def test_every_noqa_and_pragma_has_a_reason():
    missing = [
        f"{path.relative_to(gate.ROOT).as_posix()}:{h.line}: {h.text}"
        for path in gate.python_files()
        for h in gate.escape_hatches(path.read_text(encoding="utf-8", errors="replace"))
        if h.kind == "comment" and not h.reason
    ]
    assert not missing, (
        "noqa・pragma: no cover に理由がない。同じ行に「# noqa: B006 - 理由」の形で、品質上なぜ必要かを書く。"
        f"数字を通すためだけなら、noqa を外してコードかテストで直す（{gate.doc_path(gate.read_pyproject())}）:\n"
        + "\n".join(missing)
    )


def test_python_files_lists_this_project():
    files = gate.python_files()
    assert gate.ROOT / "scripts" / "quality_gate.py" in files
    assert all(f.suffix == ".py" for f in files)


def test_escape_hatches_reads_comments_and_code_but_not_strings():
    source = "\n".join(
        [
            "import pytest",
            "x = []  # noqa: B006",
            "y = []  # noqa: B006, F401 - スキーマ生成が可変の既定値を扱えるか確かめる",
            "def f():  # pragma: no cover",
            "    pass",
            's = "# noqa: B006 と pytest.mark.skip は文字列の中なら数えない"',
            "@pytest.mark.skip(reason='未対応')",
            "def test_a():",
            "    pytest.skip('理由')",
        ]
    )
    found = [(h.line, h.kind, h.reason) for h in gate.escape_hatches(source)]
    assert found == [
        (2, "comment", ""),
        (3, "comment", "スキーマ生成が可変の既定値を扱えるか確かめる"),
        (4, "comment", ""),
        (7, "skip", ""),
        (9, "skip", ""),
    ]


def test_escape_hatches_survives_broken_source():
    # 書きかけのファイルでもフックを落とさない。読めたところまでの指摘は返す（構文エラーは pytest が報告する）
    found = gate.escape_hatches("x = 1  # noqa\ndef f(:\n")
    assert [(h.line, h.kind) for h in found] == [(1, "comment")]


def test_grown_escape_hatches_counts_by_content_not_by_reason():
    old = "x = []  # noqa: B006\n"
    reworded = "x = []  # noqa: B006 - 理由を足した\n"
    assert gate.grown_escape_hatches(old, reworded) == []  # 理由を書き直しただけでは増えていない

    added = reworded + "y = []  # noqa:B006 - もう1つ\nz = 1  # pragma: no cover - 理由\n"
    assert gate.grown_escape_hatches(old, added) == [
        "noqa:B006 が 1 件から 2 件に増えた（1, 2 行目）",
        "pragma:nocover が 0 件から 1 件に増えた（3 行目）",
    ]
    assert gate.grown_escape_hatches(None, old) == ["noqa:B006 が 0 件から 1 件に増えた（1 行目）"]


BASE = {
    "tool": {
        "ruff": {"lint": {"select": ["F", "E9", "B"]}},
        "coverage": {"run": {"branch": True, "source_pkgs": ["mypkg"]}, "report": {"fail_under": 87}},
        "pytest": {"ini_options": {"testpaths": ["tests"]}},
    }
}


def _with(**changes):
    """BASE の写しに値を入れたものを返す。引数名は設定の場所を ``__`` で区切ったもの。"""
    cfg = copy.deepcopy(BASE)
    for dotted, value in changes.items():
        *parents, last = dotted.split("__")
        cur = cfg
        for key in parents:
            cur = cur.setdefault(key, {})
        cur[last] = value
    return cfg


def test_weakened_settings_reports_each_way_to_loosen_the_gate():
    cases = {
        "fail_under が下がった": _with(tool__coverage__report__fail_under=80),
        "branch を切った": _with(tool__coverage__run__branch=False),
        "select から消した": _with(tool__ruff__lint__select=["F", "E9"]),
        "ignore を足した": _with(tool__ruff__lint__ignore=["B006"]),
        "per-file-ignores を足した": _with(**{"tool__ruff__lint__per-file-ignores": {"tests/*": ["B"]}}),
        "omit を足した": _with(tool__coverage__run__omit=["src/mypkg/_http.py"]),
        "exclude_also を足した": _with(tool__coverage__report__exclude_also=["def __repr__"]),
        "testpaths から消した": _with(tool__pytest__ini_options__testpaths=[]),
        "addopts を変えた": _with(tool__pytest__ini_options__addopts="--deselect tests/test_a.py::test_x"),
    }
    for name, new in cases.items():
        assert len(gate.weakened_settings(BASE, new)) == 1, name

    removed = gate.weakened_settings(BASE, {"tool": {}})  # 節ごと消しても見逃さない
    assert any("fail_under" in s for s in removed) and any("select" in s for s in removed)


def test_weakened_settings_allows_tightening():
    stricter = _with(tool__coverage__report__fail_under=90, tool__ruff__lint__select=["F", "E9", "B", "UP"])
    assert gate.weakened_settings(BASE, stricter) == []
    assert gate.weakened_settings({}, BASE) == []  # 基準を初めて置くのは緩めたことにならない


def test_weakened_settings_reads_both_pytest_styles():
    # pytest 9 からの [tool.pytest] に書き換えただけなら緩んでいない。書き換えた先で減らしたら見つける
    native = _with(tool__pytest={"testpaths": ["tests"]})
    assert gate.weakened_settings(BASE, native) == []
    narrowed = _with(tool__pytest={"testpaths": ["tests/unit"]})
    assert gate.weakened_settings(native, narrowed) == ["tool.pytest.testpaths から削除: tests"]


def test_overrides_pyproject():
    assert gate.overrides_pyproject(".coveragerc", "")  # あるだけで優先される
    assert gate.overrides_pyproject("setup.cfg", "[metadata]\nname = x\n[tool:pytest]\naddopts = -k fast\n")
    assert not gate.overrides_pyproject("setup.cfg", "[metadata]\nname = x\n")  # 検査と関係ない節だけなら見逃す
    assert gate.overrides_pyproject("tox.ini", "[coverage:report]\nfail_under = 0\n")
    assert not gate.overrides_pyproject("README.md", "[pytest]")


def test_project_settings_and_defaults():
    assert gate.doc_path({}) == gate.DEFAULT_DOC
    assert gate.doc_path({"tool": {"quality-gate": {"doc": "docs/q.md"}}}) == "docs/q.md"
    assert gate.testpaths({}) == ["tests"]
    assert gate.testpaths(BASE) == ["tests"]
    assert gate.testpaths({"tool": {"pytest": {"ini_options": {"testpaths": "tests it"}}}}) == ["tests", "it"]


def test_count_tests():
    assert gate.count_tests(["def test_a():\n    pass\nasync def test_b():\n    pass\ndef helper(): pass"]) == 2


FAIL = gate.Failure("pytest", "1 failed")


def test_decide_blocks_on_failure_with_the_principles():
    output, newly = gate.decide([FAIL], ["基準が緩んだ: x"], retrying=False, reported=set(), doc="docs/q.md")
    assert output["decision"] == "block"
    assert "1 failed" in output["reason"] and "基準が緩んだ: x" in output["reason"]
    assert gate.PRINCIPLES in output["reason"]  # 数字を通したくなる瞬間に原則を渡す
    assert "docs/q.md" in output["reason"]  # 詳しい直し方の在りかも渡す
    assert "pytest" in output["systemMessage"]  # ユーザーにも見える
    assert newly == {"基準が緩んだ: x"}


def test_decide_does_not_block_twice_but_tells_the_user():
    # 直せずにもう一度止まろうとしたら止めない（ループを防ぐ）。ユーザーには直接知らせる
    output, _ = gate.decide([FAIL], [], retrying=True, reported=set())
    assert "decision" not in output
    assert "pytest" in output["systemMessage"]


def test_decide_reviews_new_findings_once():
    findings = ["検査を黙らせる書き方が増えた: tests/test_a.py: noqa:B006 が 0 件から 1 件に増えた（3 行目）"]
    output, newly = gate.decide([], findings, retrying=False, reported=set())
    assert output["decision"] == "block" and findings[0] in output["reason"]
    assert gate.PRINCIPLES in output["reason"]
    assert newly == set(findings)

    again, _ = gate.decide([], findings, retrying=False, reported=newly)
    assert again == {}  # 見直し済みの指摘では止めない


def test_decide_passes_when_clean():
    assert gate.decide([], [], retrying=False, reported=set()) == ({}, set())
    assert gate.decide([], [], retrying=True, reported=set()) == ({}, set())
