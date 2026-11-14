# quality-gate: 数字のために品質を落とさない品質検査

Python のプロジェクトに入れるハーネスのテンプレート。Claude Code が作業を終える前に ruff・pytest・カバレッジを検査し、落ちたら直させる。

検査の数字を通すために品質を落とす変更も見張る。基準を緩める、`# noqa` で黙らせる、テストを消す、などを見つけたら一度止めて見直させる。考え方は [files/docs/quality-gates.md](files/docs/quality-gates.md) にある。

## 入っているもの

`files/` はプロジェクトの同じパスにそのまま置く。`snippets/` は既存のファイルに足す。

| テンプレート | 置き先 | 扱い |
|---|---|---|
| [files/scripts/quality_gate.py](files/scripts/quality_gate.py) | `scripts/quality_gate.py` | そのまま置く。書き換えない |
| [files/tests/test_quality_gate.py](files/tests/test_quality_gate.py) | `tests/test_quality_gate.py` | そのまま置く。書き換えない |
| [files/docs/quality-gates.md](files/docs/quality-gates.md) | `docs/quality-gates.md` | そのまま置く。別の場所に置くなら `[tool.quality-gate]` の `doc` に書く |
| [files/.github/workflows/quality.yml](files/.github/workflows/quality.yml) | `.github/workflows/quality.yml` | そのまま置く。開発用の依存の入れ方が違えば `pip install` の行を直す |
| [snippets/settings.json](snippets/settings.json) | `.claude/settings.json` | `hooks.Stop` を足す。ほかの設定はそのまま残す |
| [snippets/pyproject-snippet.toml](snippets/pyproject-snippet.toml) | `pyproject.toml` | 節ごとに足す。パッケージ名と `fail_under` はプロジェクトに合わせる（ファイル名を `pyproject.toml` にしないのは、ruff などがテンプレートの中で設定ファイルとして読むのを避けるため） |
| [snippets/CLAUDE-section.md](snippets/CLAUDE-section.md) | `CLAUDE.md` | 節をそのまま足す |

## 前提

- Python 3.10 以上、git で管理している
- プロジェクトのルートに仮想環境（`.venv` か `venv`）がある。フックはその Python で検査を回す
- pytest のテストがある（テストが1つもないと pytest が失敗する）
- Windows では Git Bash が入っている（Claude Code のフックは bash で動く）

## 導入の手順

1. `files/` の中身を、プロジェクトのルートに同じパスでコピーする
2. `snippets/settings.json` の `hooks.Stop` を、プロジェクトの `.claude/settings.json` に足す
3. `snippets/pyproject-snippet.toml` を `pyproject.toml` に足す。`source_pkgs` に自分のパッケージ名を入れる（パッケージにしていないなら `source = ["."]` にして、テストを `omit` で外す）
4. `snippets/CLAUDE-section.md` を `CLAUDE.md` に足す
5. 開発用の依存を入れる: `pip install -e ".[dev]"`
6. 検査を回して直す: `python scripts/quality_gate.py --ci`。ruff の違反とテストの失敗を直し、カバレッジの値を見る
7. 測ったカバレッジの小数を切り捨てて、`fail_under` に入れる（87.68% なら 87）
8. コミットする。フックは最後のコミットとの差分で緩みを見つけるので、導入した状態を基準にする
9. Claude Code を開き直すか `/hooks` を開いて、Stop にフックが出ていることを確かめる

## プロジェクトごとに変えてよいもの

変えてよいのは `pyproject.toml` の設定だけ。`files/` のスクリプトとテストは書き換えない。

| 変えたいこと | 書く場所 |
|---|---|
| ruff のルール、除外するファイル | `[tool.ruff]`、`[tool.ruff.lint]` |
| テストの場所 | pytest の `testpaths` |
| カバレッジを測る範囲と基準 | `[tool.coverage.run]`、`[tool.coverage.report]` |
| 方針の文書の場所 | `[tool.quality-gate]` の `doc`（既定は `docs/quality-gates.md`） |

検査の設定は `pyproject.toml` にまとめる。`.coveragerc`・`pytest.ini`・`ruff.toml` などに書くと、フックが「優先される設定ファイルがある」と見直しを求める。緩みを見張れるのが `pyproject.toml` だけだからである。

## テンプレートを直すとき

検査を足したい、検出を直したいときは、テンプレートの `files/` を直し、使っている各プロジェクトに上書きコピーする。プロジェクトの側で直すと、テンプレートとずれて配り直せなくなる。

直したら、テンプレートを使っているプロジェクトのどれかで `python scripts/quality_gate.py --ci` と `python -m pytest` が通ることを確かめる。
