# docs 索引

このディレクトリがプロジェクトの知識の正本。会話で決めたことも、ここに書くまでは残らない。

構成は OpenAI の [Harness engineering](https://openai.com/ja-JP/index/harness-engineering/) で紹介された構造化 docs に倣っている。入口の `CLAUDE.md` は短い地図にとどめ、詳しい情報は必要になったときにリンクを辿って読む（段階的開示）。

| 文書 | 内容 |
|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | 処理の流れ、モジュールと層、テストの配置、よくある変更 |
| [design-docs/index.md](design-docs/index.md) | 設計判断の一覧と検証状況。基本方針は [core-beliefs.md](design-docs/core-beliefs.md) |
| [product-specs/index.md](product-specs/index.md) | 何を作るか（位置づけ、スコープ、公開 API） |
| [references/index.md](references/index.md) | 各社 API の仕様メモと出典 |
| [PLANS.md](PLANS.md) | 実行計画の書き方と一覧（進行中 / 完了） |
| [exec-plans/tech-debt-tracker.md](exec-plans/tech-debt-tracker.md) | 既知の負債 |
| [QUALITY_SCORE.md](QUALITY_SCORE.md) | 領域ごとの品質評価 |
| [testing.md](testing.md) | テストの方針（テスト駆動の手順、テストの3つの層、実 API の記録と再生） |
| [e2e-tests.md](e2e-tests.md) | 実 API に対する E2E テストの回し方と、テストケース（境界値を含む）の一覧 |
| [quality-gates.md](quality-gates.md) | 品質検査の方針（原則、仕組み、落ちたときの直し方）。テンプレートの写しで、switchllm 用には書き換えない |
| [RELIABILITY.md](RELIABILITY.md) | タイムアウト、リトライ、ストリーミングが失敗したときの扱い |
| [SECURITY.md](SECURITY.md) | APIキーの扱い、ツール実行の注意、脆弱性の報告窓口 |

## 文書の種類と更新のきっかけ

同じ事実は、正本の1か所にだけ書く。ほかの文書は、書き写さずにリンクする（2026-11-27 に決めた。[文書の整理](exec-plans/completed/2026-11-27-docs-refactor.md)）。

| 種類 | 置き場所 | 何の正本か | いつ更新するか |
|---|---|---|---|
| 仕様メモ | `references/` | 各社 API の形式と出典 | 各社 API の形式を一次情報で確かめたとき |
| 設計判断 | `design-docs/` | なぜそうしたか | 方針を決めた・変えたとき。古い判断は消さず、状態を「廃止」にして新しい判断へリンクする |
| 製品仕様 | `product-specs/` | 公開 API が何をするか（`public-api.md`）、やること・やらないこと（`scope.md`） | 公開 API、スコープ、プリセットが変わったとき |
| 実行計画 | `exec-plans/` | 作業の手順と経緯。実 API で確かめていない前提と結果は [実 API 検証](exec-plans/active/real-api-verification.md) の前提の一覧 | 複数ステップの作業を始める・進める・終えるとき |
| 品質・信頼性・セキュリティ | このディレクトリ直下 | テストと検証の状態（`QUALITY_SCORE.md`）、E2E のケース（`e2e-tests.md`）、失敗したときの挙動（`RELIABILITY.md`） | テストや検証の状況、失敗時の挙動が変わったとき |
| 変更履歴 | `CHANGELOG.md` | 利用者向けの変更 | 利用者に見える変更をしたとき |

書き方の決まり:

- ほかの文書に関わることは、U 番号や見出しの名前でリンクする（例: 「[実 API 検証](exec-plans/active/real-api-verification.md) の U8」）
- 重ねて書いてよいのは、読む人が違う文書と、索引として要点を並べる表だけ。README（利用者向け）と、[references の横断の対応表](references/index.md) がこれに当たる。食い違ったら正本に合わせる
- 計画の進捗ログには、あとで効く気づきだけを書く。細かい経緯は git の履歴に任せ、どのコミットを見ればよいかを書き添える
- 長い段落や表のセルは、箇条書きや短い表に分ける

## 機械的な検査

`python -m pytest` に含まれる `tests/test_docs.py` が次を検査する。失敗メッセージに直し方が書いてある。

- 相対リンクの先が存在する
- `CLAUDE.md` からリンクを辿って到達できない文書がない
- 各 `index.md`（計画は `PLANS.md`）が、同じディレクトリの文書をすべて載せている
- `CLAUDE.md` が 100 行以内（地図を短く保つ）
- [public-api.md](product-specs/public-api.md) の一覧が `switchllm.__all__` と、プリセット表が `PRESETS` と、オプション表が各アダプターの `option_names` と一致する
- 2つの README（英語の `README.md` と日本語の `README.ja.md`）のリンクが、すべて GitHub の絶対 URL で、指すファイルがリポジトリにある（PyPI のページでは相対リンクとページ内リンクが切れるため）
- 2つの README の見出しの並び、コードブロックの数、リンク先がそろっている

これとは別に、`tests/test_quality_gate.py` が、`# noqa`・`# pragma: no cover` に理由が書いてあることを検査する。ruff・pytest・カバレッジをまとめて回すのは `scripts/quality_gate.py` で、Claude Code の Stop フックと CI から呼ぶ（[品質検査の方針](quality-gates.md)）。品質検査の仕組みは他のプロジェクトでも使うテンプレート（[harness/quality-gate/](../harness/quality-gate/README.md)）の写しで、`tests/test_harness.py` が一致を検査する。
