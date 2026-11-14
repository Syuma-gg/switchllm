"""品質検査（ruff・pytest・カバレッジ）を回し、検査を緩める変更を見つける。

ハーネスのテンプレート（quality-gate）の一部で、どのプロジェクトでも同じファイルを使う。
プロジェクトごとの違いは pyproject.toml（ruff・pytest・coverage の設定と [tool.quality-gate]）に書き、
このファイルは書き換えない。直すときはテンプレートを直して、各プロジェクトに上書きコピーする。

呼び出し元は2つある。

- Claude Code の Stop フック（.claude/settings.json）: 標準入力でフックの JSON を受け取る。
  検査に落ちたら ``{"decision": "block"}`` を返し、Claude に直させてから終わらせる
- CI: ``--ci`` を付ける。落ちたら終了コード 1

置き場所は <プロジェクト>/scripts/quality_gate.py で、scripts/ の親をプロジェクトのルートとみなす。
考え方は [tool.quality-gate] の doc に書いた文書（既定は docs/quality-gates.md）。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 では coverage[toml] が入れる tomli を使う
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOC = "docs/quality-gates.md"
DEFAULT_TESTPATHS = ["tests"]
MAX_DETAIL_LINES = 60

#: pyproject.toml より優先される設定ファイル。置くと pyproject.toml の基準を黙って上書きできる。
#: 値は、ファイルの中にその節があるときだけ優先されるもの（None は、あるだけで優先される）
OVERRIDING_CONFIGS: dict[str, str | None] = {
    ".coveragerc": None,
    ".coveragerc.toml": None,
    "pytest.toml": None,
    ".pytest.toml": None,
    "pytest.ini": None,
    ".pytest.ini": None,
    "ruff.toml": None,
    ".ruff.toml": None,
    "setup.cfg": r"^\[(?:tool:pytest|coverage:)",
    "tox.ini": r"^\[(?:pytest|coverage:)",
}

PRINCIPLES = """\
- 検査は品質を上げるための手段。数字を通すことを目的にしない
- テストには振る舞いを確かめる assert を書く。行を実行するだけのテストは書かない
- 基準（fail_under・ruff のルール・カバレッジやテストの除外）を緩めない。緩める必要があるなら、理由を説明してユーザーの了承を得る
- noqa・pragma: no cover・skip で黙らせない。品質上の理由があるときだけ、理由を同じ行に書いて使い、報告で伝える
- 品質を落とさずには直せないときは、無理に通さない。止まって、状況と選択肢をユーザーに報告する"""


# --------------------------------------------------------------------------- #
# プロジェクトの設定（pyproject.toml）
# --------------------------------------------------------------------------- #
def read_pyproject() -> dict[str, Any]:
    """プロジェクトの pyproject.toml。ない・読めないときは空（読めないことは pytest と ruff が報告する）。"""
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _lookup(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _pytest_options(cfg: dict[str, Any]) -> dict[str, Any]:
    """pytest の設定。[tool.pytest.ini_options]（従来の書き方）と [tool.pytest]（pytest 9 からの書き方）のどちらでも読む。"""
    table = _lookup(cfg, "tool.pytest")
    if not isinstance(table, dict):
        return {}
    ini = table.get("ini_options")
    return ini if isinstance(ini, dict) else {k: v for k, v in table.items() if k != "ini_options"}


def _setting(cfg: dict[str, Any], key: str) -> Any:
    """``tool.pytest.<名前>`` は書き方によらず pytest の設定から、それ以外はそのままの場所から引く。"""
    if key.startswith("tool.pytest."):
        return _pytest_options(cfg).get(key.removeprefix("tool.pytest."))
    return _lookup(cfg, key)


def doc_path(cfg: dict[str, Any]) -> str:
    """検査に落ちたときに Claude に案内する文書（[tool.quality-gate] の doc）。"""
    value = _lookup(cfg, "tool.quality-gate.doc")
    return value if isinstance(value, str) and value else DEFAULT_DOC


def testpaths(cfg: dict[str, Any]) -> list[str]:
    """テストを置くフォルダー（pytest の testpaths）。テスト関数が減っていないかを、この中で数える。"""
    value = _pytest_options(cfg).get("testpaths")
    paths = value.split() if isinstance(value, str) else [str(v) for v in value or []]
    return paths or list(DEFAULT_TESTPATHS)


# --------------------------------------------------------------------------- #
# 検査（落ちたら直すまで終わらせない）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Failure:
    check: str
    detail: str


def _run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
    )


def _tail(text: str, n: int = MAX_DETAIL_LINES) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join(["...（前略）", *lines[-n:]])


def run_checks() -> list[Failure]:
    """ruff → pytest（カバレッジを測りながら）→ カバレッジの基準、の順に回す。

    どのファイルを検査するかは各ツールの設定に任せる（ruff はプロジェクト全体から、設定の exclude を除いたもの）。
    """
    py = sys.executable
    failures: list[Failure] = []

    r = _run([py, "-m", "ruff", "check", "--output-format", "concise"])
    if r.returncode != 0:
        failures.append(Failure("ruff", _tail(r.stdout + r.stderr)))

    # 利用者の .coverage と pytest のキャッシュを上書きしない
    data_file = Path(tempfile.gettempdir()) / f"quality-gate-{os.getpid()}.coverage"
    env = {"COVERAGE_FILE": str(data_file)}
    try:
        r = _run([py, "-m", "coverage", "run", "-m", "pytest", "-q", "-p", "no:cacheprovider"], env)
        if r.returncode != 0:
            failures.append(Failure("pytest", _tail(r.stdout + r.stderr)))
        else:  # テストが落ちているときのカバレッジは意味がないので測らない
            r = _run([py, "-m", "coverage", "report"], env)
            if r.returncode != 0:
                hint = "Missing 列の行が、どの振る舞いのためにあるかを読み、それを確かめるテストを足す。"
                failures.append(Failure("coverage", _tail(r.stdout + r.stderr) + "\n" + hint))
    finally:
        data_file.unlink(missing_ok=True)
    return failures


# --------------------------------------------------------------------------- #
# 見直し（検査を緩める変更。同じ指摘では1回だけ止める）
# --------------------------------------------------------------------------- #
_DIRECTIVE = re.compile(
    r"#\s*(?P<directive>noqa(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?|pragma:\s*no\s*(?:cover|branch))"
    r"(?P<rest>.*)$"
)
_SKIP = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail|importorskip)\(")
_REASON_SEPARATORS = " \t-–—:#"
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.M)


@dataclass(frozen=True)
class EscapeHatch:
    """検査を黙らせる書き方。kind は "comment"（noqa・pragma: no cover）か "skip"（skip・xfail）。

    signature は理由を除いた中身（例: ``noqa:B006``）。理由を書き直しただけの変更を、増えたと数えないために使う。
    """

    line: int
    kind: str
    text: str
    signature: str
    reason: str = ""


def escape_hatches(source: str) -> list[EscapeHatch]:
    """コメントの noqa・pragma と、コードの skip・xfail を返す。文字列の中は数えない。"""
    lines = source.splitlines()
    found: list[EscapeHatch] = []
    code: dict[int, list[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                m = _DIRECTIVE.search(tok.string)
                if m:
                    signature = re.sub(r"\s+", "", m["directive"])
                    reason = m["rest"].strip(_REASON_SEPARATORS)
                    found.append(EscapeHatch(tok.start[0], "comment", tok.string, signature, reason))
            elif tok.type in (tokenize.NAME, tokenize.OP):
                code.setdefault(tok.start[0], []).append(tok.string)
    except (tokenize.TokenError, SyntaxError):
        return found  # 構文エラーは pytest と ruff が報告する
    for line, tokens in code.items():
        joined = "".join(tokens)  # 文字列（skip の理由）は入らない
        if _SKIP.search(joined):
            found.append(EscapeHatch(line, "skip", lines[line - 1].strip(), joined))
    return sorted(found, key=lambda h: h.line)


def grown_escape_hatches(old_source: str | None, new_source: str) -> list[str]:
    """old から new で数が増えた、検査を黙らせる書き方を返す（old が None なら新しいファイル）。

    中身（signature）ごとに数を比べるので、理由の書き直しや行の移動は増えたと数えない。
    """
    before = Counter(h.signature for h in escape_hatches(old_source or ""))
    now = escape_hatches(new_source)
    after = Counter(h.signature for h in now)
    out = []
    for sig in sorted(after):
        if after[sig] > before[sig]:
            lines = ", ".join(str(h.line) for h in now if h.signature == sig)
            out.append(f"{sig} が {before[sig]} 件から {after[sig]} 件に増えた（{lines} 行目）")
    return out


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):  # per-file-ignores: {パターン: [コード]}
        return {f"{k}: {c}" for k, codes in value.items() for c in ([codes] if isinstance(codes, str) else codes)}
    return {str(v) for v in value}


#: 項目が減ると検査が緩む設定（tool.pytest.* は [tool.pytest.ini_options] と [tool.pytest] のどちらの書き方でも読む）
_SHRINK_IS_WEAKER = [
    "tool.ruff.lint.select",
    "tool.ruff.lint.extend-select",
    "tool.ruff.select",
    "tool.ruff.extend-select",
    "tool.coverage.run.source",
    "tool.coverage.run.source_pkgs",
    "tool.pytest.testpaths",
    "tool.pytest.python_files",
    "tool.pytest.python_classes",
    "tool.pytest.python_functions",
]
#: 項目が増えると検査が緩む設定
_GROW_IS_WEAKER = [
    "tool.ruff.exclude",
    "tool.ruff.extend-exclude",
    "tool.ruff.lint.exclude",
    "tool.ruff.lint.ignore",
    "tool.ruff.lint.extend-ignore",
    "tool.ruff.lint.per-file-ignores",
    "tool.ruff.lint.extend-per-file-ignores",
    "tool.ruff.ignore",
    "tool.ruff.extend-ignore",
    "tool.ruff.per-file-ignores",
    "tool.ruff.extend-per-file-ignores",
    "tool.coverage.run.omit",
    "tool.coverage.report.omit",
    "tool.coverage.report.exclude_lines",
    "tool.coverage.report.exclude_also",
    "tool.coverage.report.partial_branches",
    "tool.pytest.norecursedirs",
]
_FAIL_UNDER = "tool.coverage.report.fail_under"
_BRANCH = "tool.coverage.run.branch"
#: 中身を読まないと緩んだか分からないので、変わったら見直させる設定
_ANY_CHANGE = ["tool.pytest.addopts"]


def weakened_settings(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """pyproject.toml の検査の基準のうち、old から new で緩んだものを返す。厳しくなった変更は返さない。"""
    out: list[str] = []
    before, after = _lookup(old, _FAIL_UNDER), _lookup(new, _FAIL_UNDER)
    if before is not None and (after is None or after < before):
        out.append(f"{_FAIL_UNDER}: {before} → {after}")
    if _lookup(old, _BRANCH) and not _lookup(new, _BRANCH):
        out.append(f"{_BRANCH}: true → {_lookup(new, _BRANCH)}")
    for key in _SHRINK_IS_WEAKER:
        removed = _as_set(_setting(old, key)) - _as_set(_setting(new, key))
        if removed:
            out.append(f"{key} から削除: {', '.join(sorted(removed))}")
    for key in _GROW_IS_WEAKER:
        added = _as_set(_setting(new, key)) - _as_set(_setting(old, key))
        if added:
            out.append(f"{key} に追加: {', '.join(sorted(added))}")
    for key in _ANY_CHANGE:
        if _setting(old, key) != _setting(new, key):
            out.append(f"{key}: {_setting(old, key)!r} → {_setting(new, key)!r}")
    return out


def overrides_pyproject(name: str, text: str) -> bool:
    """ルートにある name というファイルが、pyproject.toml の検査の設定より優先されるか。"""
    if name not in OVERRIDING_CONFIGS:
        return False
    section = OVERRIDING_CONFIGS[name]
    return section is None or re.search(section, text, re.M) is not None


def count_tests(sources: Iterable[str]) -> int:
    return sum(len(_TEST_DEF.findall(s)) for s in sources)


def _git(*args: str) -> str | None:
    r = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return r.stdout if r.returncode == 0 else None


def _git_paths(command: str, *args: str) -> list[str] | None:
    """パスを返す git のコマンドを -z 付きで回す（日本語のファイル名がエスケープされないように）。"""
    out = _git(command, "-z", *args)  # -z は "--" より前に置く（後ろだとパスとして読まれる）
    return None if out is None else [p for p in out.split("\0") if p]


_SKIPPED_DIRS = {"venv", "__pycache__", "build", "dist", "node_modules", "site-packages"}


def python_files() -> list[Path]:
    """プロジェクトの .py ファイル（git の管理下と、無視していない新しいファイル）。

    git がない（sdist を展開した先など）ときは、仮想環境や隠しフォルダーを除いてフォルダーを辿る。
    """
    listed = _git_paths("ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py")
    if listed is not None:
        return sorted({ROOT / p for p in listed if (ROOT / p).is_file()})

    def skipped(path: Path) -> bool:
        dirs = path.relative_to(ROOT).parts[:-1]
        return any(d.startswith(".") or d in _SKIPPED_DIRS or d.endswith(".egg-info") for d in dirs)

    return sorted(p for p in ROOT.rglob("*.py") if not skipped(p))


def review_changes() -> list[str]:
    """HEAD（最後のコミット）からの変更のうち、検査を緩めるものを返す。"""
    findings: list[str] = []

    new: dict[str, Any] | None = {}
    try:
        new = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError:
        pass  # pyproject.toml を消したなら、HEAD の基準がすべて消えたと報告する
    except tomllib.TOMLDecodeError as e:
        findings.append(f"pyproject.toml を読めない: {e}")
        new = None
    old_text = _git("show", "HEAD:./pyproject.toml")
    if old_text is not None and new is not None:
        try:
            findings += [f"基準が緩んだ: {s}" for s in weakened_settings(tomllib.loads(old_text), new)]
        except tomllib.TOMLDecodeError:
            pass  # HEAD の pyproject.toml が壊れていたなら、比べる基準がない

    for name in OVERRIDING_CONFIGS:
        file = ROOT / name
        if file.is_file() and overrides_pyproject(name, file.read_text(encoding="utf-8", errors="replace")):
            findings.append(f"pyproject.toml より優先される設定ファイルがある: {name}（設定は pyproject.toml に移す）")

    changed = _git_paths("diff", "--name-only", "--relative", "HEAD") or []
    changed += _git_paths("ls-files", "--others", "--exclude-standard") or []
    for path in sorted(set(changed)):
        file = ROOT / path
        if not path.endswith(".py") or not file.is_file():
            continue
        old = _git("show", f"HEAD:./{path}")  # HEAD にないファイルは None
        for s in grown_escape_hatches(old, file.read_text(encoding="utf-8", errors="replace")):
            findings.append(f"検査を黙らせる書き方が増えた: {path}: {s}")

    # ファイルごとに比べる（全体で比べると、別のファイルに足したテストで消したテストが隠れる）
    for path in _git_paths("ls-tree", "-r", "--name-only", "HEAD", "--", *testpaths(new or {})) or []:
        if not path.endswith(".py"):
            continue
        file = ROOT / path
        before = count_tests([_git("show", f"HEAD:./{path}") or ""])
        after = count_tests([file.read_text(encoding="utf-8", errors="replace")]) if file.is_file() else 0
        if after < before:
            findings.append(f"テスト関数が減った: {path}: {before} 件から {after} 件に")
    return findings


# --------------------------------------------------------------------------- #
# フックの出力
# --------------------------------------------------------------------------- #
def _principles(doc: str) -> str:
    return f"直し方の原則（詳しくは {doc}）:\n{PRINCIPLES}"


def failure_report(failures: list[Failure], findings: list[str], doc: str = DEFAULT_DOC) -> str:
    parts = ["品質検査に落ちました。直してから終えてください。"]
    parts += [f"== {f.check} ==\n{f.detail}" for f in failures]
    if findings:
        parts.append("== 見直してほしい変更 ==\n" + "\n".join(f"- {s}" for s in findings))
    parts.append(_principles(doc))
    return "\n\n".join(parts)


def review_report(findings: list[str], doc: str = DEFAULT_DOC) -> str:
    return "\n\n".join(
        [
            "品質検査は通りましたが、検査を緩める変更が見つかりました。品質のために必要な変更か見直してください。",
            "\n".join(f"- {s}" for s in findings),
            "品質のために必要なら残してよい。その場合は、理由を最後の報告でユーザーに伝える"
            "（基準を緩める変更は、ユーザーの了承を得る）。数字を通すためだけの変更なら取り消し、テストかコードで直す。",
            _principles(doc),
        ]
    )


def decide(
    failures: list[Failure],
    findings: list[str],
    *,
    retrying: bool,
    reported: set[str],
    doc: str = DEFAULT_DOC,
) -> tuple[dict[str, Any], set[str]]:
    """フックの出力を決める。返り値は (標準出力に書く JSON, 新たに報告済みにする指摘)。

    retrying は Stop フックの ``stop_hook_active``。一度止めた後にもう一度止まろうとしているときは
    止めない（止め続けるとループになる）。代わりに systemMessage でユーザーに直接知らせる。
    """
    new = [f for f in findings if f not in reported]
    names = "・".join(f.check for f in failures)
    if retrying:
        notes = []
        if failures:
            notes.append(f"品質検査が通っていません（{names}）")
        if new:
            notes.append(f"検査を緩める変更が {len(new)} 件あります")
        if not notes:
            return {}, set()
        return {"systemMessage": "⚠ " + "。".join(notes) + "。Claude の報告を確認してください"}, set(new)
    if failures:
        return {
            "decision": "block",
            "reason": failure_report(failures, findings, doc),
            "systemMessage": f"品質検査に落ちたので、Claude に直させています（{names}）",
        }, set(findings)
    if new:
        return {
            "decision": "block",
            "reason": review_report(findings, doc),
            "systemMessage": f"検査を緩める変更が {len(new)} 件あるので、Claude に見直させています",
        }, set(new)
    return {}, set()


def _state_file(session_id: str) -> Path:
    key = hashlib.sha256(f"{ROOT}\0{session_id}".encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "quality-gate" / f"{key}.json"


def _load_reported(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="品質検査（ruff・pytest・カバレッジ）")
    parser.add_argument("--ci", action="store_true", help="結果を表示し、落ちたら終了コード 1 を返す")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    doc = doc_path(read_pyproject())

    if args.ci:
        failures = run_checks()
        print(failure_report(failures, [], doc) if failures else "品質検査: ruff・pytest・カバレッジがすべて通った")
        return 1 if failures else 0

    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook = {}
    state = _state_file(str(hook.get("session_id", "")))
    reported = _load_reported(state)
    output, newly = decide(
        run_checks(), review_changes(), retrying=bool(hook.get("stop_hook_active")), reported=reported, doc=doc
    )
    if newly:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(sorted(reported | newly), ensure_ascii=False), encoding="utf-8")
    if output:
        print(json.dumps(output))  # ensure_ascii の既定のまま。端末の文字コードに左右されない
    return 0


if __name__ == "__main__":
    sys.exit(main())
