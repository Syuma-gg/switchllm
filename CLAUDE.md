# switchllm — Claude Code 向けの地図

OpenAI / Anthropic / Gemini / OpenAI互換サーバーを同じコードで呼べる、軽量な Python ライブラリ。
`Client("provider:model")` の文字列（または環境変数 `SWITCHLLM_MODEL`）だけでプロバイダーを切り替える。

このファイルは**地図**なので詳細は書かない。知識の正本は `docs/` にある。
作業の前に、下の「どこを読むか」から該当する文書だけを開くこと。

## コマンド（Windows / PowerShell）

```powershell
pip install -e ".[dev]"
python -m pytest                     # 全テスト（ネットワーク不要・数秒）。docs と層構造の検査、実 API の記録の再生を含む
python -m pytest tests/test_gemini.py -q
python -m pytest e2e --models "openai:gpt-5.5" --collect-only -q  # E2E（実 API）が呼ぶ回数を見るだけ
python -m pytest e2e --models "openai:gpt-5.5" --record          # 実 API で回して記録する（料金が発生する）
ruff check                           # プロジェクト全体。規則は pyproject.toml
python scripts/quality_gate.py --ci  # ruff・pytest・カバレッジ（Stop フックと CI と同じ検査）
```

- CI（`.github/workflows/test.yml`）は同じ `python -m pytest` を Ubuntu の Python 3.10〜3.15 と Windows の 3.10・3.15 で回す。対応する Python の版を変えたら、ここと `pyproject.toml` の `requires-python`・`classifiers` を合わせる
- 公開は `.github/workflows/publish.yml`（手動で実行すると TestPyPI、GitHub の Release を公開すると PyPI）。設定と手順は [v0.1 公開](docs/exec-plans/active/v0.1-release.md)
- **`--models` を付けた `e2e/`（E2E テスト）は実 API を叩き、料金が発生する。実行前に必ずユーザーに確認すること。** 付けなければ記録を再生するだけで、ネットワークを使わない（[E2E テスト](docs/e2e-tests.md)）
- APIキーは環境変数で渡す。コードやファイルに書かない（[docs/SECURITY.md](docs/SECURITY.md)）。

## 品質検査

作業を終える前に、Stop フック（`.claude/settings.json`）が ruff・pytest・カバレッジを検査する。落ちたら直してから終える。直し方は [品質検査の方針](docs/quality-gates.md) に従う。

- 検査は品質を上げるための手段。数字を通すために品質を落とさない
- テストには振る舞いを確かめる assert を書く。基準（`pyproject.toml` の `fail_under`・ruff のルール・除外）を緩めない
- `# noqa`・`# pragma: no cover`・skip は品質上の理由があるときだけ使い、同じ行に理由を書き、報告で伝える
- 品質を落とさずに直せないときは、止まって状況と選択肢をユーザーに報告する

## テスト駆動

振る舞いを変えるときは、先にテストを書き、変更前のコードで失敗することを確かめてから直す。報告では、変更前に失敗したことを伝える。記録の再生が失敗したら、記録を手で直さず、実 API で記録し直すか変更を直す（[テストの方針](docs/testing.md)）。

## どこを読むか

| やりたいこと | 読む文書 |
|---|---|
| 全体の構造、どのファイルが何をするか、よくある変更の手順 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| docs 全体の索引 | [docs/index.md](docs/index.md) |
| なぜこの設計なのか | [docs/design-docs/index.md](docs/design-docs/index.md)（基本方針は [core-beliefs.md](docs/design-docs/core-beliefs.md)） |
| 機能を足してよいか（やること / やらないこと） | [docs/product-specs/scope.md](docs/product-specs/scope.md) |
| 公開 API、正規化した値の定義、プリセット | [docs/product-specs/public-api.md](docs/product-specs/public-api.md) |
| 各社 API の形式と出典 | [docs/references/index.md](docs/references/index.md) |
| 進行中の作業、次にやること | [docs/PLANS.md](docs/PLANS.md) |
| 既知の負債 | [docs/exec-plans/tech-debt-tracker.md](docs/exec-plans/tech-debt-tracker.md) |
| テストと検証の状況 | [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md) |
| テスト駆動の手順、テストの層、記録と再生 | [docs/testing.md](docs/testing.md) |
| 実 API に対する E2E テスト（回し方、テストケース） | [docs/e2e-tests.md](docs/e2e-tests.md) |
| 検査に落ちたときの直し方、基準を変えてよいか | [docs/quality-gates.md](docs/quality-gates.md) |
| 品質検査のテンプレート（他のプロジェクトに入れる、検査そのものを直す） | [harness/quality-gate/README.md](harness/quality-gate/README.md) |
| リトライ、タイムアウト、例外 | [docs/RELIABILITY.md](docs/RELIABILITY.md) |

