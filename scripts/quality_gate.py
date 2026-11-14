"""品質検査（ruff・pytest・カバレッジ）を回し、検査を緩める変更を見つける。

呼び出し元は2つある。

- Claude Code の Stop フック（.claude/settings.json）: 標準入力でフックの JSON を受け取る。
  検査に落ちたら ``{"decision": "block"}`` を返し、Claude に直させてから終わらせる
- CI（.github/workflows/test.yml の quality ジョブ）: ``--ci`` を付ける。落ちたら終了コード 1

基準の値は pyproject.toml の [tool.ruff.lint] と [tool.coverage] にある。
考え方は docs/design-docs/quality-gates.md（数字を通すために品質を落とさない）。
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
CHECKED_DIRS = ["src", "tests", "scripts"]
#: pyproject.toml より優先される設定ファイル。置くと pyproject.toml の基準を黙って上書きできる
OVERRIDING_CONFIGS = [".coveragerc", "setup.cfg", "tox.ini", "pytest.ini", "ruff.toml", ".ruff.toml"]
MAX_DETAIL_LINES = 60

PRINCIPLES = """\
直し方の原則（docs/design-docs/quality-gates.md）:
- 検査は品質を上げるための手段。数字を通すことを目的にしない
- テストには振る舞いを確かめる assert を書く。行を実行するだけのテストは書かない
- 基準（fail_under・ruff のルール・カバレッジの除外）を緩めない。緩める必要があるなら、理由を説明してユーザーの了承を得る
- noqa・pragma: no cover・skip で黙らせない。品質上の理由があるときだけ、理由を同じ行に書いて使い、報告で伝える
- 品質を落とさずには直せないときは、無理に通さない。止まって、状況と選択肢をユーザーに報告する"""


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
    """ruff → pytest（カバレッジを測りながら）→ カバレッジの基準、の順に回す。"""
    py = sys.executable
    failures: list[Failure] = []

    r = _run([py, "-m", "ruff", "check", *CHECKED_DIRS, "--output-format", "concise"])
    if r.returncode != 0:
        failures.append(Failure("ruff", _tail(r.stdout + r.stderr)))

    # 利用者の .coverage と pytest のキャッシュを上書きしない
    data_file = Path(tempfile.gettempdir()) / f"switchllm-quality-gate-{os.getpid()}.coverage"
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


def _lookup(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):  # per-file-ignores: {パターン: [コード]}
        return {f"{k}: {c}" for k, codes in value.items() for c in ([codes] if isinstance(codes, str) else codes)}
    return {str(v) for v in value}


#: 項目が減ると検査が緩む設定
_SHRINK_IS_WEAKER = [
    "tool.ruff.lint.select",
    "tool.ruff.lint.extend-select",
    "tool.coverage.run.source_pkgs",
    "tool.pytest.ini_options.testpaths",
]
#: 項目が増えると検査が緩む設定
_GROW_IS_WEAKER = [
    "tool.ruff.exclude",
    "tool.ruff.extend-exclude",
    "tool.ruff.lint.ignore",
    "tool.ruff.lint.extend-ignore",
    "tool.ruff.lint.per-file-ignores",
    "tool.ruff.lint.extend-per-file-ignores",
    "tool.coverage.run.omit",
    "tool.coverage.report.omit",
    "tool.coverage.report.exclude_lines",
    "tool.coverage.report.exclude_also",
    "tool.coverage.report.partial_branches",
]
_FAIL_UNDER = "tool.coverage.report.fail_under"
_BRANCH = "tool.coverage.run.branch"
_ADDOPTS = "tool.pytest.ini_options.addopts"


def weakened_settings(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """pyproject.toml の検査の基準のうち、old から new で緩んだものを返す。厳しくなった変更は返さない。"""
    out: list[str] = []
    before, after = _lookup(old, _FAIL_UNDER), _lookup(new, _FAIL_UNDER)
    if before is not None and (after is None or after < before):
        out.append(f"{_FAIL_UNDER}: {before} → {after}")
    if _lookup(old, _BRANCH) and not _lookup(new, _BRANCH):
        out.append(f"{_BRANCH}: true → {_lookup(new, _BRANCH)}")
    for key in _SHRINK_IS_WEAKER:
        removed = _as_set(_lookup(old, key)) - _as_set(_lookup(new, key))
        if removed:
            out.append(f"{key} から削除: {', '.join(sorted(removed))}")
    for key in _GROW_IS_WEAKER:
        added = _as_set(_lookup(new, key)) - _as_set(_lookup(old, key))
        if added:
            out.append(f"{key} に追加: {', '.join(sorted(added))}")
    if _lookup(old, _ADDOPTS) != _lookup(new, _ADDOPTS):
        out.append(f"{_ADDOPTS}: {_lookup(old, _ADDOPTS)!r} → {_lookup(new, _ADDOPTS)!r}")
    return out


def count_tests(sources: Iterable[str]) -> int:
    return sum(len(_TEST_DEF.findall(s)) for s in sources)


def _git(*args: str) -> str | None:
    r = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return r.stdout if r.returncode == 0 else None


def review_changes() -> list[str]:
    """HEAD（最後のコミット）からの変更のうち、検査を緩めるものを返す。"""
    findings: list[str] = []

    old_text = _git("show", "HEAD:pyproject.toml")
    if old_text is not None:
        try:
            new = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
            findings += [f"基準が緩んだ: {s}" for s in weakened_settings(tomllib.loads(old_text), new)]
        except tomllib.TOMLDecodeError as e:
            findings.append(f"pyproject.toml を読めない: {e}")

    findings += [
        f"pyproject.toml より優先される設定ファイルがある: {name}"
        for name in OVERRIDING_CONFIGS
        if (ROOT / name).exists()
    ]

    changed = (_git("diff", "HEAD", "--name-only", "--", *CHECKED_DIRS) or "").splitlines()
    changed += (_git("ls-files", "--others", "--exclude-standard", "--", *CHECKED_DIRS) or "").splitlines()
    for path in sorted(set(changed)):
        file = ROOT / path
        if not path.endswith(".py") or not file.exists():
            continue
        old = _git("show", f"HEAD:{path}")  # HEAD にないファイルは None
        for s in grown_escape_hatches(old, file.read_text(encoding="utf-8")):
            findings.append(f"検査を黙らせる書き方が増えた: {path}: {s}")

    # ファイルごとに比べる（全体で比べると、別のファイルに足したテストで消したテストが隠れる）
    for path in (_git("ls-tree", "-r", "--name-only", "HEAD", "--", "tests") or "").splitlines():
        if not path.endswith(".py"):
            continue
        file = ROOT / path
        before = count_tests([_git("show", f"HEAD:{path}") or ""])
        after = count_tests([file.read_text(encoding="utf-8")]) if file.exists() else 0
        if after < before:
            findings.append(f"テスト関数が減った: {path}: {before} 件から {after} 件に")
    return findings


# --------------------------------------------------------------------------- #
# フックの出力
# --------------------------------------------------------------------------- #
def failure_report(failures: list[Failure], findings: list[str]) -> str:
    parts = ["品質検査に落ちました。直してから終えてください。"]
    parts += [f"== {f.check} ==\n{f.detail}" for f in failures]
    if findings:
        parts.append("== 見直してほしい変更 ==\n" + "\n".join(f"- {s}" for s in findings))
    parts.append(PRINCIPLES)
    return "\n\n".join(parts)


def review_report(findings: list[str]) -> str:
    return "\n\n".join(
        [
            "品質検査は通りましたが、検査を緩める変更が見つかりました。品質のために必要な変更か見直してください。",
            "\n".join(f"- {s}" for s in findings),
            "品質のために必要なら残してよい。その場合は、理由を最後の報告でユーザーに伝える"
            "（基準を緩める変更は、ユーザーの了承を得る）。数字を通すためだけの変更なら取り消し、テストかコードで直す。",
            PRINCIPLES,
        ]
    )


def decide(
    failures: list[Failure], findings: list[str], *, retrying: bool, reported: set[str]
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
            "reason": failure_report(failures, findings),
            "systemMessage": f"品質検査に落ちたので、Claude に直させています（{names}）",
        }, set(findings)
    if new:
        return {
            "decision": "block",
            "reason": review_report(findings),
            "systemMessage": f"検査を緩める変更が {len(new)} 件あるので、Claude に見直させています",
        }, set(new)
    return {}, set()


def _state_file(session_id: str) -> Path:
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "switchllm-quality-gate" / f"{key}.json"


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

    if args.ci:
        failures = run_checks()
        print(failure_report(failures, []) if failures else "品質検査: ruff・pytest・カバレッジがすべて通った")
        return 1 if failures else 0

    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook = {}
    state = _state_file(str(hook.get("session_id", "")))
    reported = _load_reported(state)
    output, newly = decide(
        run_checks(), review_changes(), retrying=bool(hook.get("stop_hook_active")), reported=reported
    )
    if newly:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(sorted(reported | newly), ensure_ascii=False), encoding="utf-8")
    if output:
        print(json.dumps(output))  # ensure_ascii の既定のまま。端末の文字コードに左右されない
    return 0


if __name__ == "__main__":
    sys.exit(main())
