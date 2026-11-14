## 品質検査

作業を終える前に、Stop フック（`.claude/settings.json`）が ruff・pytest・カバレッジを検査する。落ちたら直してから終える。直し方は [品質検査の方針](docs/quality-gates.md) に従う。

- 検査は品質を上げるための手段。数字を通すために品質を落とさない
- テストには振る舞いを確かめる assert を書く。基準（`pyproject.toml` の `fail_under`・ruff のルール・除外）を緩めない
- `# noqa`・`# pragma: no cover`・skip は品質上の理由があるときだけ使い、同じ行に理由を書き、報告で伝える
- 品質を落とさずに直せないときは、止まって状況と選択肢をユーザーに報告する
