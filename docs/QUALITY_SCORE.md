# 品質評価

領域ごとの状態。テストや検証の状況が変わったら更新する（最終更新: 2026-11-18）。

テストは GitHub Actions（`.github/workflows/test.yml`）で、Ubuntu の Python 3.10〜3.15 と Windows の 3.10・3.15 で回す（2026-11-14 に、全ジョブが通ることを確認した）。

カバレッジ（分岐を含む）は 98.75%（2026-11-18、Python 3.12）。基準は `pyproject.toml` の `fail_under = 98` で、上がったら引き上げる（[品質検査の方針](quality-gates.md)）。行はすべて到達している。残りは分岐の一部で、多くはパーツの型で分ける `isinstance` の連鎖の、どれにも当たらない側（送る前の検査で `TypeError` にするので、今は通らない）。

実 API に対する E2E テストは `e2e/` にあり、`--models` を渡したときだけ実 API を呼ぶ（料金が発生するため。[E2E テスト](e2e-tests.md)）。渡さなければ、実 API で記録したやりとりを再生し、既定の実行と CI にも入る（[テストの方針](testing.md)）。まだ実 API では回しておらず、記録は 0 件。

## 評価の基準

| 評価 | 意味 |
|---|---|
| A | モックのテストが揃っていて、実 API でも確認済み |
| B | モックのテスト（リクエスト形状 / ツール往復 / ストリーミング / エラー）は揃っているが、実 API では未確認 |
| C | テストが一部だけ |
| D | テストがない |

## アダプター

| アダプター | 評価 | モックのテスト | 実 API | 足りないテスト | 主な未検証の前提 |
|---|---|---|---|---|---|
| openai（Responses） | B | `tests/test_openai_responses.py` | 未確認 | — | U1〜U3 |
| openai_chat | B | `tests/test_openai_chat.py` | 未確認 | — | U10、U11、U15〜U18 |
| anthropic | B | `tests/test_anthropic.py` | 未確認 | — | U8、U9 |
| gemini | B | `tests/test_gemini.py` | 未確認 | — | U4〜U7、U16 |

## 共通部分

| 領域 | 評価 | テスト | メモ |
|---|---|---|---|
| Client、モデル文字列、APIキー、オプション | B | `tests/test_client.py` | 知らないオプション名・`tool_choice` の誤り・読めないパーツを、送る前に例外にする |
| リトライ、例外 | B | `tests/test_http.py` | 接続エラー、`Retry-After`、エラー本体の解釈、ストリームの途中の切断。実際の 429 / 5xx / 切断での挙動は未確認 |
| プロバイダーの切り替え | B | `tests/test_cross_provider.py`、`tests/test_consistency.py`、`tests/test_client.py` | 履歴の変換、4アダプターで同じ振る舞いになること、`with_model()`、`extra_by_provider`。U5、U8、U16、U17 |
| 共通型 | B | `tests/test_types.py` | `Message.from_dict`、`ToolCall.arguments_json`、`ImagePart` |
| 履歴の保存・復元 | B | `tests/test_history.py` | `to_dict` / `from_dict` の往復、4アダプターで復元した履歴から同じリクエストを送れること。実 API では未確認（E2E の `test_restored_history_continues`） |
| `@tool`、`run_tool_calls` | B | `tests/test_tools.py` | 生成したスキーマを各社が受け付けるかは実 API で確認する |
| 画像入力（`ImagePart`） | B | 各 `tests/test_<adapter>.py` | 4アダプターのリクエスト形状、Gemini の URL 拒否、`from_file` の形式判定。実 API では未確認 |
| SSE パース | B | `tests/test_client.py` | |
| 実 API の記録と再生 | B | `tests/test_recording.py` | 秘密を残さない（ヘッダー、URL の `key`、キーの値）、記録と違うリクエストで失敗する、gzip の応答、仮 ID の扱い。実 API の記録はまだない |
| 層構造、ランタイム依存 | — | `tests/test_architecture.py` | 検査そのもの |
| ドキュメント | — | `tests/test_docs.py` | リンク・索引・公開 API・プリセット・オプションの一致を検査 |

実 API での確認は [実 API 検証](exec-plans/active/real-api-verification.md) で進める。
