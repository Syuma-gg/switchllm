"""switchllm の品質検査が、ハーネスのテンプレート（harness/quality-gate/）と同じであることの検査。

switchllm はテンプレートの最初の利用者で、ここで検査が回り続けることがテンプレートの動作確認になる
（docs/design-docs/quality-gate-harness.md）。失敗メッセージには直し方を書く。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "harness" / "quality-gate"
#: テンプレートの files/ のうち、switchllm に同じパスで置くもの（CI は test.yml の quality ジョブで回すので除く）
SHARED = ["scripts/quality_gate.py", "tests/test_quality_gate.py", "docs/quality-gates.md"]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")  # 改行コードの違い（CRLF / LF）は読むときにそろう


def test_shared_files_match_the_template():
    differ = [p for p in SHARED if _text(ROOT / p) != _text(TEMPLATE / "files" / p)]
    assert not differ, (
        "テンプレート（harness/quality-gate/files/）と中身が違う。プロジェクトごとの違いは pyproject.toml に書き、"
        "これらのファイルは書き換えない。検査そのものを直すなら、両方を同じ変更で直す:\n" + "\n".join(differ)
    )


def test_stop_hook_matches_the_template():
    ours = json.loads(_text(ROOT / ".claude" / "settings.json"))["hooks"]["Stop"]
    theirs = json.loads(_text(TEMPLATE / "snippets" / "settings.json"))["hooks"]["Stop"]
    assert ours == theirs, (
        ".claude/settings.json の Stop フックが、テンプレート（harness/quality-gate/snippets/settings.json）と違う。"
        "両方を同じ変更で直す"
    )


def test_claude_md_has_the_template_section():
    section = _text(TEMPLATE / "snippets" / "CLAUDE-section.md").strip()
    assert section in _text(ROOT / "CLAUDE.md"), (
        "CLAUDE.md の「品質検査」の節が、テンプレート（harness/quality-gate/snippets/CLAUDE-section.md）と違う。"
        "両方を同じ変更で直す"
    )