## 壊してはいけない不変条件

ここには規則だけを書く。理由はリンク先にある。

1. **生データ再送**: 推論情報を持つアダプター（openai / anthropic / gemini）の `parse_response` は、assistant メッセージに必ず `ProviderState(self.key, self.model, raw)` を付ける。`build_request` は assistant メッセージでまず `self.native_raw(m)` を見て、あれば無加工で送る。`openai_chat` は `reasoning_content` を返したときだけ付け、送るときはその `reasoning_content` だけを assistant メッセージに足す。再送は同じプロバイダーキーかつ同じモデルのときだけ。例外は `anthropic`（`resend_across_models = True`）で、同じプロバイダーキーならモデルが違っても送る（[理由](docs/design-docs/provider-state-resend.md)）
2. **`parse_stream` は最後に `done` を1回だけ yield** し、非ストリーミングと同じ Response にする。非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用する
3. **未知の SSE イベントは無視する**（例外にしない）。プロバイダーは新しいイベントを追加する
4. **共通パラメータを増やしたら4アダプターすべてで対応する**か、`UnsupportedFeatureError` を明示的に投げる。黙って無視しない。増やす前に [scope.md](docs/product-specs/scope.md) を確認する
5. **正規化した値の意味を変えない**（`stop_reason` / `Usage` / `ToolCall.arguments`。定義は [public-api.md](docs/product-specs/public-api.md)）
6. **テストはネットワークを使わない**。`tests/conftest.py` の `FakeAPI` で応答を積み、送られたリクエスト本体を検証する。実 API を呼ぶテストは `e2e/` にだけ置き、`--models` を渡したときだけ呼ぶ
7. **API 仕様に関わる変更は一次情報（各社の公式ドキュメント）で確認してから行う**。確認したら `docs/references/` の該当文書に出典を足し、[実 API 検証](docs/exec-plans/active/real-api-verification.md) の表を更新する

## コード規約

- Python 3.10 以上。各ファイル先頭で `from __future__ import annotations`
- コメント・docstring・ドキュメントは日本語。例外メッセージは英語。例外は `README.md`（英語。PyPI に載る）で、日本語版は `README.ja.md`
- ランタイム依存は `httpx` だけ（`tests/test_architecture.py` が検査）。増やすなら先に design-docs に判断を残す
- 公開 API を増減したら `src/switchllm/__init__.py` の `__all__` と [public-api.md](docs/product-specs/public-api.md) を同じ変更で直す（`tests/test_docs.py` が照合）

## ドキュメントの保守

- 新しい文書は、そのディレクトリの `index.md`（計画は [docs/PLANS.md](docs/PLANS.md)）からリンクする。どこからも辿れない文書があると `tests/test_docs.py` が失敗する
- コードの挙動を変えたら、同じ変更で関係する文書も直す。古い記述を見つけたらその場で直すか、tech-debt-tracker に書く
- README は英語版と日本語版を同じ変更で直す。リンクは `https://github.com/Syuma-gg/switchllm/blob/main/...` の絶対 URL にする（`tests/test_docs.py` が検査）
- 複数ステップの作業は `docs/exec-plans/active/` に計画を置き、終わったら `completed/` に移す
- 会話で決めたことは、docs に書くまで残らないものとして扱う
